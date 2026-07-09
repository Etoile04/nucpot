"""V4 extraction API endpoints (NFM-558).

Provides the dedicated /api/v4/ namespace for the extraction lifecycle:
- POST /api/v4/extraction/submit        — Submit extraction job
- GET  /api/v4/extraction/{job_id}/status — Poll job progress
- GET  /api/v4/extraction/{job_id}/result — Retrieve extraction results
- GET  /api/v4/properties/{material_system} — Browse extracted properties
- POST /api/v4/extraction/{job_id}/validate — Trigger validation workflow
- GET  /api/v4/material-systems          — List material systems
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.extraction import (
    V4BrowseResponse,
    V4ConfidenceSummary,
    V4ExtractionSubmitRequest,
    V4JobProgress,
    V4MaterialSystemSummary,
    V4PropertyResponse,
    V4ResultResponse,
    V4StatusResponse,
    V4SubmitResponse,
    V4ValidateRequest,
    V4ValidateResponse,
)
from nfm_db.services.extraction_pipeline import (
    JobStatus,
    get_job,
    trigger_extraction,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Valid enum values
# ---------------------------------------------------------------------------

VALID_SOURCE_TYPES = {"doi", "url", "file", "internal_id"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_STAGING_STATUS = {"pending", "approved", "rejected", "promoted"}
VALID_SORT_FIELDS = {"property", "temperature", "confidence", "created_at"}
VALID_SORT_ORDERS = {"asc", "desc"}

# DOI format regex per CTO ADR (NFM-632)
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")

# Ordered job lifecycle steps for progress tracking
_ORDERED_STEPS = [
    "queued",
    "running",
    "extracting",
    "mapping",
    "quality_gate",
    "completed",
]


# ---------------------------------------------------------------------------
# Helper: error envelope
# ---------------------------------------------------------------------------


def _error_response(status_code: int, message: str) -> JSONResponse:
    """Return a standard ApiResponse error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": message, "meta": None},
    )


# ---------------------------------------------------------------------------
# Helper: build progress sub-object from JobStatus
# ---------------------------------------------------------------------------


def _build_progress(status: JobStatus) -> V4JobProgress:
    """Build the V4JobProgress from the current job status."""
    status_value = status.value
    current_idx = (
        _ORDERED_STEPS.index(status_value)
        if status_value in _ORDERED_STEPS
        else 0
    )
    return V4JobProgress(
        current_step=status_value,
        steps_completed=_ORDERED_STEPS[:current_idx],
        steps_remaining=_ORDERED_STEPS[current_idx + 1 :],
    )


# ---------------------------------------------------------------------------
# Helper: convert raw property dicts to V4PropertyResponse
# ---------------------------------------------------------------------------


def _to_v4_property(
    prop: dict[str, Any],
    *,
    job_id: str | None = None,
) -> V4PropertyResponse:
    """Convert a raw extraction property dict to V4PropertyResponse."""
    conditions = prop.get("conditions")
    if conditions is None:
        # Build conditions from flat temperature/pressure/method fields
        flat_keys = ("temperature", "pressure", "method")
        flat_vals = {
            k: str(v)
            for k, v in prop.items()
            if k in flat_keys and v is not None
        }
        conditions = flat_vals if flat_vals else None

    return V4PropertyResponse(
        material_name=prop.get("material_name") or prop.get("element_system"),
        composition=prop.get("composition"),
        phase=prop.get("phase"),
        element=prop.get("element"),
        property_category=prop.get("property_category"),
        property=prop.get("property") or prop.get("property_name", ""),
        value=str(prop.get("value", "")),
        unit=prop.get("unit", ""),
        conditions=conditions,
        context=prop.get("context"),
        confidence=prop.get("confidence", "medium"),
        reference=prop.get("reference") or prop.get("source"),
        source_file=prop.get("source_file") or prop.get("source"),
        job_id=job_id,
        staging_status=prop.get("staging_status"),
        cache_level=prop.get("cache_level"),
    )


# ---------------------------------------------------------------------------
# Helper: collect stored properties for a job
# ---------------------------------------------------------------------------


