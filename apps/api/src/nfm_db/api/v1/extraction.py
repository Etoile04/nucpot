"""Extraction pipeline API endpoints (NFM-66).

Trigger and monitor OntoFuel extraction jobs:
- POST /api/v1/extraction/trigger — Trigger extraction for a literature source (human/editor)
- GET  /api/v1/extraction/status/{job_id} — Check extraction job status
- POST /api/v1/extraction/ingest   — OntoFuel service-account ingest endpoint (NFM-1973)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor, require_ingest_authority
from nfm_db.database import get_db
from nfm_db.models import Corpus, Dataset, DataSource, ExtractionJob, PropertyMeasurement
from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES, ExtractionStep
from nfm_db.models.user import User
from nfm_db.schemas.extraction import (
    ExtractionTriggerRequest,
)
from nfm_db.services.extraction_pipeline import _extraction_job_to_dict
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
        max_length=500,
        description=(
            "Source identifier OntoFuel used to produce this batch "
            "(DOI, URL, internal id, file path). Empty strings are "
            "accepted but cause sync-verification to be SKIPPED."
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
    reused_entities: int = Field(default=0, description="Existing DB entities reused (DataSource/Material already in DB).")
    skipped_duplicate_measurements: int = Field(default=0, description="Duplicate measurements skipped (5-tuple dedup).")
    skipped_unknown_properties: int = Field(default=0, description="Records skipped because the property is not in property_types.")
    skipped_duplicates: int = Field(default=0, description="[Deprecated] Total skipped. Equals reused_entities + skipped_duplicate_measurements + skipped_unknown_properties.")
    validation_errors: int = Field(default=0, description="Records that failed validation.")
    total_received: int = Field(default=0, description="Total property records in the request.")
    processing_time_ms: float = Field(default=0, description="Server-side processing time in milliseconds.")
    verified: bool = Field(default=False, description="AC-R3: True iff the per-request delta in PropertyMeasurement rows tied to source_reference equals created_measurements. Catches silent D1 dead-mode failures.")
    db_measurement_count: int = Field(default=0, description="AC-R3: PropertyMeasurement row count tied to source_reference AFTER this request's map_and_persist. Used to derive the per-request delta.")
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
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """查询提取任务执行状态。

    Returns current status, counts of extracted/staged/rejected properties,
    timestamps, and error message (if failed).
    """
    try:
        result = await session.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
        job = result.scalar_one_or_none()
    except (ValueError, SQLAlchemyError) as exc:
        logger.warning("ORM job lookup failed for %s: %s", job_id, exc)
        job = None

    if job is not None:
        return {
            "success": True,
            "data": _extraction_job_to_dict(job),
        }

    raise HTTPException(
        status_code=404,
        detail=f"Extraction job '{job_id}' not found.",
    )


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
    started_at = datetime.now(UTC)

    # NFM-2032 CR Finding #6: normalize the envelope's source_reference
    # into each property's source_doi when source_type == 'doi'.  Without
    # this, the dedup key for every property in the batch is empty, so
    # DataSource.find_or_create creates a new source per ingest and
    # the 5-tuple dedup can never line up across requests.  This is the
    # acknowledged root cause that masked NFM-2032's DB-level UNIQUE
    # from catching duplicates.
    if payload.source_type == "doi" and payload.source_reference:
        properties_for_mapper = [
            {**prop, "source_doi": payload.source_reference}
            if not prop.get("source_doi")
            else prop
            for prop in payload.properties
        ]
    else:
        properties_for_mapper = list(payload.properties)

    # --- Persist properties via map_and_persist (NFM-1983 AC-3) ---
    created_measurements = 0
    reused_entities = 0
    skipped_duplicate_measurements = 0
    skipped_unknown_properties = 0
    skipped_duplicates = 0
    validation_errors = 0
    errors: list[str] = []
    job_status = "completed"
    error_message: str | None = None

    # AC-R3 (NFM-2009 / NFM-2096 W1): sync verification — re-query DB to
    # confirm map_and_persist actually wrote the rows it claimed. Catches
    # silent D1 dead-mode failures where the mapper silently drops writes
    # (e.g. async LLM 502, validator returning early, transaction rolled
    # back by a later exception). Without this gate, the API returns
    # `created_measurements=N` while the row never lands.
    #
    # W1 fix (NFM-2096): use a *per-request delta*, not a cumulative
    # `count == created_measurements` equality.  The cumulative comparison
    # only holds on the FIRST ingest for a given source_reference; any
    # subsequent request carrying a distinct, valid, non-duplicate value
    # trips a false MISMATCH because the cumulative total already includes
    # the prior request's rows.  Snapshot the count BEFORE map_and_persist
    # and assert `(after - before) == created_measurements`.
    db_measurement_count = 0
    count_before = 0
    verified = False
    source_ref = payload.source_reference or ""

    def _count_q_for_source(ref: str):
        return (
            select(func.count(PropertyMeasurement.id))
            .join(Dataset, PropertyMeasurement.dataset_id == Dataset.id)
            .join(DataSource, Dataset.source_id == DataSource.id)
            .where(DataSource.doi == ref)
        )

    if payload.properties:
        # Snapshot the pre-persist count for the source_reference.  Skipped
        # silently if source_reference is empty (cannot match by DOI).
        if source_ref:
            try:
                count_before = (
                    await session.execute(_count_q_for_source(source_ref))
                ).scalar_one()
            except Exception:
                logger.exception(
                    "ingest_extraction_batch: pre-persist count failed job_id=%s",
                    job_id,
                )
                count_before = 0

        try:
            from nfm_db.services.extraction_to_db_mapper import (
                map_and_persist,
            )

            mapping_result = await map_and_persist(
                session, properties_for_mapper
            )
            created_measurements = mapping_result.created_measurements
            reused_entities = mapping_result.reused_entities
            skipped_duplicate_measurements = mapping_result.skipped_duplicate_measurements
            skipped_unknown_properties = mapping_result.skipped_unknown_properties
            skipped_duplicates = mapping_result.skipped_duplicates
            validation_errors = mapping_result.validation_errors
        except Exception as exc:
            # Log but do not fail — the ack is always returned.
            # Future: GraphBuilder isolation (NFM-1972-D) will add KG
            # node/edge creation here, isolated from measurement writes.
            logger.exception(
                "ingest_extraction_batch: map_and_persist failed for job_id=%s",
                job_id,
            )
            errors.append("map_and_persist raised an unexpected error")
            job_status = "failed"
            error_message = f"{type(exc).__name__}: {exc}"[:500]

    elapsed_ms = (time.monotonic() - t_start) * 1000
    completed_at = datetime.now(UTC)

    # AC-R3 post-persist verification: per-request delta vs. claimed
    # created_measurements.  This is the W1 fix — it survives legitimate
    # incremental ingests (POST#1 then POST#2 with distinct values under
    # the same DOI both report verified=True) while still catching silent
    # drops (mock that claims created_measurements=2 but inserts 0 rows).
    if payload.properties and source_ref:
        try:
            db_measurement_count = (
                await session.execute(_count_q_for_source(source_ref))
            ).scalar_one()
            delta = db_measurement_count - count_before
            verified = delta == created_measurements
            if not verified:
                drift_msg = (
                    f"sync-verification MISMATCH: claimed created_measurements="
                    f"{created_measurements} but per-request delta for "
                    f"source_reference={source_ref!r} is {delta} "
                    f"(count_before={count_before}, count_after="
                    f"{db_measurement_count})"
                )
                logger.error(
                    "ingest_extraction_batch: %s job_id=%s", drift_msg, job_id
                )
                errors.append(drift_msg)
        except Exception:
            logger.exception(
                "ingest_extraction_batch: post-persist verification query "
                "failed job_id=%s",
                job_id,
            )
            errors.append("sync-verification query raised an unexpected error")
    elif payload.properties and not source_ref:
        # No source_reference → cannot match by DOI; flag as unverified.
        verified = False
        errors.append("sync-verification SKIPPED: no source_reference")

    # --- NFM-2013 AC-2: persist an ExtractionJob row so the operator can
    # audit what landed and the new /status endpoint can serve the real
    # state instead of the in-memory facade.
    extraction_job = ExtractionJob(
        id=job_id,
        source_reference=payload.source_reference,
        source_type=payload.source_type,
        corpus_id=corpus.corpus_id,
        status=job_status,
        error_message=error_message,
        total_received=total_received,
        created_measurements=created_measurements,
        reused_entities=reused_entities,
        skipped_duplicate_measurements=skipped_duplicate_measurements,
        skipped_unknown_properties=skipped_unknown_properties,
        skipped_duplicates=skipped_duplicates,
        validation_errors=validation_errors,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(extraction_job)
    try:
        await session.flush()
    except Exception:
        logger.exception(
            "ingest_extraction_batch: failed to persist ExtractionJob %s; "
            "rolling back to preserve DB invariant",
            job_id,
        )
        await session.rollback()
        raise

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
            reused_entities=reused_entities,
            skipped_duplicate_measurements=skipped_duplicate_measurements,
            skipped_unknown_properties=skipped_unknown_properties,
            skipped_duplicates=skipped_duplicates,
            validation_errors=validation_errors,
            total_received=total_received,
            processing_time_ms=round(elapsed_ms, 1),
            verified=verified,
            db_measurement_count=db_measurement_count,
            errors=errors,
            received_at=datetime.now(UTC),
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/ingest/{job_id}/status  (NFM-2013)
# ---------------------------------------------------------------------------


@router.get(
    "/extraction/ingest/{job_id}/status",
    summary="查询提取任务状态",
    description=(
        "通过 ORM 查询提取任务的状态（UUID → 数据库行）。\n"
        "返回 404（未找到）或 503（数据库错误，Retry-After: 5s）。\n\n"
        "Look up an extraction job by UUID via ORM. "
        "Returns 404 if not found, or 503 with Retry-After on DB error."
    ),
)
async def get_ingest_job_status(
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """查询提取任务状态（NFM-2013 AC-5）。

    Looks up the job by UUID in the ``extraction_jobs`` table via ORM.
    Returns 404 if the row does not exist, or 503 with ``Retry-After: 5``
    on a database error.
    """
    # NFM-2013 AC-5: try the persisted ExtractionJob row first.  This is
    # the canonical state for POST /extraction/ingest jobs.
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        # NFM-3007 AC-3: Non-UUID job_id (legacy Celery task ID) is
        # deprecated.  The ingest endpoint always generates UUIDs.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Extraction job '{job_id}' is not a valid UUID. "
                "Non-UUID (legacy Celery) job IDs are no longer supported. "
                "Use a UUID-format job_id from a recent /extraction/ingest call."
            ),
            headers={"Deprecation": "true", "Sunset": "2026-12-31"},
        )

    try:
        row = (
            await session.execute(
                select(ExtractionJob).where(ExtractionJob.id == job_uuid)
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        # DB-level failure (connection refused, bind quirk, etc.).  Surface
        # as 503 so SRE can distinguish from a clean 404.
        logger.exception(
            "get_ingest_job_status: DB error querying ExtractionJob for id=%s",
            job_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable. Retry after a short delay.",
            headers={"Retry-After": "5"},
        )
    if row is not None:
        canonical = _extraction_job_to_dict(row)
        # Merge the 8 ingest-side extras on top of the canonical 24-key dict
        # so the ingest endpoint preserves the full contract.
        ingest_extras = {
            "corpus_id": row.corpus_id or "",
            "total_received": row.total_received,
            "created_measurements": row.created_measurements,
            "reused_entities": row.reused_entities,
            "skipped_duplicate_measurements": row.skipped_duplicate_measurements,
            "skipped_unknown_properties": row.skipped_unknown_properties,
            "skipped_duplicates": row.skipped_duplicates,
            "validation_errors": row.validation_errors,
        }
        return {
            "success": True,
            "data": {**canonical, **ingest_extras},
        }

    raise HTTPException(
        status_code=404,
        detail=f"Extraction job '{job_id}' not found.",
    )


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id}/steps/{step_name}  (NFM-3597 — Sibling C)
# ---------------------------------------------------------------------------
# Contract (from NFM-3543 AC):
#   200 → {job_id, step_name, status, track_id, artifacts,
#          started_at, finished_at, error}
#   404 → {"error": "step_not_found", "job_id": ..., "step_name": ...}
#         (unified shape; avoids existence disclosure)
#   ETag over (track_id, status, finished_at) — If-None-Match → 304.
#
# Distinction from NFM-2883 (``/extraction/jobs/.../steps/...``):
#   * adds ``artifacts`` (from ``step.metadata_.artifacts``) and ``error``
#     (from ``step.error_message``);
#   * renames ``completed_at`` → ``finished_at`` per the CTO contract;
#   * normalizes status ``completed`` → ``succeeded`` per the spec enum;
#   * adds ETag/304 revalidation.
#
# track_id is read from ``step.track_id`` when the NFM-3595 column lands,
# falling back to ``job.track_id`` (NFM-2881) so the route is usable
# before integration.
# ---------------------------------------------------------------------------


# Status normalization: the on-disk enum is
# ``(pending, running, completed, failed, skipped)`` but the CTO contract
# exposes the more conventional ``succeeded`` rather than ``completed``.
_STATUS_NORMALIZE = {"completed": "succeeded"}


def _normalize_status(raw: str) -> str:
    """Map on-disk status to the public contract enum."""
    return _STATUS_NORMALIZE.get(raw, raw)


def _step_track_id(step_row: ExtractionStep, job_row: ExtractionJob) -> str | None:
    """Return the durable step track_id, preferring the per-step column.

    Falls back to the parent job's track_id (NFM-2881) so the route is
    useful before NFM-3595's column lands. Returns ``None`` when neither
    is set.
    """
    step_tid = getattr(step_row, "track_id", None)
    if step_tid is not None:
        return str(step_tid)
    job_tid = getattr(job_row, "track_id", None)
    return str(job_tid) if job_tid is not None else None


def _step_artifacts(step_row: ExtractionStep) -> list[dict[str, object]]:
    """Pull artifacts list from ``step.metadata_["artifacts"]``.

    Returns an empty list when the column is empty or missing the key.
    Filters out malformed entries so the response shape stays predictable.
    """
    raw = getattr(step_row, "metadata_", None) or {}
    artifacts = raw.get("artifacts") if isinstance(raw, dict) else None
    if not isinstance(artifacts, list):
        return []
    cleaned: list[dict[str, object]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        try:
            cleaned.append(
                {
                    "key": str(item.get("key", "")),
                    "url": str(item.get("url", "")),
                    "size_bytes": int(item.get("size_bytes", 0)),
                }
            )
        except (TypeError, ValueError) as exc:
            # Malformed artifact entry — log and skip so one bad row does not
            # poison the whole step response. The endpoint contract only
            # promises well-formed entries; persisted metadata may contain
            # legacy or upstream-evolved shapes that don't fit the current
            # schema.
            logger.warning(
                "step_artifacts: skipping malformed artifact entry %r: %s",
                item,
                exc,
            )
            continue
    return cleaned


def _step_etag(track_id: str | None, status: str, finished_at: datetime | None) -> str:
    """Compute a stable ETag over (track_id, status, finished_at).

    The hash is deterministic so successive GETs against an unchanged step
    produce identical validators; any state transition invalidates the
    cache.
    """
    payload = f"{track_id or ''}|{status}|{finished_at.isoformat() if finished_at else ''}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


def _etag_matches(header_value: str | None, etag: str) -> bool:
    """RFC 7232 §3.2 weak/strong comparison.

    Both sides are quoted strings of the form ``"abc"`` (strong) or
    ``W/"abc"`` (weak). We accept either matching the stored ETag because
    the values we emit are always strong.
    """
    if not header_value:
        return False
    candidates = {tag.strip() for tag in header_value.split(",")}
    return etag in candidates or etag.lstrip("W/") in candidates


@router.get(
    "/jobs/{job_id}/steps/{step_name}",
    summary="查询单个作业步骤详情 (NFM-3597)",
    description=(
        "返回指定提取任务中某个步骤的完整状态：状态、track_id、产物、"
        "时间戳与错误信息。支持 ETag/304 重新校验。\n\n"
        "Return the full state of a single pipeline step within an "
        "extraction job: status, track_id, artifacts, timestamps, and "
        "error message. Supports ETag/304 revalidation."
    ),
    responses={
        200: {
            "description": "Step state.",
            "content": {
                "application/json": {
                    "example": {
                        "job_id": "00000000-0000-0000-0000-000000000000",
                        "step_name": "extract",
                        "status": "succeeded",
                        "track_id": "00000000-0000-0000-0000-000000000000",
                        "artifacts": [
                            {
                                "key": "chunks.json",
                                "url": "s3://bucket/chunks.json",
                                "size_bytes": 12345,
                            }
                        ],
                        "started_at": "2026-08-24T00:00:00+00:00",
                        "finished_at": "2026-08-24T00:00:05+00:00",
                        "error": None,
                    }
                }
            },
        },
        304: {"description": "Not modified (If-None-Match matched ETag)."},
        404: {
            "description": "Step not found (unified shape for unknown "
            "job_id and unknown step_name).",
        },
    },
)
async def get_job_step(
    job_id: UUID,
    step_name: str,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    session: AsyncSession = Depends(get_db),
) -> object:
    """查询单个作业步骤详情 (NFM-3597 / Sibling C).

    Reads are pure (DB SELECT) so the response is fully cacheable. The
    ETag folds in ``track_id`` (durable identity for Sibling D rerun),
    ``status``, and ``finished_at`` so any state transition invalidates
    the cache while unchanged steps return 304 cheaply.
    """
    # Validate step_name against the known set (defense in depth — also
    # gates the 404 path so unknown step names never reach the DB).
    if step_name not in EXTRACTION_STEP_TYPES:
        # 404 envelope is unified: callers cannot distinguish "unknown
        # job" from "unknown step".
        return _step_not_found_response(job_id, step_name)

    # Fetch the parent job.
    job_row = (
        await session.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
    ).scalar_one_or_none()

    if job_row is None:
        return _step_not_found_response(job_id, step_name)

    # Fetch the specific step.
    step_row = (
        await session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job_id,
                ExtractionStep.step_type == step_name,
            )
        )
    ).scalar_one_or_none()

    if step_row is None:
        return _step_not_found_response(job_id, step_name)

    track_id = _step_track_id(step_row, job_row)
    artifacts = _step_artifacts(step_row)
    finished_at = step_row.completed_at
    public_status = _normalize_status(step_row.status)
    etag = _step_etag(track_id, public_status, finished_at)

    # ETag short-circuit: per RFC 7232 §4.1, a 304 must carry the same
    # validators and an empty body.
    if _etag_matches(if_none_match, etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag},
        )

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return {
        "job_id": str(job_id),
        "step_name": step_name,
        "status": public_status,
        "track_id": track_id,
        "artifacts": artifacts,
        "started_at": (
            step_row.started_at.isoformat() if step_row.started_at else None
        ),
        "finished_at": (
            finished_at.isoformat() if finished_at else None
        ),
        "error": step_row.error_message,
    }


def _step_not_found_response(job_id: UUID, step_name: str) -> Response:
    """Unified 404 body for unknown job_id or step_name.

    Per the spec AC, both errors return the same shape to avoid leaking
    job/step existence. We return a ``Response`` directly so we can pin
    status_code=404 *and* ship the exact body shape the AC requires
    (``HTTPException`` would force the ``{"detail": ...}`` envelope).
    """
    import json as _json

    body = _json.dumps(
        {
            "error": "step_not_found",
            "job_id": str(job_id),
            "step_name": step_name,
        }
    )
    return Response(
        content=body,
        status_code=status.HTTP_404_NOT_FOUND,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/jobs/{job_id}/steps/{step_name}  (NFM-2883)
# ---------------------------------------------------------------------------


@router.get(
    "/extraction/jobs/{job_id}/steps/{step_name}",
    summary="查询单个管道步骤状态",
    description=(
        "返回指定提取任务中某个步骤的状态、时间戳及关联 track_id。\n\n"
        "Return the status, timestamps, and associated track_id for a "
        "single pipeline step within an extraction job."
    ),
)
async def get_extraction_step_status(
    job_id: UUID,
    step_name: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """查询单个管道步骤状态 (NFM-2883).

    Looks up the parent :class:`ExtractionJob` by ``job_id``, then the
    child :class:`ExtractionStep` by ``job_id + step_type``.  Returns a
    flat envelope with status, timestamps, and ``track_id`` (when the
    column exists; otherwise ``null``).
    """
    # Validate step_name against known step types.
    if step_name not in EXTRACTION_STEP_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Step '{step_name}' not found.",
        )

    # Fetch the parent job.
    job_row = (
        await session.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
    ).scalar_one_or_none()

    if job_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Extraction job '{job_id}' not found.",
        )

    # Fetch the specific step.
    step_row = (
        await session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job_id,
                ExtractionStep.step_type == step_name,
            )
        )
    ).scalar_one_or_none()

    if step_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step '{step_name}' not found for job '{job_id}'.",
        )

    # track_id column added by NFM-2881; use getattr for forward compat.
    track_id = getattr(job_row, "track_id", None)
    if track_id is not None:
        track_id = str(track_id)

    return {
        "job_id": str(job_id),
        "step_name": step_name,
        "status": step_row.status,
        "track_id": track_id,
        "started_at": step_row.started_at.isoformat() if step_row.started_at else None,
        "completed_at": step_row.completed_at.isoformat() if step_row.completed_at else None,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/extraction/jobs/{job_id}/steps/{step_name}/rerun  (NFM-2884)
# ---------------------------------------------------------------------------


@router.post(
    "/extraction/jobs/{job_id}/steps/{step_name}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
    summary="重跑单个管道步骤",
    description=(
        "Reset a single completed/failed pipeline step to ``pending``, "
        "clear the parent job's ``track_id``, and dispatch the step "
        "execution as a fire-and-forget background task.\n\n"
        "Returns 202 Accepted with the reset step snapshot.  Returns "
        "404 if the job or step does not exist, and 409 if the step is "
        "currently ``running`` (cannot rerun a step that is still in "
        "flight)."
    ),
)
async def rerun_extraction_step(
    job_id: UUID,
    step_name: str,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """Reset and dispatch a single pipeline step (NFM-2884).

    Implements the rerun contract documented in the parent epic
    (NFM-2872) and the issue body of NFM-2884:

    - AC1: new POST endpoint.
    - AC2: only ``completed`` or ``failed`` steps are eligible;
      everything else (including ``pending`` / ``skipped``) is rejected.
    - AC3: reset step to ``pending``, clear ``track_id`` on parent job,
      fire execution as ``asyncio.create_task``.
    - AC4: return 202 + the reset step snapshot.
    - AC5: 404 if job or step missing.
    - AC6: 409 if the step is currently ``running``.

    The dispatched task currently marks the step ``completed`` to
    demonstrate the fire-and-forget wiring; production orchestration
    will replace this stub once the per-step executor registry
    (NFM-2739 Phase B) lands.
    """
    # AC-5: validate step_name against known step types first.
    if step_name not in EXTRACTION_STEP_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"Step '{step_name}' not found.",
        )

    # Fetch the parent job (404 if missing).
    job_row = (
        await session.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_id)
        )
    ).scalar_one_or_none()

    if job_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Extraction job '{job_id}' not found.",
        )

    # Fetch the specific step (404 if missing).
    step_row = (
        await session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job_id,
                ExtractionStep.step_type == step_name,
            )
        )
    ).scalar_one_or_none()

    if step_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step '{step_name}' not found for job '{job_id}'.",
        )

    # AC-6: cannot rerun a step that is currently in flight.
    if step_row.status == "running":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step '{step_name}' is currently running; "
                "wait for completion before rerunning."
            ),
        )

    # AC-2: only completed / failed steps are eligible for rerun.
    if step_row.status not in ("completed", "failed"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step '{step_name}' has status '{step_row.status}'; "
                "only completed or failed steps can be rerun."
            ),
        )

    # AC-3: reset step state and clear parent track_id.
    step_row.status = "pending"
    step_row.started_at = None
    step_row.completed_at = None
    step_row.error_message = None
    # track_id lives on the parent job (NFM-2881); clear it.
    if hasattr(job_row, "track_id"):
        job_row.track_id = None
    await session.commit()
    await session.refresh(step_row)

    # AC-3: dispatch the step execution as fire-and-forget.
    # The stub marks the step completed immediately; production
    # orchestrator integration (NFM-2739 Phase B) will replace this.
    asyncio.create_task(_dispatch_rerun_step(job_id, step_name, step_row.id))  # noqa: RUF006 — fire-and-forget intentional

    # AC-4: return 202 with the reset step snapshot.
    track_id = getattr(job_row, "track_id", None)
    return {
        "job_id": str(job_id),
        "step_name": step_name,
        "status": step_row.status,
        "track_id": track_id if track_id is not None else None,
        "started_at": None,
        "completed_at": None,
        "dispatched": True,
    }


async def _dispatch_rerun_step(
    job_id: UUID,
    step_name: str,
    step_id: UUID,
) -> None:
    """Stub background task that marks the rerun step ``completed``.

    Production wiring (NFM-2739 Phase B) will look up the step executor
    in a registry and invoke it with the parent job's inputs.  Until
    that lands, this stub flips status to ``completed`` so the API
    contract (AC-4) is observable end-to-end.

    Runs on its own DB session because the request-scoped session is
    closed by the time this fires.
    """
    from nfm_db.database import async_session_factory
    from nfm_db.models.extraction_step import ExtractionStep

    try:
        async with async_session_factory() as session:
            step_row = (
                await session.execute(
                    select(ExtractionStep).where(
                        ExtractionStep.id == step_id,
                    )
                )
            ).scalar_one_or_none()
            if step_row is None:
                return
            now = datetime.now(UTC)
            step_row.status = "completed"
            step_row.started_at = now
            step_row.completed_at = now
            await session.commit()
    except Exception:
        logger.exception(
            "rerun dispatch failed for job_id=%s step=%s",
            job_id,
            step_name,
        )
