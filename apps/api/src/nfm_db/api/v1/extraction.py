"""Extraction pipeline API endpoints (NFM-66).

Trigger and monitor OntoFuel extraction jobs:
- POST /api/v1/extraction/trigger — Trigger extraction for a literature source
- GET  /api/v1/extraction/status/{job_id} — Check extraction job status
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid_module
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import async_session_factory, get_db
from nfm_db.models.user import User
from nfm_db.schemas.extraction import (
    ExtractionStatusResponse,
    ExtractionTriggerRequest,
)
from nfm_db.services.extraction_pipeline import (
    get_job,
    trigger_extraction,
)

logger = logging.getLogger(__name__)

# Module-level set of in-flight background extraction tasks. Used so the
# FastAPI request handler doesn't lose the task reference (RUF006) and
# to allow graceful shutdown via ``_bg_tasks.clear()``.
_bg_tasks: set[asyncio.Task[None]] = set()

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
    → quality gate → staging. Returns a job_id for status polling.

    Accepted source_type values: 'doi', 'url', 'file', 'internal_id'.
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

    # Run extraction in the background with its own DB session to avoid
    # Cloudflare Tunnel 100s timeout (Qwen3 thinking mode: 60-120s).
    # Pre-generate a job_id so the caller can poll the status endpoint
    # immediately instead of guessing (2026-07-28 follow-up).
    async def _bg_extraction(
        source_reference: str,
        source_type: str,
        element_systems: list[str] | None,
        cache_level: str | None,
        max_confidence: str | None,
        bg_job_id: str,
    ) -> None:
        async with async_session_factory() as bg:
            try:
                await trigger_extraction(
                    session=bg,
                    source_reference=source_reference,
                    source_type=source_type,
                    element_systems=element_systems,
                    cache_level=cache_level,
                    max_confidence=max_confidence,
                    job_id=bg_job_id,
                )
            except Exception:
                logger.exception("Background extraction failed")

    job_id = str(_uuid_module.uuid4())
    task = asyncio.create_task(
        _bg_extraction(
            payload.source_reference,
            payload.source_type,
            payload.element_systems,
            payload.cache_level,
            payload.max_confidence,
            job_id,
        )
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return {
        "success": True,
        "data": {
            "source_reference": payload.source_reference,
            "source_type": payload.source_type,
            "status": "queued",
            # Surface the job_id from _job_store so callers can poll
            # /api/v1/extraction/status/{job_id} for progress. Previously
            # only the background pipeline knew the job_id, which made
            # it impossible to watch a single trigger's outcome (D1
            # follow-up, 2026-07-28).
            "job_id": job_id,
            "message": "Extraction job queued. Check review queue for results.",
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
