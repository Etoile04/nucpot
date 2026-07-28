"""Material deduplication API endpoints (NFM-1391, B3.1.1).

Endpoints (mounted at /api/v1/dedup):

- GET  /dedup/duplicates         -- list candidate duplicate material pairs
- POST /dedup/merge              -- record a merge decision in entity_merge_log
- GET  /dedup/logs               -- paginated audit log
- GET  /dedup/logs/{log_id}      -- single audit log row

The router is read-mostly: only the merge endpoint writes new state, and
it only writes to ``entity_merge_log``.  It does NOT mutate downstream
properties/sources/KG references; that cascade is the caller's job.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.entity_merge import MatchMethod
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.services.dedup_service import (
    DuplicateCandidate,
    MergeResult,
    execute_merge,
    find_duplicates,
    get_merge_log,
    list_merge_logs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dedup", tags=["实体去重"])


# ---------------------------------------------------------------------------
# Response schemas (inline -- kept close to the endpoints that emit them)
# ---------------------------------------------------------------------------


class DuplicateCandidateResponse(BaseModel):
    """A single duplicate pair detected by the dedup engine."""

    canonical_id: UUID
    canonical_name: str
    duplicate_id: UUID
    duplicate_name: str
    match_score: float
    match_method: MatchMethod
    matched_aliases: list[str] = Field(default_factory=list)


class MergeRequest(BaseModel):
    """Body for POST /dedup/merge."""

    canonical_id: UUID
    duplicate_id: UUID
    match_score: float = Field(..., ge=0.0, le=1.0)
    match_method: MatchMethod
    matched_aliases: list[str] = Field(default_factory=list)


class MergeLogResponse(BaseModel):
    """A single row from ``entity_merge_log``."""

    id: UUID
    canonical_id: UUID
    merged_id: UUID
    match_score: float
    match_method: MatchMethod
    merged_at: datetime
    details: dict | None = None


def _candidate_to_response(c: DuplicateCandidate) -> DuplicateCandidateResponse:
    return DuplicateCandidateResponse(
        canonical_id=c.canonical.id,
        canonical_name=c.canonical.name,
        duplicate_id=c.duplicate.id,
        duplicate_name=c.duplicate.name,
        match_score=c.match_score,
        match_method=c.match_method,
        matched_aliases=list(c.matched_aliases),
    )


def _log_to_response(row) -> MergeLogResponse:
    return MergeLogResponse(
        id=row.id,
        canonical_id=row.canonical_id,
        merged_id=row.merged_id,
        match_score=row.match_score,
        match_method=row.match_method,
        merged_at=row.merged_at,
        details=row.details,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/duplicates",
    response_model=ApiResponse[list[DuplicateCandidateResponse]],
    summary="列出重复候选材料",
    description=(
        "扫描 materials 表，按公式、模糊名、别名重叠三个策略找出可能的重复对。\n\n"
        "Scan the materials table for candidate duplicate pairs using three "
        "strategies: exact formula, fuzzy name (Levenshtein), and alias overlap."
    ),
)
async def list_duplicate_candidates(
    fuzzy_threshold: float = Query(
        0.85, ge=0.0, le=1.0, description="Fuzzy name match threshold"
    ),
    limit: int = Query(50, ge=1, le=500, description="Max candidate pairs"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[DuplicateCandidateResponse]]:
    """List candidate duplicate material pairs (read-only)."""
    candidates = await find_duplicates(
        db, fuzzy_threshold=fuzzy_threshold, limit=limit
    )
    return ApiResponse(
        success=True,
        data=[_candidate_to_response(c) for c in candidates],
    )


@router.post(
    "/merge",
    response_model=ApiResponse[MergeLogResponse],
    status_code=201,
    summary="记录材料合并决策",
    description=(
        "将 duplicate 合并入 canonical 并写入 entity_merge_log 审计行。\n\n"
        "Record a material merge decision -- writes a new row to "
        "``entity_merge_log`` capturing canonical/duplicate ids, the match "
        "method, score, and any matched aliases."
    ),
)
async def record_merge(
    payload: MergeRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MergeLogResponse]:
    """Record a merge decision in the audit log."""
    # Load both materials.
    from nfm_db.models.material import Material

    canonical_row = await db.get(Material, payload.canonical_id)
    duplicate_row = await db.get(Material, payload.duplicate_id)
    if canonical_row is None:
        raise HTTPException(status_code=404, detail="canonical material not found")
    if duplicate_row is None:
        raise HTTPException(status_code=404, detail="duplicate material not found")

    try:
        result: MergeResult = await execute_merge(
            db,
            canonical=canonical_row,
            duplicate=duplicate_row,
            match_score=payload.match_score,
            match_method=payload.match_method,
            matched_aliases=payload.matched_aliases,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(result.log)
    return ApiResponse(success=True, data=_log_to_response(result.log))


@router.get(
    "/logs",
    response_model=ApiResponse[PaginatedResponse[MergeLogResponse]],
    summary="查询合并审计日志",
    description=(
        "分页列出 entity_merge_log 审计行，按时间倒序。\n\n"
        "Paginated entity_merge_log audit trail, newest first."
    ),
)
async def list_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    match_method: MatchMethod | None = Query(
        None, description="Filter by match method"
    ),
    canonical_id: UUID | None = Query(
        None, description="Filter by canonical material id"
    ),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[MergeLogResponse]]:
    """List audit log rows with optional filters."""
    rows, total = await list_merge_logs(
        db,
        page=page,
        per_page=per_page,
        match_method=match_method,
        canonical_id=canonical_id,
    )
    pages = (total + per_page - 1) // per_page if total else 0
    return ApiResponse(
        success=True,
        data=PaginatedResponse[MergeLogResponse](
            items=[_log_to_response(r) for r in rows],
            total=total,
            page=page,
            limit=per_page,
            pages=pages,
        ),
    )


@router.get(
    "/logs/{log_id}",
    response_model=ApiResponse[MergeLogResponse],
    summary="查询单个合并审计行",
    description="Get a single audit log row by id.",
)
async def get_log(
    log_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MergeLogResponse]:
    """Fetch a single audit log row."""
    row = await get_merge_log(db, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="merge log not found")
    return ApiResponse(success=True, data=_log_to_response(row))
