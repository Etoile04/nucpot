"""Re-extraction queue API endpoints (NFM-2581 / NFM-2573-T4).

Allows domain experts to trigger re-extraction of corpora when an ontology
version upgrades.  Queue entries are consumed by the re_extraction_worker
module which dispatches actual extraction pipeline runs.

Endpoints:
  - POST /ontology/versions/{id}/re-extract — trigger re-extraction for selected corpora
  - GET  /re-extraction/queue — list queue entries
  - GET  /re-extraction/queue/{id} — get queue entry status
  - POST /re-extraction/queue/{id}/cancel — cancel a pending entry
  - POST /re-extraction/queue/{id}/process — manually process a single entry
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_domain_expert
from nfm_db.database import get_db
from nfm_db.models import Corpus, OntologyVersion, ReExtractionQueue
from nfm_db.models.re_extraction_queue import RE_EXTRACTION_STATUSES
from nfm_db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["重新提取管理"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TriggerReExtractionRequest(BaseModel):
    """Body for ``POST /ontology/versions/{id}/re-extract``."""

    corpus_ids: list[UUID] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of corpus IDs to re-extract.",
    )


class ReExtractionQueueItem(BaseModel):
    """Single queue entry returned by list/detail endpoints."""

    id: UUID
    ontology_version_id: UUID
    corpus_id: UUID
    triggered_by: UUID
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TriggerReExtractionResponse(BaseModel):
    """Response for the trigger endpoint."""

    created: list[ReExtractionQueueItem]
    skipped: list[dict[str, str]]


# ---------------------------------------------------------------------------
# POST /ontology/versions/{id}/re-extract
# ---------------------------------------------------------------------------


@router.post(
    "/ontology/versions/{version_id}/re-extract",
    response_model=TriggerReExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="触发重新提取",
    description=(
        "为指定本体版本触发所选语料库的重新提取。需要 domain_expert 角色。\n\n"
        "Trigger re-extraction for selected corpora against an ontology version. "
        "Requires domain_expert role."
    ),
)
async def trigger_re_extraction(
    version_id: UUID,
    payload: TriggerReExtractionRequest,
    current_user: Annotated[User, Depends(require_domain_expert)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TriggerReExtractionResponse:
    """Trigger re-extraction for selected corpora against an ontology version.

    Creates one ReExtractionQueue entry per corpus, all with status=pending.
    Skips corpora that already have a pending/running entry for the same
    ontology version (idempotency guard).
    """
    # Verify ontology version exists.
    ov = (
        await session.execute(
            select(OntologyVersion).where(OntologyVersion.id == version_id)
        )
    ).scalar_one_or_none()

    if ov is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ontology version '{version_id}' not found.",
        )

    created: list[ReExtractionQueueItem] = []
    skipped: list[dict[str, str]] = []

    for corpus_id in payload.corpus_ids:
        # Verify corpus exists.
        corpus = (
            await session.execute(
                select(Corpus).where(Corpus.id == corpus_id)
            )
        ).scalar_one_or_none()

        if corpus is None:
            skipped.append(
                {"corpus_id": str(corpus_id), "reason": "corpus not found"}
            )
            continue

        # Idempotency: skip if a pending/running entry already exists.
        existing = (
            await session.execute(
                select(ReExtractionQueue).where(
                    ReExtractionQueue.ontology_version_id == version_id,
                    ReExtractionQueue.corpus_id == corpus_id,
                    ReExtractionQueue.status.in_(["pending", "running"]),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            skipped.append(
                {
                    "corpus_id": str(corpus_id),
                    "reason": f"already queued (entry {existing.id})",
                }
            )
            continue

        entry = ReExtractionQueue(
            ontology_version_id=version_id,
            corpus_id=corpus_id,
            triggered_by=current_user.id,
            status="pending",
        )
        session.add(entry)
        await session.flush()
        created.append(_row_to_item(entry))

    logger.info(
        "trigger_re_extraction: version_id=%s user=%s created=%d skipped=%d",
        version_id,
        current_user.username,
        len(created),
        len(skipped),
    )

    return TriggerReExtractionResponse(created=created, skipped=skipped)


# ---------------------------------------------------------------------------
# GET /re-extraction/queue
# ---------------------------------------------------------------------------


@router.get(
    "/re-extraction/queue",
    response_model=list[ReExtractionQueueItem],
    summary="列出重新提取队列",
    description=(
        "列出所有重新提取队列条目。需要 domain_expert 角色。\n\n"
        "List all re-extraction queue entries. Requires domain_expert role."
    ),
)
async def list_re_extraction_queue(
    current_user: Annotated[User, Depends(require_domain_expert)],
    session: Annotated[AsyncSession, Depends(get_db)],
    ontology_version_id: UUID | None = None,
    corpus_id: UUID | None = None,
    status: str | None = None,
) -> list[ReExtractionQueueItem]:
    """List re-extraction queue entries with optional filters."""
    query = select(ReExtractionQueue).order_by(
        ReExtractionQueue.created_at.desc()
    )

    if ontology_version_id is not None:
        query = query.where(
            ReExtractionQueue.ontology_version_id == ontology_version_id
        )
    if corpus_id is not None:
        query = query.where(ReExtractionQueue.corpus_id == corpus_id)
    if status is not None:
        if status not in RE_EXTRACTION_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid status '{status}'. "
                    f"Must be one of: {', '.join(RE_EXTRACTION_STATUSES)}"
                ),
            )
        query = query.where(ReExtractionQueue.status == status)

    result = await session.execute(query)
    rows = result.scalars().all()
    return [_row_to_item(row) for row in rows]


# ---------------------------------------------------------------------------
# GET /re-extraction/queue/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/re-extraction/queue/{entry_id}",
    response_model=ReExtractionQueueItem,
    summary="查询重新提取队列状态",
    description=(
        "获取指定重新提取队列条目的状态。需要 domain_expert 角色。\n\n"
        "Get a specific re-extraction queue entry. Requires domain_expert role."
    ),
)
async def get_re_extraction_entry(
    entry_id: UUID,
    current_user: Annotated[User, Depends(require_domain_expert)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ReExtractionQueueItem:
    """Get a single re-extraction queue entry by ID."""
    entry = (
        await session.execute(
            select(ReExtractionQueue).where(ReExtractionQueue.id == entry_id)
        )
    ).scalar_one_or_none()

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Re-extraction queue entry '{entry_id}' not found.",
        )

    return _row_to_item(entry)


# ---------------------------------------------------------------------------
# POST /re-extraction/queue/{id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/re-extraction/queue/{entry_id}/cancel",
    response_model=ReExtractionQueueItem,
    summary="取消重新提取任务",
    description=(
        "取消待处理的重新提取任务。需要 domain_expert 角色。\n\n"
        "Cancel a pending re-extraction entry. Requires domain_expert role."
    ),
)
async def cancel_re_extraction(
    entry_id: UUID,
    current_user: Annotated[User, Depends(require_domain_expert)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ReExtractionQueueItem:
    """Cancel a pending re-extraction queue entry.

    Only entries with status='pending' can be cancelled.
    """
    entry = (
        await session.execute(
            select(ReExtractionQueue).where(ReExtractionQueue.id == entry_id)
        )
    ).scalar_one_or_none()

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Re-extraction queue entry '{entry_id}' not found.",
        )

    if entry.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot cancel entry in status '{entry.status}'. "
                "Only 'pending' entries can be cancelled."
            ),
        )

    entry.status = "cancelled"
    await session.flush()
    # Refresh so server-side defaults/onupdate columns (notably
    # ``updated_at``) are eagerly loaded before we serialize the
    # response.  Without this, attribute access during Pydantic
    # serialization triggers async lazy IO outside the SQLAlchemy
    # greenlet context, raising ``MissingGreenlet`` under async drivers.
    await session.refresh(entry)

    logger.info(
        "cancel_re_extraction: entry_id=%s user=%s",
        entry_id,
        current_user.username,
    )

    return _row_to_item(entry)


# ---------------------------------------------------------------------------
# POST /re-extraction/queue/{id}/process — manually process a single entry
# ---------------------------------------------------------------------------


@router.post(
    "/re-extraction/queue/{entry_id}/process",
    response_model=ReExtractionQueueItem,
    summary="处理重新提取任务",
    description=(
        "手动触发处理单个重新提取队列条目。需要 domain_expert 角色。\n\n"
        "Manually process a single re-extraction queue entry by invoking "
        "the extraction pipeline. Requires domain_expert role."
    ),
)
async def process_re_extraction_entry(
    entry_id: UUID,
    current_user: Annotated[User, Depends(require_domain_expert)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ReExtractionQueueItem:
    """Process a single re-extraction queue entry.

    Invokes the re_extraction_worker to run the extraction pipeline
    for this entry's corpus against the specified ontology version.
    Updates the entry status to running, then completed/failed.
    """
    from nfm_db.services.re_extraction_worker import process_single_entry

    entry = (
        await session.execute(
            select(ReExtractionQueue).where(ReExtractionQueue.id == entry_id)
        )
    ).scalar_one_or_none()

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Re-extraction queue entry '{entry_id}' not found.",
        )

    if entry.status not in ("pending", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot process entry in status '{entry.status}'. "
                "Only 'pending' or 'failed' entries can be processed."
            ),
        )

    try:
        entry = await process_single_entry(session, entry_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await session.refresh(entry)

    logger.info(
        "process_re_extraction_entry: entry_id=%s user=%s status=%s",
        entry_id,
        current_user.username,
        entry.status,
    )

    return _row_to_item(entry)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _row_to_item(row: ReExtractionQueue) -> ReExtractionQueueItem:
    """Convert an ORM row to a Pydantic response item."""
    return ReExtractionQueueItem(
        id=row.id,
        ontology_version_id=row.ontology_version_id,
        corpus_id=row.corpus_id,
        triggered_by=row.triggered_by,
        status=row.status,
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        error_message=row.error_message,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
