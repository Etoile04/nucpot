"""Extraction pipeline API endpoints (NFM-66).

Trigger and monitor OntoFuel extraction jobs:
- POST /api/v1/extraction/trigger — Trigger extraction for a literature source
- GET  /api/v1/extraction/status/{job_id} — Check extraction job status
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.extraction import (
    ExtractionStatusResponse,
    ExtractionTriggerRequest,
)
from nfm_db.services.extraction_pipeline import get_job
from nfm_db.services.literature_dispatcher import (
    process_literature_task,
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
    summary="触发提取任务",
    description="触发文献数据提取任务。管道流程：数据源→OntoFuel提取→属性映射→质量门控→暂存。返回任务ID用于状态轮询。\n\nTrigger an extraction pipeline job. Flow: source → OntoFuel extraction → property mapping → quality gate → staging. Returns a job_id for polling.",
)
async def trigger_extraction_job(
    payload: ExtractionTriggerRequest,
    _current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """触发文献数据提取任务。

    The pipeline runs: source → OntoFuel extraction → property mapping
    → quality gate → staging.

    Dispatches to Celery via :func:`process_literature_task` instead of
    an asyncio.create_task background coroutine. The asyncio approach
    silently died under uvicorn ``--workers N > 1`` because each worker
    has its own event loop and module-level ``_job_store`` (the dict is
    per-process).  Celery tasks run in the dedicated worker process and
    persist to the broker queue — they survive uvicorn worker recycling
    (D1 monitor caught this regression on 2026-07-28, see PR #423).

    Accepted source_type values: 'doi', 'url', 'file', 'internal_id',
    'datasource'.
    """
    valid_source_types = {"doi", "url", "file", "internal_id", "datasource"}
    if payload.source_type not in valid_source_types:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid source_type '{payload.source_type}'. "
                f"Must be one of: {', '.join(sorted(valid_source_types))}"
            ),
        )

    # Dispatch via Celery. For 'datasource' source_type this maps 1:1
    # to process_literature_task. For 'doi' / 'url' / 'file' types the
    # worker still handles them via the same dispatcher (it'll
    # fetch-from-DOI / fetch-from-URL / copy-from-upload accordingly).
    celery_async = process_literature_task.delay(payload.source_reference)
    logger.info(
        "trigger_extraction: dispatched source_reference=%s to celery task_id=%s",
        payload.source_reference,
        celery_async.id,
    )

    return {
        "success": True,
        "data": {
            "source_reference": payload.source_reference,
            "source_type": payload.source_type,
            "status": "queued",
            # Surface the Celery task_id so callers can correlate the
            # Celery log entry. The legacy /api/v1/extraction/status/
            # endpoint is no longer used for these jobs — operators
            # should watch the Celery worker log instead.
            "job_id": celery_async.id,
            "message": "Extraction job queued. Celery worker will process asynchronously.",
        },
    }


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/status/{job_id}
# ---------------------------------------------------------------------------


@router.get(
    "/extraction/status/{job_id}",
    summary="查询提取任务状态",
    description="查询提取任务执行状态，包括已提取、已暂存、已拒绝的属性计数及时间戳。\n\nCheck extraction job status including extracted/staged/rejected property counts and timestamps.",
)
async def get_extraction_status(
    job_id: UUID,
) -> dict[str, object]:
    """查询提取任务执行状态。

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
