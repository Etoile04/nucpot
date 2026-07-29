"""Extraction pipeline API endpoints (NFM-66).

Trigger and monitor OntoFuel extraction jobs:
- POST /api/v1/extraction/trigger — Trigger extraction for a literature source (human/editor)
- GET  /api/v1/extraction/status/{job_id} — Check extraction job status
- POST /api/v1/extraction/ingest   — OntoFuel service-account ingest endpoint (NFM-1973)
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor, require_ingest_authority
from nfm_db.database import get_db
from nfm_db.models import Corpus
from nfm_db.models.user import User
from nfm_db.schemas.extraction import (
    ExtractionStatusResponse,
    ExtractionTriggerRequest,
)
from nfm_db.services.extraction_pipeline import get_job
from nfm_db.services.literature_dispatcher import (
    process_literature_task,
)
from nfm_db.services.rate_limit import ingest_rate_limit

logger = logging.getLogger(__name__)


class ExtractionIngestRequest(BaseModel):
    """Request body for ``POST /api/v1/extraction/ingest``.

    OntoFuel's nucpot client (NFM-1972 / NFM-1973) posts a JSON envelope
    containing extracted material properties plus provenance.  Fields are
    deliberately permissive so the upstream schema can evolve without
    requiring an API change here; the authoritative contract lives in
    ``OntoFuel`` (the upstream producer).  Missing or unknown fields are
    forwarded to the ingestion pipeline as-is.
    """

    source_reference: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Source identifier OntoFuel used to produce this batch "
            "(DOI, URL, internal id, file path)."
        ),
    )
    source_type: str = Field(
        default="doi",
        max_length=20,
        description="Type of source_reference: 'doi' | 'url' | 'file' | 'internal_id'.",
    )
    corpus_id: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "External corpus slug the batch belongs to (NFM-1972 AC-5). "
            "Service accounts may auto-create unknown corpora; human "
            "callers must reference an already-registered corpus."
        ),
    )
    element_systems: list[str] | None = Field(
        default=None,
        description="Element systems OntoFuel extracted for (e.g. ['U', 'Pu']).",
    )
    properties: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Material property records extracted by OntoFuel.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Provenance / OntoFuel run metadata (model version, timestamp, etc.).",
    )


class ExtractionIngestAck(BaseModel):
    """Acknowledgement returned by the service-account ingest endpoint.

    Conforms to the OntoFuel integration handoff contract (NFM-1972):
    the outer envelope is ``{success, data}`` so the OntoFuel client
    can call ``body.get("data", {}).get("ingested")`` etc.  Field names
    match the handoff doc exactly (``ingested`` not ``accepted_count``).
    """

    job_id: UUID = Field(description="Server-assigned id for this ingest batch.")
    source_reference: str
    source_type: str
    corpus_id: str = Field(description="Corpus the batch was tagged with.")
    ingested: int = Field(description="Number of property records ingested (new).")
    created_measurements: int = Field(default=0, description="Property measurements persisted.")
    skipped_duplicates: int = Field(default=0, description="Duplicate records skipped (5-tuple dedup).")
    validation_errors: int = Field(default=0, description="Records that failed validation.")
    total_received: int = Field(default=0, description="Total property records in the request.")
    processing_time_ms: float = Field(default=0, description="Server-side processing time in milliseconds.")
    errors: list[str] = Field(default_factory=list, description="Error details for failed records.")
    received_at: datetime
    message: str = "Ingest accepted; queued for processing."

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


# ---------------------------------------------------------------------------
# POST /api/v1/extraction/ingest  (NFM-1973 / NFM-1972 AC-1)
# ---------------------------------------------------------------------------
#
# This is the **only** HTTP endpoint in the API that admits service
# accounts.  Authorization is enforced by ``require_service_scope`` which
# verifies:
#   1. The JWT carries an ``is_service_account: true`` claim.
#   2. The JWT's ``scope`` claim equals
#      ``ServiceAccountScope.EXTRACTION_INGEST``.
#   3. The DB row has ``is_service_account=True`` (belt-and-suspenders
#      against forged tokens or post-issuance demotion).
#
# Any other endpoint — including ``/extraction/trigger`` and the entire
# ``/admin/*`` tree — returns ``403 Forbidden`` for service accounts via
# ``require_blog_role`` / ``require_permission`` (see ``api/v1/auth.py``).


@router.post(
    "/extraction/ingest",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提取批次入库（服务账号 / 编辑者）",
    description=(
        "接收 OntoFuel 服务账号（或人工编辑者）提交的提取批次。\n\n"
        "Accept an extraction batch from an OntoFuel service account "
        "(scope ``extraction:ingest``) or a human editor/admin. "
        "AC-5 corpus rules:\n"
        "* Service account + unknown ``corpus_id`` → auto-create the corpus.\n"
        "* Human editor/admin + unknown ``corpus_id`` → ``400 Bad Request``.\n"
        "Other identities (reviewers, no-blog-role humans, wrong scope) "
        "receive ``403 Forbidden``.\n\n"
        "Response conforms to the OntoFuel integration handoff contract: "
        "``{success: true, data: {ingested, skipped_duplicates, ...}}``."
    ),
)
async def ingest_extraction_batch(
    payload: ExtractionIngestRequest,
    caller: Annotated[User, Depends(require_ingest_authority())],
    _rate_limit: Annotated[None, Depends(ingest_rate_limit)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """接受服务账号或编辑者提交的提取批次。

    AC-5 missing-corpus behaviour:

    * ``caller.is_service_account`` and corpus absent → row created with
      ``is_auto_created=True, owner_id=None``.
    * Human caller and corpus absent → ``HTTP 400`` with the message
      ``corpus '<id>' not registered; contact admin``.

    The handler performs minimal synchronous validation — assigning a
    ``job_id``, resolving/creating the corpus, and counting accepted
    property records — and returns an ack.  Heavy lifting (property
    mapping, ontology lookup, persistence) is expected to land in a
    follow-up issue that wires this endpoint to the
    ``process_literature_task`` Celery pipeline.
    """
    corpus = (
        await session.execute(
            select(Corpus).where(Corpus.corpus_id == payload.corpus_id)
        )
    ).scalar_one_or_none()

    if corpus is None:
        if caller.is_service_account:
            corpus = Corpus(
                corpus_id=payload.corpus_id,
                name=payload.corpus_id,
                description=None,
                owner_id=None,
                is_auto_created=True,
            )
            session.add(corpus)
            await session.flush()
            logger.info(
                "ingest_extraction_batch: auto-created corpus_id=%s by svc_user=%s",
                payload.corpus_id,
                caller.username,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"corpus '{payload.corpus_id}' not registered; "
                    "contact admin"
                ),
            )

    # AC-6 (NFM-1982): batch-size cap.
    if len(payload.properties) > 500:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Batch size {len(payload.properties)} exceeds the "
                f"maximum of 500 properties per request."
            ),
        )

    job_id = uuid4()
    total_received = len(payload.properties)
    t_start = time.monotonic()

    # --- Persist properties via map_and_persist (NFM-1983 AC-3) ---
    created_measurements = 0
    skipped_duplicates = 0
    validation_errors = 0
    errors: list[str] = []

    if payload.properties:
        try:
            from nfm_db.services.extraction_to_db_mapper import (
                map_and_persist,
            )

            mapping_result = await map_and_persist(
                session, payload.properties
            )
            created_measurements = mapping_result.created_measurements
            skipped_duplicates = mapping_result.skipped_duplicates
            validation_errors = mapping_result.validation_errors
        except Exception:
            # Log but do not fail — the ack is always returned.
            # Future: GraphBuilder isolation (NFM-1972-D) will add KG
            # node/edge creation here, isolated from measurement writes.
            logger.exception(
                "ingest_extraction_batch: map_and_persist failed for job_id=%s",
                job_id,
            )
            errors.append("map_and_persist raised an unexpected error")

    elapsed_ms = (time.monotonic() - t_start) * 1000

    logger.info(
        "ingest_extraction_batch: job_id=%s source=%s corpus=%s caller=%s "
        "service=%s total=%d ingested=%d measurements=%d skipped=%d errors=%d",
        job_id,
        payload.source_reference,
        corpus.corpus_id,
        caller.username,
        caller.is_service_account,
        total_received,
        created_measurements,
        created_measurements,
        skipped_duplicates,
        len(errors),
    )

    return {
        "success": True,
        "data": ExtractionIngestAck(
            job_id=job_id,
            source_reference=payload.source_reference,
            source_type=payload.source_type,
            corpus_id=corpus.corpus_id,
            ingested=created_measurements,
            created_measurements=created_measurements,
            skipped_duplicates=skipped_duplicates,
            validation_errors=validation_errors,
            total_received=total_received,
            processing_time_ms=round(elapsed_ms, 1),
            errors=errors,
            received_at=datetime.now(UTC),
        ).model_dump(),
    }
