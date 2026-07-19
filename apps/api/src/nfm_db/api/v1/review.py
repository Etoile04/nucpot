"""Review & Provenance API endpoints (Phase 3).

Cross-table review of extraction_results, kg_nodes, kg_edges, and
property_measurements with data traceability to source documents.

Endpoints:
- GET  /pending        — Pending review items (paginated, filterable by type)
- GET  /{id}/source   — Source provenance for a review item
- PATCH /{id}          — Update review status (with transition validation)
- POST /batch          — Batch review operation
- GET  /stats          — Review statistics across all tables
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_reviewer
from nfm_db.database import get_db
from nfm_db.models.extraction_result import ExtractionResult
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.property import PropertyMeasurement
from nfm_db.models.review import VALID_TRANSITIONS, ReviewStatus
from nfm_db.models.source import DataSource
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.review import (
    ReviewBatchRequest,
    ReviewBatchResponse,
    ReviewItemResponse,
    ReviewSourceInfo,
    ReviewStatsResponse,
    ReviewStatusUpdate,
    SourceProvenanceResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/review", tags=["评审管理"])

VALID_STATUSES = {s.value for s in ReviewStatus}

# Maps table name → item_type label for review items.
_TABLE_TYPE_MAP: dict[str, str] = {
    "extraction_results": "extraction",
    "kg_nodes": "node",
    "kg_edges": "edge",
    "property_measurements": "measurement",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_transition(current_status: str, new_status: str) -> None:
    """Validate that the status transition is allowed."""
    try:
        current = ReviewStatus(current_status)
        target = ReviewStatus(new_status)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {new_status}. Must be one of: {VALID_STATUSES}",
        )

    allowed = VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from {current_status} to {new_status}. "
            f"Allowed: {[s.value for s in allowed]}",
        )


async def _find_review_item(
    item_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Any, str]:
    """Find a review item across all 4 tables. Returns (row, table_name)."""
    for model, table_name in [
        (ExtractionResult, "extraction_results"),
        (KGNode, "kg_nodes"),
        (KGEdge, "kg_edges"),
        (PropertyMeasurement, "property_measurements"),
    ]:
        row = await db.get(model, item_id)
        if row is not None:
            return row, table_name
    raise HTTPException(status_code=404, detail="Review item not found")


def _row_to_review_item(row: Any, table_name: str) -> ReviewItemResponse:
    """Convert a DB row to a ReviewItemResponse."""
    source_info: ReviewSourceInfo | None = None
    if hasattr(row, "source_paragraph") and row.source_paragraph:
        source_info = ReviewSourceInfo(
            paragraph=row.source_paragraph,
            page=row.source_page,
            doi=row.source_doi,
        )

    item_data: dict[str, Any] = {}
    if table_name == "kg_nodes":
        item_data = {
            "label": row.label,
            "node_type": row.node_type,
            "properties": row.properties,
        }
    elif table_name == "kg_edges":
        item_data = {
            "relation_type": row.relation_type,
            "properties": row.properties,
        }
    elif table_name == "property_measurements":
        item_data = {
            "value_scalar": float(row.value_scalar) if row.value_scalar else None,
            "unit_id": str(row.unit_id) if row.unit_id else None,
            "notes": row.notes,
        }
    elif table_name == "extraction_results":
        item_data = row.item_data if row.item_data else {
            "property_name": row.property_name,
            "value": row.value,
        }

    return ReviewItemResponse(
        id=row.id,
        item_type=_TABLE_TYPE_MAP.get(table_name, table_name),
        item_data=item_data,
        confidence=getattr(row, "confidence", 0.0),
        review_status=getattr(row, "review_status", "pending"),
        source=source_info,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/pending",
    response_model=ApiResponse[PaginatedResponse[ReviewItemResponse]],
    summary="获取跨表待审核项列表",
    description="Return pending review items across all 4 tables with pagination.",
)
async def get_pending_reviews(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    item_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[ReviewItemResponse]]:
    """Return pending review items across all 4 tables with pagination."""
    tables_to_query: list[tuple[Any, str]] = [
        (ExtractionResult, "extraction_results"),
        (KGNode, "kg_nodes"),
        (KGEdge, "kg_edges"),
        (PropertyMeasurement, "property_measurements"),
    ]
    if item_type:
        type_to_table = {v: k for k, v in _TABLE_TYPE_MAP.items()}
        table_name = type_to_table.get(item_type)
        if table_name is None:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid item_type: {item_type}. "
                f"Must be one of: {list(type_to_table.keys())}",
            )
        tables_to_query = [(m, t) for m, t in tables_to_query if t == table_name]

    all_items: list[ReviewItemResponse] = []
    for model, table_name in tables_to_query:
        stmt = (
            select(model)
            .where(model.review_status == ReviewStatus.PENDING.value)
            .order_by(model.created_at.desc())
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        for row in rows:
            all_items.append(_row_to_review_item(row, table_name))

    # Sort by created_at desc (stable across tables).
    all_items.sort(key=lambda x: x.created_at, reverse=True)

    total = len(all_items)
    pages = max(1, math.ceil(total / limit))
    start = (page - 1) * limit
    page_items = all_items[start : start + limit]

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=page_items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
    )


@router.get(
    "/{item_id}/source",
    response_model=ApiResponse[SourceProvenanceResponse],
    summary="获取审核项数据溯源",
    description="Return the source text, page, DOI, and metadata for a review item.",
)
async def get_review_source(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SourceProvenanceResponse]:
    """Return the source text, page, DOI, and metadata for a review item."""
    row, table_name = await _find_review_item(item_id, db)

    source_id = getattr(row, "source_id", None)
    source_title = None
    journal = None
    year = None

    if source_id is not None:
        ds = await db.get(DataSource, source_id)
        if ds is not None:
            source_title = ds.title
            journal = getattr(ds, "journal", None)
            year = getattr(ds, "year", None)

    paragraph = getattr(row, "source_paragraph", None)
    page = getattr(row, "source_page", None)
    doi = getattr(row, "source_doi", None)

    return ApiResponse(
        success=True,
        data=SourceProvenanceResponse(
            paragraph=paragraph,
            page=page,
            doi=doi,
            source_title=source_title,
            journal=journal,
            year=year,
        ),
    )


@router.patch(
    "/{item_id}",
    response_model=ApiResponse[ReviewItemResponse],
    summary="更新审核项状态",
    description="Approve, reject, or request revision on a review item.",
)
async def update_review_status(
    item_id: uuid.UUID,
    body: ReviewStatusUpdate,
    current_user: Annotated[User, Depends(require_reviewer)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewItemResponse]:
    """Approve, reject, or request revision on a review item."""
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {body.status}. Must be one of: {VALID_STATUSES}",
        )

    row, table_name = await _find_review_item(item_id, db)

    # Validate state machine transition.
    _validate_transition(row.review_status, body.status)

    row.review_status = body.status
    row.review_note = body.note
    row.reviewed_by = str(current_user.id) if current_user else None
    row.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(row)

    return ApiResponse(
        success=True,
        data=_row_to_review_item(row, table_name),
    )


@router.post(
    "/batch",
    response_model=ApiResponse[ReviewBatchResponse],
    summary="批量更新审核项状态",
    description="Update multiple review items in a single request.",
)
async def batch_review(
    body: ReviewBatchRequest,
    current_user: Annotated[User, Depends(require_reviewer)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewBatchResponse]:
    """Update multiple review items in a single request."""
    succeeded = 0
    failed = 0
    errors: list[dict[str, Any]] = []

    for item in body.items:
        if item.status not in VALID_STATUSES:
            failed += 1
            errors.append(
                {"id": str(item.id), "error": f"Invalid status: {item.status}"}
            )
            continue

        try:
            row, _ = await _find_review_item(item.id, db)
        except HTTPException as exc:
            failed += 1
            errors.append({"id": str(item.id), "error": exc.detail})
            continue

        try:
            _validate_transition(row.review_status, item.status)
        except HTTPException as exc:
            failed += 1
            errors.append({"id": str(item.id), "error": exc.detail})
            continue

        row.review_status = item.status
        row.review_note = item.note
        row.reviewed_by = str(current_user.id) if current_user else None
        row.reviewed_at = datetime.now(UTC)
        succeeded += 1

    await db.commit()

    return ApiResponse(
        success=True,
        data=ReviewBatchResponse(
            succeeded=succeeded,
            failed=failed,
            errors=errors,
        ),
    )


@router.get(
    "/stats",
    response_model=ApiResponse[ReviewStatsResponse],
    summary="获取跨表审核统计",
    description="Return counts of review items grouped by status across all 4 tables.",
)
async def get_review_stats(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewStatsResponse]:
    """Return counts of review items grouped by status across all 4 tables."""
    stats = ReviewStatsResponse()

    for model in [
        ExtractionResult,
        KGNode,
        KGEdge,
        PropertyMeasurement,
    ]:
        for status in ReviewStatus:
            stmt = select(func.count()).where(
                model.review_status == status.value,
            )
            result = await db.execute(stmt)
            count = result.scalar() or 0
            setattr(stats, status.value, getattr(stats, status.value) + count)

    return ApiResponse(success=True, data=stats)
