"""Reference values API endpoints: bulk staging, review queue, approve/reject.

Per NFM-54 design Sections 2.2-2.3:
- POST /api/v1/reference-values/bulk — Bulk write to staging
- GET  /api/v1/reference-values/pending-review — Review queue
- POST /api/v1/reference-values/{id}/approve — Approve + promote
- POST /api/v1/reference-values/{id}/reject — Reject
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.schemas.reference_values import (
    BulkStagingItemResult,
    BulkStagingRequest,
    BulkStagingResponse,
    PendingReviewResponse,
    ReviewRequest,
    ReviewResponse,
    StagingRecordResponse,
)
from nfm_db.services.promotion_service import (
    InvalidTransitionError,
    StagingRecordNotFoundError,
    approve_staging_record,
    reject_staging_record,
)
from nfm_db.services.quality_gate import QualityGateService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/bulk
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/bulk",
    response_model=dict,
    status_code=201,
)
async def bulk_stage_reference_values(
    payload: BulkStagingRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Bulk write reference values to staging.

    Accepts an array of reference_value dicts, runs quality gate on each
    (dedup, range check, confidence routing), and stages accepted values.

    Returns accepted/rejected counts with per-item status.
    """
    gate = QualityGateService(session)
    raw_values = [item.model_dump(by_alias=True) for item in payload.values]

    bulk_result = await gate.process_bulk(raw_values)

    results: list[BulkStagingItemResult] = []
    for gate_result in bulk_result.accepted:
        matching_raw = _find_matching_raw(raw_values, gate_result.dedup_hash)
        if matching_raw is not None:
            record = await gate.stage_record(matching_raw, gate_result)
            results.append(BulkStagingItemResult(
                staging_id=record.id,
                status=gate_result.decision.value,
                confidence=gate_result.confidence,
            ))

    for gate_result in bulk_result.duplicates:
        results.append(BulkStagingItemResult(
            status=gate_result.decision.value,
            confidence=gate_result.confidence,
        ))

    for gate_result in bulk_result.rejected:
        results.append(BulkStagingItemResult(
            status=gate_result.decision.value,
            confidence=gate_result.confidence,
        ))

    return {
        "success": True,
        "data": BulkStagingResponse(
            accepted=len(bulk_result.accepted),
            rejected=len(bulk_result.rejected) + len(bulk_result.duplicates),
            results=results,
        ).model_dump(),
    }


def _find_matching_raw(
    values: list[dict[str, Any]],
    dedup_hash: str,
) -> dict[str, Any] | None:
    """Find the raw input dict whose dedup_hash matches (approximate)."""
    from nfm_db.services.quality_gate import compute_dedup_hash

    for raw in values:
        raw_hash = compute_dedup_hash(
            element_system=str(raw.get("element_system", "")),
            phase=raw.get("phase"),
            property_name=str(raw.get("property", raw.get("property_name", ""))),
            method=raw.get("method"),
            source=str(raw.get("source", "")),
        )
        if raw_hash == dedup_hash:
            return raw
    return None


# ---------------------------------------------------------------------------
# GET /api/v1/reference-values/pending-review
# ---------------------------------------------------------------------------


@router.get("/reference-values/pending-review")
async def list_pending_review(
    element_system: str | None = Query(default=None, max_length=50),
    phase: str | None = Query(default=None, max_length=50),
    property_name: str | None = Query(default=None, max_length=100),
    confidence: Confidence | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Paginated list of staging records pending review.

    Filters: element_system, phase, property_name, confidence.
    Standard {success, data} envelope with pagination metadata.
    """
    base_filter = [
        RefGapFillStaging.status == StagingStatus.PENDING,
    ]

    if element_system is not None:
        base_filter.append(
            RefGapFillStaging.element_system == element_system,
        )
    if phase is not None:
        base_filter.append(RefGapFillStaging.phase == phase)
    if property_name is not None:
        base_filter.append(
            RefGapFillStaging.property_name == property_name,
        )
    if confidence is not None:
        base_filter.append(
            RefGapFillStaging.confidence == confidence,
        )

    # Count query
    count_stmt = select(func.count()).select_from(RefGapFillStaging).where(*base_filter)
    total = (await session.execute(count_stmt)).scalar_one()

    # Data query with pagination
    offset = (page - 1) * per_page
    data_stmt = (
        select(RefGapFillStaging)
        .where(*base_filter)
        .order_by(RefGapFillStaging.created_at.desc())
        .limit(per_page)
        .offset(offset)
    )
    result = await session.execute(data_stmt)
    records = result.scalars().all()

    return {
        "success": True,
        "data": PendingReviewResponse(
            records=[StagingRecordResponse.model_validate(r) for r in records],
            total=total,
            page=page,
            per_page=per_page,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/approve
# ---------------------------------------------------------------------------


@router.post("/reference-values/{staging_id}/approve")
async def approve_reference_value(
    staging_id: UUID,
    payload: ReviewRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Approve and promote a staging record.

    Accepts an optional review_note. Runs the promotion pipeline:
    updates staging status to PROMOTED with review metadata and timestamp.

    When the NFMD normalized schema is available, this will also INSERT
    into property_measurements + measurement_conditions.
    """
    body = payload if payload is not None else ReviewRequest()

    try:
        record = await approve_staging_record(
            session=session,
            staging_id=staging_id,
            review_note=body.review_note,
        )
    except StagingRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Staging record not found")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "success": True,
        "data": ReviewResponse(
            staging_id=record.id,
            status=record.status,
            review_note=record.review_note,
            property_measurement_id=record.promoted_to_pm_id,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/reject
# ---------------------------------------------------------------------------


@router.post("/reference-values/{staging_id}/reject")
async def reject_reference_value(
    staging_id: UUID,
    payload: ReviewRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Reject a staging record.

    Accepts an optional review_note. Updates staging status to REJECTED
    with review metadata and timestamp.
    """
    body = payload if payload is not None else ReviewRequest()

    try:
        record = await reject_staging_record(
            session=session,
            staging_id=staging_id,
            review_note=body.review_note,
        )
    except StagingRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Staging record not found")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "success": True,
        "data": ReviewResponse(
            staging_id=record.id,
            status=record.status,
            review_note=record.review_note,
        ).model_dump(),
    }
