"""Extraction-gap API endpoints (NFM-2599 / NFM-2575-T3).

Listing, detail, and status-transition API for the
``ExtractionGap`` ORM model produced by :class:`GapScanService`.

Endpoints
---------
- GET   /api/v1/extraction-gaps                — paginated + filtered list
- GET   /api/v1/extraction-gaps/{gap_id}       — detail with chunk source_reference
- PATCH /api/v1/extraction-gaps/{gap_id}/status — status transition

Status lifecycle: ``open -> filling|wont_fix`` ; ``filling -> filled|wont_fix``.
Terminal statuses (``filled``, ``wont_fix``) are immutable and reject updates
with HTTP 409.  ``resolved_at`` is set when transitioning to a terminal state.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models import ExtractionChunk, ExtractionGap
from nfm_db.models.extraction_gap import EXTRACTION_GAP_STATUSES
from nfm_db.schemas.extraction_gap import ExtractionGapResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["提取缺口管理"])


# ---------------------------------------------------------------------------
# Request / response envelopes
# ---------------------------------------------------------------------------


class ExtractionGapListResponse(BaseModel):
    """Paginated list envelope returned by the list endpoint."""

    data: list[ExtractionGapResponse]
    meta: dict[str, int]


class GapStatusUpdateRequest(BaseModel):
    """Request body for ``PATCH /extraction-gaps/{gap_id}/status``.

    The allowed values are only the *target* states a gap can transition
    to (``filling``, ``filled``, ``wont_fix``); the current state is
    validated server-side against the lifecycle.
    """

    status: str = Field(
        description="Target status: filling | filled | wont_fix.",
    )


# Statuses the PATCH endpoint will accept on the wire.
_PATCHABLE_STATUSES: frozenset[str] = frozenset({"filling", "filled", "wont_fix"})

# Statuses considered terminal — once reached, the row is immutable.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"filled", "wont_fix"})

# Allowed transitions: source -> set of permitted target statuses.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"filling", "wont_fix"}),
    "filling": frozenset({"filled", "wont_fix"}),
    "filled": frozenset(),  # terminal
    "wont_fix": frozenset(),  # terminal
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_gap_or_404(
    session: AsyncSession, gap_id: uuid.UUID,
) -> ExtractionGap:
    """Fetch a single gap, raising 404 if absent."""
    gap = (
        await session.execute(
            select(ExtractionGap).where(ExtractionGap.id == gap_id),
        )
    ).scalar_one_or_none()
    if gap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction gap '{gap_id}' not found.",
        )
    return gap


def _to_response(
    gap: ExtractionGap, chunk: ExtractionChunk | None,
) -> ExtractionGapResponse:
    """Map an ORM row (+ optionally its chunk) to the public response.

    The chunk's ``source_reference`` is surfaced under the gap's
    ``source_reference`` field when the gap has a chunk_id.  This is the
    "Detail endpoint includes chunk source reference" AC.
    """
    return ExtractionGapResponse.model_validate(
        {
            "id": gap.id,
            "ontology_version_id": gap.ontology_version_id,
            "entity_type": gap.entity_type,
            "property": gap.property,
            "source_reference": (
                chunk.source_reference if chunk is not None else None
            ),
            "chunk_id": gap.chunk_id,
            "gap_status": gap.gap_status,
            "detected_at": gap.detected_at,
            "resolved_at": gap.resolved_at,
            "created_at": gap.created_at,
            "updated_at": gap.updated_at,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps — list
# ---------------------------------------------------------------------------


@router.get(
    "/extraction-gaps",
    response_model=ExtractionGapListResponse,
    summary="列出提取缺口",
    description=(
        "分页查询提取缺口，支持按本体版本、实体类型、缺口状态、所属任务筛选。\n\n"
        "List extraction gaps with pagination and filtering by ontology "
        "version (required), entity_type, gap_status, and job_id."
    ),
)
async def list_extraction_gaps(
    ontology_version_id: uuid.UUID = Query(
        ...,
        description="Ontology version id — required for all list queries.",
    ),
    entity_type: str | None = Query(
        default=None,
        max_length=100,
        description="Optional entity_type filter.",
    ),
    gap_status: str | None = Query(
        default=None,
        description=(
            "Optional gap_status filter. "
            f"One of: {', '.join(EXTRACTION_GAP_STATUSES)}."
        ),
    ),
    job_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Optional job_id filter. Matches gaps whose chunk belongs to "
            "the given extraction job."
        ),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Page size (1-200, default 50).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Page offset (default 0).",
    ),
    session: AsyncSession = Depends(get_db),
) -> ExtractionGapListResponse:
    """Paginated extraction-gap listing.

    Implements the API contract from the issue spec:

    - ``ontology_version_id`` (UUID, required)
    - ``entity_type``, ``gap_status`` (open|filling|filled|wont_fix), ``job_id`` optional
    - ``limit`` (default 50, max 200), ``offset`` (default 0)
    - Response envelope: ``{ data: [...], meta: { total, limit, offset } }``
    """
    if gap_status is not None and gap_status not in EXTRACTION_GAP_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid gap_status '{gap_status}'. "
                f"Must be one of: {', '.join(EXTRACTION_GAP_STATUSES)}"
            ),
        )

    # Build the WHERE clause incrementally.
    where_clauses = [ExtractionGap.ontology_version_id == ontology_version_id]
    if entity_type is not None:
        where_clauses.append(ExtractionGap.entity_type == entity_type)
    if gap_status is not None:
        where_clauses.append(ExtractionGap.gap_status == gap_status)
    join_chunk = job_id is not None

    if join_chunk:
        # ``job_id`` filter — fetch candidate chunk_ids via a subquery
        # against extraction_chunks (cheaper than a full JOIN when the
        # chunk set is small relative to the gap set).
        chunk_ids = (
            await session.execute(
                select(ExtractionChunk.id).where(
                    ExtractionChunk.job_id == job_id,
                ),
            )
        ).scalars().all()
        if not chunk_ids:
            return ExtractionGapListResponse(
                data=[],
                meta={"total": 0, "limit": limit, "offset": offset},
            )
        where_clauses.append(ExtractionGap.chunk_id.in_(chunk_ids))

    # Count query (independent of limit/offset).
    total_stmt = select(func.count(ExtractionGap.id)).where(*where_clauses)
    total = (await session.execute(total_stmt)).scalar_one()

    # Page query.
    page_stmt = (
        select(ExtractionGap)
        .where(*where_clauses)
        .order_by(ExtractionGap.detected_at.asc(), ExtractionGap.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(page_stmt)).scalars().all()

    # When any row has a chunk_id, hydrate the related chunk for
    # source_reference surfacing.  Done in a single round-trip.
    row_chunk_ids = {row.chunk_id for row in rows if row.chunk_id is not None}
    chunks_by_id: dict[uuid.UUID, ExtractionChunk] = {}
    if row_chunk_ids:
        chunk_rows = (
            await session.execute(
                select(ExtractionChunk).where(
                    ExtractionChunk.id.in_(row_chunk_ids),
                ),
            )
        ).scalars().all()
        chunks_by_id = {c.id: c for c in chunk_rows}

    items = [
        _to_response(row, chunks_by_id.get(row.chunk_id) if row.chunk_id else None)
        for row in rows
    ]
    return ExtractionGapListResponse(
        data=items,
        meta={"total": int(total), "limit": limit, "offset": offset},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps/{gap_id} — detail
# ---------------------------------------------------------------------------


@router.get(
    "/extraction-gaps/{gap_id}",
    response_model=ExtractionGapResponse,
    summary="查询提取缺口",
    description=(
        "获取指定提取缺口的详情，包含关联 chunk 的 source_reference "
        "（若设置了 chunk_id）。\n\n"
        "Get the detail of a single extraction gap; the related chunk's "
        "source_reference is surfaced when chunk_id is set."
    ),
)
async def get_extraction_gap(
    gap_id: uuid.UUID = Path(..., description="Extraction gap id."),
    session: AsyncSession = Depends(get_db),
) -> ExtractionGapResponse:
    """Return a single extraction gap, including chunk source_reference."""
    gap = await _load_gap_or_404(session, gap_id)

    chunk: ExtractionChunk | None = None
    if gap.chunk_id is not None:
        chunk = (
            await session.execute(
                select(ExtractionChunk).where(
                    ExtractionChunk.id == gap.chunk_id,
                ),
            )
        ).scalar_one_or_none()

    return _to_response(gap, chunk)


# ---------------------------------------------------------------------------
# PATCH /api/v1/extraction-gaps/{gap_id}/status — transition
# ---------------------------------------------------------------------------


@router.patch(
    "/extraction-gaps/{gap_id}/status",
    response_model=ExtractionGapResponse,
    summary="更新提取缺口状态",
    description=(
        "转换缺口状态。可用转换：open → filling|wont_fix；"
        "filling → filled|wont_fix。终态（filled、wont_fix）不可修改，"
        "返回 409。\n\n"
        "Transition an extraction gap's status. "
        "Allowed transitions: open → filling|wont_fix; "
        "filling → filled|wont_fix. Terminal states (filled, wont_fix) "
        "are immutable and return 409."
    ),
)
async def update_extraction_gap_status(
    payload: GapStatusUpdateRequest,
    gap_id: uuid.UUID = Path(..., description="Extraction gap id."),
    session: AsyncSession = Depends(get_db),
) -> ExtractionGapResponse:
    """Apply a status transition to a single gap.

    Errors:
    - 404 — gap_id not found.
    - 422 — body.status not in {filling, filled, wont_fix}.
    - 400 — body.status is a valid target but the transition is not
      permitted from the gap's current state (e.g. open → filled).
    - 409 — gap is already in a terminal state.
    """
    target = payload.status
    if target not in _PATCHABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid status '{target}'. "
                f"Must be one of: {', '.join(sorted(_PATCHABLE_STATUSES))}"
            ),
        )

    gap = await _load_gap_or_404(session, gap_id)

    if gap.gap_status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Gap '{gap_id}' is in terminal status '{gap.gap_status}' "
                "and cannot be modified."
            ),
        )

    allowed_targets = _ALLOWED_TRANSITIONS.get(gap.gap_status, frozenset())
    if target not in allowed_targets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid transition '{gap.gap_status}' → '{target}'. "
                f"Allowed targets from '{gap.gap_status}': "
                f"{', '.join(sorted(allowed_targets)) or '(none — terminal)'}"
            ),
        )

    previous_status = gap.gap_status
    gap.gap_status = target
    if target in _TERMINAL_STATUSES:
        gap.resolved_at = datetime.now(UTC)

    await session.flush()
    # Refresh to load server-side onupdate columns (notably updated_at)
    # so Pydantic serialization doesn't trigger async lazy IO outside the
    # SQLAlchemy greenlet (see re_extraction.py:307 for the same fix).
    await session.refresh(gap)

    chunk: ExtractionChunk | None = None
    if gap.chunk_id is not None:
        chunk = (
            await session.execute(
                select(ExtractionChunk).where(
                    ExtractionChunk.id == gap.chunk_id,
                ),
            )
        ).scalar_one_or_none()

    logger.info(
        "update_extraction_gap_status: gap_id=%s %s -> %s resolved_at=%s",
        gap_id,
        previous_status,
        target,
        gap.resolved_at,
    )

    return _to_response(gap, chunk)