async def _get_job_properties(
    job_id: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Retrieve properties from ref_gap_fill_staging for a job.

    Looks up the ExtractionJob in _job_store to get fill_batch_id,
    then queries the staging table for matching records.
    Returns empty list if job not found or fill_batch_id is None.
    """
    from nfm_db.models.ref_gap_fill import RefGapFillStaging

    job = get_job(job_id)
    if job is None or job.fill_batch_id is None:
        return []

    batch_uuid = uuid.UUID(job.fill_batch_id)
    result = await session.execute(
        select(RefGapFillStaging).where(
            RefGapFillStaging.fill_batch_id == batch_uuid
        )
    )
    rows = result.scalars().all()

    return [
        {
            "element_system": row.element_system,
            "phase": row.phase,
            "property_name": row.property_name,
            "value": row.value,
            "unit": row.unit,
            "method": row.method,
            "source": row.source,
            "source_doi": row.source_doi,
            "uncertainty": row.uncertainty,
            "temperature": row.temperature,
            "source_file": row.source_file,
            "composition": row.composition,
            "element": row.element,
            "property_category": row.property_category,
            "context": row.context,
            "confidence": row.confidence.value,
            "staging_status": row.status.value,
            "cache_level": row.cache_level.value if row.cache_level else None,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Helper: build material systems index
# ---------------------------------------------------------------------------


def _build_material_systems_index() -> list[dict[str, Any]]:
    """Build material systems overview from available job data.

    TODO(NFM-561): Aggregate from extracted_properties table once the
    v4 pipeline writes to the database. Currently returns empty because
    the extraction pipeline operates in-memory.
    """
    return []


# ---------------------------------------------------------------------------
# 1. POST /api/v4/extraction/submit
# ---------------------------------------------------------------------------


@router.post(
    "/extraction/submit",
    status_code=202,
)
async def submit_extraction(
    payload: V4ExtractionSubmitRequest,
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """提交V4提取任务。

    Validates source_type, triggers the extraction pipeline, and returns
    a job_id for status polling.
    """
    if payload.source_type not in VALID_SOURCE_TYPES:
        return _error_response(
            400,
            f"Invalid source_type '{payload.source_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}",
        )

    if not payload.source_reference.strip():
        return _error_response(400, "source_reference must not be empty.")

    if payload.source_type == "doi" and not DOI_PATTERN.match(
        payload.source_reference.strip()
    ):
        return _error_response(
            400,
            "Invalid DOI format. DOIs must match pattern 10.NNNN/... "
            "(e.g., 10.1016/j.nucengdes.2020.110756)",
        )

    job = await trigger_extraction(
        session=session,
        source_reference=payload.source_reference,
        source_type=payload.source_type,
        element_systems=payload.element_systems,
        cache_level=payload.cache_level,
        max_confidence=payload.max_confidence,
    )

    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "data": V4SubmitResponse(
                job_id=job.job_id,
                source_reference=job.source_reference,
                source_type=job.source_type,
                status=job.status.value,
                message="Extraction job queued successfully."
                if job.status != JobStatus.FAILED
                else f"Extraction failed: {job.error_message}",
                error_message=job.error_message,
                created_at=job.created_at,
            ).model_dump(mode="json"),
        },
    )


# ---------------------------------------------------------------------------
# 2. GET /api/v4/extraction/{job_id}/status
# ---------------------------------------------------------------------------


@router.get("/extraction/{job_id}/status")
async def get_extraction_status(job_id: str) -> JSONResponse:
    """轮询提取任务进度（含详细步骤追踪）。"""
    job = get_job(job_id)

    if job is None:
        return _error_response(
            404,
            f"Extraction job '{job_id}' not found.",
        )

    return JSONResponse(
        content={
            "success": True,
            "data": V4StatusResponse(
                job_id=job.job_id,
                source_reference=job.source_reference,
                source_type=job.source_type,
                status=job.status.value,
                progress=_build_progress(job.status),
                extracted_count=job.extracted_count,
                staged_count=job.staged_count,
                rejected_count=job.rejected_count,
                error_message=job.error_message,
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
            ).model_dump(mode="json"),
        },
    )


# ---------------------------------------------------------------------------
# 3. GET /api/v4/extraction/{job_id}/result
# ---------------------------------------------------------------------------


@router.get("/extraction/{job_id}/result")
async def get_extraction_result(
    job_id: str,
    confidence: str | None = Query(default=None),
    property_category: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """获取已完成任务的提取结果，支持分页。"""
    job = get_job(job_id)

    if job is None:
        return _error_response(
            404,
            f"Extraction job '{job_id}' not found.",
        )

    if job.status != JobStatus.COMPLETED:
        return _error_response(
            409,
            f"Job '{job_id}' is '{job.status.value}', not 'completed'. "
            "Results are only available for completed jobs.",
        )

    if confidence is not None and confidence not in VALID_CONFIDENCE:
        return _error_response(
            400,
            f"Invalid confidence '{confidence}'. "
            f"Must be one of: {', '.join(sorted(VALID_CONFIDENCE))}",
        )

    raw_properties = await _get_job_properties(job_id, session)

    if confidence is not None:
        raw_properties = [
            p for p in raw_properties if p.get("confidence") == confidence
        ]

    if property_category is not None:
        raw_properties = [
            p
            for p in raw_properties
            if p.get("property_category") == property_category
        ]

    total = len(raw_properties)
    start = (page - 1) * limit
    page_properties = raw_properties[start : start + limit]

    properties = [
        _to_v4_property(p, job_id=job_id) for p in page_properties
    ]

    return JSONResponse(
        content={
            "success": True,
            "data": V4ResultResponse(
                source_reference=job.source_reference,
                job_status=job.status.value,
                total_extracted=job.extracted_count,
                properties=[p.model_dump(mode="json") for p in properties],
            ).model_dump(mode="json"),
            "meta": {
                "total": total,
                "page": page,
                "limit": limit,
                "confidence_filter": confidence,
                "category_filter": property_category,
            },
        },
    )


# ---------------------------------------------------------------------------
# 4. GET /api/v4/properties/{material_system}
# ---------------------------------------------------------------------------


@router.get("/properties/{material_system}")
async def browse_properties(
    material_system: str,
    property_category: str | None = Query(default=None),
    confidence: str | None = Query(default=None),
    phase: str | None = Query(default=None),
    temperature_min: float | None = Query(default=None),
    temperature_max: float | None = Query(default=None),
    staging_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    sort_by: str = Query(default="property"),
    sort_order: str = Query(default="asc"),
) -> JSONResponse:
    """浏览材料体系的提取属性数据，支持筛选。"""
    if sort_by not in VALID_SORT_FIELDS:
        return _error_response(
            400,
            f"Invalid sort_by '{sort_by}'. "
            f"Must be one of: {', '.join(sorted(VALID_SORT_FIELDS))}",
        )
    if sort_order not in VALID_SORT_ORDERS:
        return _error_response(
            400,
            f"Invalid sort_order '{sort_order}'. Must be 'asc' or 'desc'.",
        )

    if confidence is not None and confidence not in VALID_CONFIDENCE:
        return _error_response(
            400,
            f"Invalid confidence '{confidence}'. "
            f"Must be one of: {', '.join(sorted(VALID_CONFIDENCE))}",
        )
    if staging_status is not None and staging_status not in VALID_STAGING_STATUS:
        return _error_response(
            400,
            f"Invalid staging_status '{staging_status}'. "
            f"Must be one of: {', '.join(sorted(VALID_STAGING_STATUS))}",
        )

    all_properties: list[dict[str, Any]] = []

    filtered = all_properties
    if confidence is not None:
        filtered = [p for p in filtered if p.get("confidence") == confidence]
    if phase is not None:
        filtered = [p for p in filtered if p.get("phase") == phase]
    if property_category is not None:
        filtered = [
            p
            for p in filtered
            if p.get("property_category") == property_category
        ]
    if staging_status is not None:
        filtered = [
            p for p in filtered if p.get("staging_status") == staging_status
        ]
    if temperature_min is not None:
        filtered = [
            p
            for p in filtered
            if p.get("conditions", {}).get("temperature") is not None
            and float(p["conditions"]["temperature"]) >= temperature_min
        ]
    if temperature_max is not None:
        filtered = [
            p
            for p in filtered
            if p.get("conditions", {}).get("temperature") is not None
            and float(p["conditions"]["temperature"]) <= temperature_max
        ]

    total = len(filtered)
    start = (page - 1) * limit
    page_properties = filtered[start : start + limit]

    properties = [_to_v4_property(p) for p in page_properties]

    return JSONResponse(
        content={
            "success": True,
            "data": V4BrowseResponse(
                material_system=material_system,
                total_count=total,
                properties=[p.model_dump(mode="json") for p in properties],
            ).model_dump(mode="json"),
            "meta": {
                "total": total,
                "page": page,
                "limit": limit,
                "filters": {
                    "property_category": property_category,
                    "confidence": confidence,
                    "phase": phase,
                    "temperature_min": temperature_min,
                    "temperature_max": temperature_max,
                    "staging_status": staging_status,
                },
            },
        },
    )


# ---------------------------------------------------------------------------
# 5. POST /api/v4/extraction/{job_id}/validate
# ---------------------------------------------------------------------------


@router.post(
    "/extraction/{job_id}/validate",
    status_code=202,
)
async def validate_extraction(
    job_id: str,
    payload: V4ValidateRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """触发提取属性验证工作流。"""
    job = get_job(job_id)

    if job is None:
        return _error_response(
            404,
            f"Extraction job '{job_id}' not found.",
        )

    if job.status != JobStatus.COMPLETED:
        return _error_response(
            409,
            f"Job '{job_id}' is '{job.status.value}', not 'completed'. "
            "Validation can only be triggered on completed jobs.",
        )

    auto_approve = payload.auto_approve if payload else True

    raw_properties = await _get_job_properties(job_id, session)
    total_properties = len(raw_properties)

    high_count = sum(1 for p in raw_properties if p.get("confidence") == "high")
    medium_count = sum(
        1 for p in raw_properties if p.get("confidence") == "medium"
    )
    low_count = sum(1 for p in raw_properties if p.get("confidence") == "low")

    auto_approved = high_count if auto_approve else 0
    flagged = low_count
    sent_to_review = medium_count

    # TODO(NFM-561): Persist validation_id to DB so review_url can resolve.
    # Currently the validation workflow is synchronous and stateless.
    validation_id = f"val-{uuid.uuid4()}"

    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "data": V4ValidateResponse(
                job_id=job_id,
                validation_id=validation_id,
                total_properties=total_properties,
                auto_approved=auto_approved,
                sent_to_review=sent_to_review,
                flagged=flagged,
                review_url=f"/admin/v4-extraction/validate/{validation_id}",
            ).model_dump(mode="json"),
        },
    )


# ---------------------------------------------------------------------------
# 6. GET /api/v4/material-systems
# ---------------------------------------------------------------------------


@router.get("/material-systems")
async def list_material_systems(
    has_pending_review: bool = Query(default=False),
    category: str | None = Query(default=None),
) -> JSONResponse:
    """获取所有已提取属性数据的材料体系列表。"""
    systems = _build_material_systems_index()

    if has_pending_review:
        systems = [
            s for s in systems if s.get("pending_review_count", 0) > 0
        ]

    if category is not None:
        systems = [
            s for s in systems if category in s.get("categories", [])
        ]

    summaries = [
        V4MaterialSystemSummary(
            name=s["name"],
            display_name=s.get("display_name", s["name"]),
            total_properties=s.get("total_properties", 0),
            categories=s.get("categories", []),
            confidence_summary=V4ConfidenceSummary(
                **s.get("confidence_summary", {})
            ),
            pending_review_count=s.get("pending_review_count", 0),
            last_extraction_at=s.get("last_extraction_at"),
        ).model_dump(mode="json")
        for s in systems
    ]

    return JSONResponse(
        content={
            "success": True,
            "data": {"material_systems": summaries},
            "meta": {"total": len(summaries)},
        },
    )
