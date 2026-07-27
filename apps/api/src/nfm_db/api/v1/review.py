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
from nfm_db.models.review import VALID_TRANSITIONS, Review, ReviewStatus
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


def _as_aware(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive datetime (SQLite round-trip safety)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


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


def _create_correction_audit(
    item_id: uuid.UUID,
    table_name: str,
    action: str,
    previous_status: str,
    previous_reviewed_at: datetime | None,
    reviewer_id: str | None,
    note: str,
) -> Review:
    """Create a Review audit record for a feedback-loop event with loop time.

    ``action`` is the reviewer-initiated status that triggered the audit
    row (e.g. ``needs_revision`` or ``corrected``). Both are valid
    feedback-loop signals from the reviewer's perspective: a request
    for revision is a feedback event, and a completed correction is the
    resolution of that feedback. NFM-1875 audit — the UI exposes only
    the needs_revision step today, but the corrected path is preserved
    for future UI work.
    """
    loop_time = (
        datetime.now(UTC) - _as_aware(previous_reviewed_at)
        if previous_reviewed_at is not None
        else None
    )
    return Review(
        result_id=item_id,
        reviewer_id=reviewer_id,
        action=action,
        comment=note,
        data={
            "table": table_name,
            "loop_time_seconds": loop_time.total_seconds() if loop_time else None,
            "previous_status": previous_status,
        },
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
    status: str = Query("pending", description="Review status filter"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[ReviewItemResponse]]:
    """Return review items across all 4 tables with pagination, filtered by status."""
    try:
        status_value = ReviewStatus(status).value
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {status}. Must be one of: {VALID_STATUSES}",
        )

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

    # Count total items matching the status filter across queried tables.
    total = 0
    for model, _ in tables_to_query:
        count_stmt = select(func.count()).where(
            model.review_status == status_value,
        )
        count_result = await db.execute(count_stmt)
        total += count_result.scalar() or 0

    pages = max(1, math.ceil(total / limit))
    # DB-level pagination: fetch at most `limit * 2` rows from each table
    # (overscan to handle cross-table ordering), then merge-sort in Python.
    # This avoids loading ALL rows into memory for large datasets.
    fetch_per_table = min(limit * 2, 100) if len(tables_to_query) > 1 else limit
    all_items: list[ReviewItemResponse] = []
    for model, table_name in tables_to_query:
        stmt = (
            select(model)
            .where(model.review_status == status_value)
            .order_by(model.created_at.desc())
            .limit(fetch_per_table)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        for row in rows:
            all_items.append(_row_to_review_item(row, table_name))

    # Sort by created_at desc (stable across tables).
    all_items.sort(key=lambda x: x.created_at, reverse=True)

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
    ds: DataSource | None = None

    if source_id is not None:
        ds = await db.get(DataSource, source_id)
        if ds is not None:
            source_title = ds.title
            journal = getattr(ds, "journal", None)
            year = getattr(ds, "year", None)

    paragraph = getattr(row, "source_paragraph", None)
    page = getattr(row, "source_page", None)
    doi = getattr(row, "source_doi", None)

    # Fallback for tables that don't store source_paragraph directly
    # (kg_nodes, kg_edges, property_measurements): derive paragraph from
    # the linked DataSource.content_md by matching the entity's primary
    # label/value/property text. Keeps Phase 3 traceability working for
    # existing seed data that predates the source-column migration.
    if paragraph is None and source_id is not None and ds is not None:
        full_markdown = getattr(ds, "content_md", None)
        if full_markdown:
            # For kg_edges, the row alone lacks value-bearing fields:
            # - ``relation_type`` ("hasProperty") is a generic ontology
            #   label that never appears in real literature text
            # - ``properties.value`` / ``properties.label`` are usually empty
            #   for edges (they only carry metadata like extraction_method)
            # So we look up the labels of the connected source/target nodes
            # and use those as the keywords (e.g. "UO2", "Melting Point")
            # which DO appear in the source text.
            extra_keywords: list[str] | None = None
            if table_name == "kg_edges":
                src_id = getattr(row, "source_node_id", None)
                tgt_id = getattr(row, "target_node_id", None)
                edge_labels: list[str] = []
                if src_id is not None:
                    src_node = await db.get(KGNode, src_id)
                    if src_node is not None and src_node.label:
                        edge_labels.append(src_node.label.strip())
                if tgt_id is not None:
                    tgt_node = await db.get(KGNode, tgt_id)
                    if tgt_node is not None and tgt_node.label:
                        edge_labels.append(tgt_node.label.strip())
                if edge_labels:
                    # Order: target (usually the specific property) first,
                    # then source (usually the generic material). This way
                    # the more distinctive keyword has a chance to match
                    # before the more generic one triggers a Figure caption
                    # hit.
                    extra_keywords = list(reversed(edge_labels))
            derived = _derive_paragraph(
                full_markdown, row, table_name, extra_keywords=extra_keywords,
            )
            if derived:
                paragraph = derived
            if doi is None:
                doi = getattr(ds, "doi", None)

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


def _derive_paragraph(
    content_md: str,
    row: Any,
    table_name: str,
    extra_keywords: list[str] | None = None,
) -> str | None:
    """Extract the most relevant paragraph from ``content_md``.

    The match strategy prefers the most distinctive field first (numeric
    value → property string fields → label), so we don't anchor on common
    words like "UO2" or "Material".

    ``extra_keywords`` are prepended to the keyword list, so they are tried
    first. Used to pass labels of related entities (e.g. for ``kg_edges``,
    the labels of the connected source/target nodes) when the row itself
    lacks distinctive value-bearing fields.

    Returns ``None`` when no good match is found.
    """
    if not content_md:
        return None

    # ``extra_keywords`` are APPENDED to the keyword list (tried last) so
    # that the row's own value-bearing fields — which are usually more
    # distinctive — are tried first. Without this ordering, a generic
    # label like "UO2" or "Material" can hit a Figure caption or table
    # header before the precise keyword (e.g. "activation_energy") ever
    # has a chance to match. Used to pass labels of related entities
    # (e.g. for ``kg_edges``, the labels of the connected source/target
    # nodes) when the row itself lacks distinctive value-bearing fields.
    keywords: list[str] = []
    if table_name == "kg_nodes":
        label = (getattr(row, "label", None) or "").strip()
        props = getattr(row, "properties", None) or {}
        if isinstance(props, dict):
            # kg_nodes properties vary — try common value-bearing keys first
            for k in ("value", "condition_value", "label", "name"):
                v = props.get(k)
                if v is not None:
                    keywords.append(str(v).strip())
            # Also try condition_key as a last-ditch anchor
            ck = props.get("condition_key")
            if ck:
                keywords.append(str(ck).strip())
        if label:
            keywords.append(label)
    elif table_name == "kg_edges":
        rt = (getattr(row, "relation_type", None) or "").strip()
        if rt:
            keywords.append(rt)
        props = getattr(row, "properties", None) or {}
        if isinstance(props, dict):
            for k in ("value", "label"):
                v = props.get(k)
                if v is not None:
                    keywords.append(str(v).strip())
    elif table_name == "property_measurements":
        val = getattr(row, "value_scalar", None)
        notes = getattr(row, "notes", None)
        if val is not None:
            keywords.append(str(val))
        if notes:
            keywords.append(notes.strip())
    elif table_name == "extraction_results":
        item_data = getattr(row, "item_data", None) or {}
        if isinstance(item_data, dict):
            for k in ("value", "property_name", "label"):
                v = item_data.get(k)
                if v is not None:
                    keywords.append(str(v).strip())
        prop_name = (getattr(row, "property_name", None) or "").strip()
        if prop_name:
            keywords.append(prop_name)

    lines = [ln.strip() for ln in content_md.splitlines() if ln.strip()]
    # Append caller-provided extra keywords last (tried after the row's
    # own fields, which are usually more distinctive — see note above).
    if extra_keywords:
        keywords.extend(k for k in extra_keywords if k and len(k) >= 2)
    for keyword in keywords:
        if not keyword or len(keyword) < 2:
            continue
        lower_kw = keyword.lower()
        for i, line in enumerate(lines):
            if lower_kw in line.lower():
                return line if i == 0 else f"{lines[i-1]}\n{line}"
    return lines[0] if lines else None


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

    previous_status = row.review_status
    previous_reviewed_at = row.reviewed_at
    row.review_status = body.status
    row.review_note = body.note
    row.reviewed_by = str(current_user.id) if current_user else None
    row.reviewed_at = datetime.now(UTC)

    # Write a Review audit record for any reviewer-initiated feedback signal
    # (correction, or a needs-revision request when the UI does not yet
    # expose the second-step 'corrected' transition). The audit row records
    # the feedback loop time (from the previous reviewed_at to now) so the
    # /feedback-metrics endpoint can report real numbers. This is the
    # minimum-change fix for NFM-1875 (feedback loop was not closing
    # because the UI only ever sent approve|reject|needs_revision, never
    # 'corrected', so the original CORRECTED-only audit branch never
    # fired — see NFM-1875 audit).
    if body.status in (
        ReviewStatus.CORRECTED.value,
        ReviewStatus.NEEDS_REVISION.value,
    ):
        db.add(_create_correction_audit(
            item_id, table_name, body.status, previous_status,
            previous_reviewed_at,
            str(current_user.id) if current_user else None,
            body.note or "",
        ))

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
            row, item_table_name = await _find_review_item(item.id, db)
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

        previous_status = row.review_status
        previous_reviewed_at = row.reviewed_at
        row.review_status = item.status
        row.review_note = item.note
        row.reviewed_by = str(current_user.id) if current_user else None
        row.reviewed_at = datetime.now(UTC)
        succeeded += 1

        # Audit record for any reviewer-initiated feedback signal (see
        # NFM-1875 audit — UI only sends needs_revision, not corrected).
        if item.status in (
            ReviewStatus.CORRECTED.value,
            ReviewStatus.NEEDS_REVISION.value,
        ):
            db.add(_create_correction_audit(
                item.id, item_table_name, item.status, previous_status,
                previous_reviewed_at,
                str(current_user.id) if current_user else None,
                item.note or "",
            ))

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
    "/feedback-metrics",
    response_model=ApiResponse[dict],
    summary="获取反馈闭环指标",
    description="Return average feedback loop time and correction count from audit records.",
)
async def get_feedback_metrics(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Return feedback loop metrics computed from Review audit records.

    Counts both ``corrected`` and ``needs_revision`` audit rows: the
    former represents a full correction cycle, the latter is the
    reviewer-flagging step that the current UI exposes (NFM-1875 audit).
    Both are valid feedback-loop signals from the reviewer's perspective.
    """
    stmt = select(Review).where(
        Review.action.in_([
            ReviewStatus.CORRECTED.value,
            ReviewStatus.NEEDS_REVISION.value,
        ])
    )
    result = await db.execute(stmt)
    reviews = result.scalars().all()

    loop_times: list[float] = []
    for r in reviews:
        lt = r.data.get("loop_time_seconds") if r.data else None
        if lt is not None:
            loop_times.append(float(lt))

    return ApiResponse(
        success=True,
        data={
            "total_corrections": len(reviews),
            "avg_loop_time_hours": (
                sum(loop_times) / len(loop_times) / 3600 if loop_times else None
            ),
            "max_loop_time_hours": max(loop_times) / 3600 if loop_times else None,
        },
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

    # Calculate overall adoption rate: corrected / (corrected + rejected)
    total_reviewed = (
        stats.approved
        + stats.rejected
        + stats.needs_revision
        + stats.corrected
    )
    corrected_or_rejected = stats.corrected + stats.rejected
    stats.total_reviewed = total_reviewed
    stats.adoption_rate = (
        (stats.corrected / corrected_or_rejected)
        if corrected_or_rejected > 0
        else None
    )

    # Build per-type stats (adoption rate per item_type)
    type_map_reverse = {
        "extraction_results": "extraction",
        "kg_nodes": "node",
        "kg_edges": "edge",
        "property_measurements": "measurement",
    }
    by_type: dict[str, dict[str, object]] = {}
    for model, tbl in [
        (ExtractionResult, "extraction_results"),
        (KGNode, "kg_nodes"),
        (KGEdge, "kg_edges"),
        (PropertyMeasurement, "property_measurements"),
    ]:
        type_label = type_map_reverse[tbl]
        if type_label not in by_type:
            by_type[type_label] = {"total": 0, "corrected": 0, "rejected": 0, "approved": 0}
        # Single GROUP BY query instead of 3 separate count queries
        group_stmt = (
            select(model.review_status, func.count())
            .where(model.review_status.in_(["approved", "rejected", "corrected"]))
            .group_by(model.review_status)
        )
        group_result = await db.execute(group_stmt)
        for status_val, count in group_result:
            by_type[type_label][status_val] = count  # type: ignore[literal-required]
            by_type[type_label]["total"] += count  # type: ignore[operator]
        # Calculate adoption rate for this type
        cr = by_type[type_label]["corrected"] + by_type[type_label]["rejected"]  # type: ignore[operator]
        by_type[type_label]["adoption_rate"] = (
            (by_type[type_label]["corrected"] / cr) if cr > 0 else None  # type: ignore[operator]
        )

    stats.by_type = by_type

    return ApiResponse(success=True, data=stats)
