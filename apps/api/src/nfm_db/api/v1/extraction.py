"""Extraction pipeline API endpoints (NFM-66).

Trigger and monitor OntoFuel extraction jobs:
- POST /api/v1/extraction/trigger — Trigger extraction for a literature source
- GET  /api/v1/extraction/status/{job_id} — Check extraction job status
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.extraction import (
    ExtractionStatusResponse,
    ExtractionTriggerRequest,
    ExtractionTriggerResponse,
)
from nfm_db.services.extraction_pipeline import (
    get_job,
    trigger_extraction,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["提取管理"])


# ---------------------------------------------------------------------------
# POST /api/v1/extraction/trigger
# ---------------------------------------------------------------------------


@router.post(
    "/extraction/trigger",
    response_model=dict,
    status_code=202,
)
async def trigger_extraction_job(
    payload: ExtractionTriggerRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """触发文献数据提取任务。

    The pipeline runs: source → OntoFuel extraction → property mapping
    → quality gate → staging. Returns a job_id for status polling.

    Accepted source_type values: 'doi', 'url', 'file', 'internal_id'.
    """
    valid_source_types = {"doi", "url", "file", "internal_id"}
    if payload.source_type not in valid_source_types:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid source_type '{payload.source_type}'. "
                f"Must be one of: {', '.join(sorted(valid_source_types))}"
            ),
        )

    job = await trigger_extraction(
        session=session,
        source_reference=payload.source_reference,
        source_type=payload.source_type,
        element_systems=payload.element_systems,
        cache_level=payload.cache_level,
        max_confidence=payload.max_confidence,
    )

    return {
        "success": True,
        "data": ExtractionTriggerResponse(
            job_id=job.job_id,
            source_reference=job.source_reference,
            source_type=job.source_type,
            status=job.status.value,
            message="Extraction job queued successfully.",
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/status/{job_id}
# ---------------------------------------------------------------------------


@router.get("/extraction/status/{job_id}")
async def get_extraction_status(
    job_id: UUID,
) -> dict:
    """查询提取任务状态。

    Returns current status, counts of extracted/staged/rejected properties,
    timestamps, and error message (if failed).
    """
    job = get_job(str(job_id))

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Extraction job '{job_id}' not found.",
        )

    return {
        "success": True,
        "data": ExtractionStatusResponse(
            job_id=job.job_id,
            source_reference=job.source_reference,
            source_type=job.source_type,
            status=job.status.value,
            extracted_count=job.extracted_count,
            staged_count=job.staged_count,
            rejected_count=job.rejected_count,
            error_message=job.error_message,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        ).model_dump(),
    }
