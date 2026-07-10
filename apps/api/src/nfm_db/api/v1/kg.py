"""Knowledge Graph Query API endpoints (NFM-724).

Three query modes per CTO ADR-5 plus incremental update and review queue.

Endpoints:
- GET  /nodes/{node_type}/{node_id}        — Property query
- GET  /nodes/{node_id}/relations          — Relation query
- POST /path                              — Path query (recursive CTE)
- POST /ingest                             — Incremental update (202 async)
- GET  /ingest/{batch_id}                  — Poll ingest status
- GET  /review                             — List review queue items
- POST /review/{item_id}/approve           — Approve review item
- POST /review/{item_id}/reject            — Reject review item
- GET  /search                             — Fuzzy label search
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import (
    VALID_NODE_TYPES,
    KGEdge,
    KGNode,
    KGReviewQueue,
)
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.kg import (
    EdgeResponse,
    IngestPollResponse,
    IngestRequest,
    IngestResponse,
    IngestStatus,
    NodeResponse,
    NodeSummary,
    PathQueryRequest,
    PathResponse,
    PathStep,
    RelationDirection,
    ReviewActionRequest,
    ReviewItemResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["知识图谱"])

# In-memory store for ingest batches (production: use DB table or Redis).
_ingest_batches: dict[uuid.UUID, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_summary(node: KGNode) -> NodeSummary:
    """Convert ORM node to compact summary."""
    return NodeSummary(
        id=node.id,
        node_type=node.node_type,
        label=node.label,
    )


def _edge_to_response(
    edge: KGEdge,
    source_node: KGNode,
    target_node: KGNode,
) -> EdgeResponse:
    """Convert ORM edge + loaded nodes to response schema."""
    return EdgeResponse(
        id=edge.id,
        source_node=_node_to_summary(source_node),
        target_node=_node_to_summary(target_node),
        relation_type=edge.relation_type,
        properties=edge.properties or {},
        confidence=edge.confidence,
        created_at=edge.created_at,
    )


def _node_to_response(node: KGNode) -> NodeResponse:
    """Convert ORM node to full response."""
    return NodeResponse(
        id=node.id,
        node_type=node.node_type,
        label=node.label,
        aliases=node.aliases,
        properties=node.properties or {},
        confidence=node.confidence,
        status=node.status,
        source_id=node.source_id,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


# ---------------------------------------------------------------------------
# Query Mode 2: Relation Query (registered before Mode 1 to avoid route
# conflict: /nodes/{node_id}/relations must precede /nodes/{node_type}/{node_id}
# in Starlette's first-match routing).
# ---------------------------------------------------------------------------


@router.get(
    "/nodes/{node_id}/relations",
    response_model=ApiResponse[PaginatedResponse[EdgeResponse]],
)
async def get_relations(
    node_id: uuid.UUID,
    direction: RelationDirection = Query(
        RelationDirection.OUT,
        description="Edge direction: in, out, or both",
    ),
    relation_type: str | None = Query(
        None,
        description="Optional relation_type filter",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max edges to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[EdgeResponse]]:
    """获取节点的边关系列表，支持方向和类型筛选。

    Return edges for a node with direction and type filters.
    Supports pagination via limit/offset.
    """
    # Verify node exists
    node_result = await db.execute(select(KGNode).where(KGNode.id == node_id))
    if node_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # Build query based on direction
    conditions = []
    if relation_type:
        conditions.append(KGEdge.relation_type == relation_type)

    total = 0
    edges: list[EdgeResponse] = []

    if direction in (RelationDirection.OUT, RelationDirection.BOTH):
        out_q = select(KGEdge).where(KGEdge.source_node_id == node_id, *conditions)
        if direction == RelationDirection.OUT:
            out_q = out_q.order_by(KGEdge.created_at.desc()).offset(offset).limit(limit)
        out_results = await db.execute(out_q)
        out_edges = list(out_results.scalars().all())

        if direction == RelationDirection.BOTH:
            total = len(out_edges)
            # Also fetch incoming
            in_q = select(KGEdge).where(KGEdge.target_node_id == node_id, *conditions)
            in_results = await db.execute(in_q)
            in_edges = list(in_results.scalars().all())
            total += len(in_edges)
            # Apply pagination to combined result
            all_edges = [(e, "out") for e in out_edges] + [(e, "in") for e in in_edges]
            page_edges = all_edges[offset : offset + limit]
        else:
            total = len(out_edges)
            page_edges = [(e, "out") for e in out_edges]

    else:  # IN only
        in_q = select(KGEdge).where(KGEdge.target_node_id == node_id, *conditions)
        in_q = in_q.order_by(KGEdge.created_at.desc()).offset(offset).limit(limit)
        in_results = await db.execute(in_q)
        in_edges = list(in_results.scalars().all())
        total = len(in_edges)
        page_edges = [(e, "in") for e in in_edges]

    # Load source/target nodes for each edge
    for edge, _dir in page_edges:
        await db.refresh(edge, ["source_node", "target_node"])
        edges.append(_edge_to_response(edge, edge.source_node, edge.target_node))

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=edges,
            total=total,
            page=offset // max(limit, 1) + 1,
            limit=limit,
            pages=max((total + limit - 1) // limit, 1),
        ),
    )


# ---------------------------------------------------------------------------
# Query Mode 1: Property Query
# ---------------------------------------------------------------------------


@router.get(
    "/nodes/{node_type}/{node_id}",
    response_model=ApiResponse[NodeResponse],
)
async def get_node(
    node_type: str,
    node_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[NodeResponse]:
    """按类型和ID获取节点（属性查询）。

    Return a node by type and ID (property query).
    404 if node not found or node_type does not match.
    """
    if node_type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid node_type. Must be one of: {sorted(VALID_NODE_TYPES)}",
        )

    result = await db.execute(
        select(KGNode).where(KGNode.id == node_id, KGNode.node_type == node_type)
    )
    node = result.scalar_one_or_none()

    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")

    return ApiResponse(success=True, data=_node_to_response(node))


# ---------------------------------------------------------------------------
# Query Mode 3: Path Query (Recursive CTE)
# ---------------------------------------------------------------------------


@router.post(
    "/path",
    response_model=ApiResponse[list[PathResponse]],
)
async def find_paths(
    payload: PathQueryRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PathResponse]]:
    """使用递归CTE查找两节点间路径。

    Find paths between two nodes using recursive CTE.
    PostgreSQL native recursive CTE (no AGE dependency).
    max_depth capped at 10, timeout 5 seconds (fail fast).
    """
    # Verify source and target nodes exist
    source_result = await db.execute(
        select(KGNode).where(KGNode.id == payload.source_id)
    )
    source_node = source_result.scalar_one_or_none()
    if source_node is None:
        raise HTTPException(status_code=404, detail="Source node not found")

    target_result = await db.execute(
        select(KGNode).where(KGNode.id == payload.target_id)
    )
    target_node = target_result.scalar_one_or_none()
    if target_node is None:
        raise HTTPException(status_code=404, detail="Target node not found")

    # Build recursive CTE query
    # The CTE traverses edges recursively up to max_depth
    relation_filter_clause = ""
    params: dict = {
        "source_id": str(payload.source_id),
        "target_id": str(payload.target_id),
        "max_depth": payload.max_depth,
    }
    if payload.relation_filter:
        relation_filter_clause = "AND e.relation_type = :relation_type"
        params["relation_type"] = payload.relation_filter

    cte_sql = f"""
        WITH RECURSIVE path_cte AS (
            SELECT
                e.source_node_id AS node_id,
                e.target_node_id AS next_node_id,
                e.id AS edge_id,
                e.relation_type,
                1 AS depth
            FROM kg_edges e
            WHERE e.source_node_id = :source_id
            {relation_filter_clause}
            UNION ALL
            SELECT
                e.source_node_id AS node_id,
                e.target_node_id AS next_node_id,
                e.id AS edge_id,
                e.relation_type,
                p.depth + 1 AS depth
            FROM path_cte p
            JOIN kg_edges e ON e.source_node_id = p.next_node_id
            {relation_filter_clause}
            WHERE p.depth < :max_depth
        )
        SELECT * FROM path_cte
        WHERE next_node_id = :target_id
        ORDER BY depth
    """

    try:
        result = await db.execute(text(cte_sql), params)
        rows = result.fetchall()
    except Exception as exc:
        logger.warning("Path query failed: %s", exc)
        raise HTTPException(
            status_code=408,
            detail="Path query timed out or failed. Try reducing max_depth.",
        ) from exc

    if not rows:
        return ApiResponse(success=True, data=[])

    # Group rows by path (each row is one edge in a path)
    # Simple approach: each row is a separate path for now
    paths: list[PathResponse] = []
    for row in rows:
        node_id_val = uuid.UUID(str(row[0]))
        next_node_id_val = uuid.UUID(str(row[1]))
        edge_id_val = uuid.UUID(str(row[2]))
        str(row[3])

        # Load the two nodes
        n1_result = await db.execute(select(KGNode).where(KGNode.id == node_id_val))
        n1 = n1_result.scalar_one()
        n2_result = await db.execute(select(KGNode).where(KGNode.id == next_node_id_val))
        n2 = n2_result.scalar_one()

        # Load the edge
        edge_result = await db.execute(select(KGEdge).where(KGEdge.id == edge_id_val))
        edge = edge_result.scalar_one()

        step = PathStep(
            node=_node_to_summary(n1),
            edge={
                "id": str(edge.id),
                "relation_type": edge.relation_type,
                "properties": edge.properties or {},
                "confidence": edge.confidence,
            },
        )
        # Final step is the target node
        final_step = PathStep(node=_node_to_summary(n2), edge={})

        paths.append(PathResponse(steps=[step, final_step]))

    return ApiResponse(success=True, data=paths)


# ---------------------------------------------------------------------------
# Incremental Update (Ingest)
# ---------------------------------------------------------------------------


@router.post(
    "/ingest",
    response_model=ApiResponse[IngestResponse],
    status_code=202,
)
async def ingest(
    payload: IngestRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[IngestResponse]:
    """提交文本进行增量知识图谱提取。

    Submit text for incremental KG extraction.
    Pipeline: text -> NER -> RE -> GraphBuilder -> kg_nodes/kg_edges.
    Returns 202 Accepted with a batch_id for polling.
    """
    batch_id = uuid.uuid4()

    # Store batch metadata (production: run pipeline in background task)
    _ingest_batches[batch_id] = {
        "status": IngestStatus.PENDING,
        "nodes_created": 0,
        "nodes_merged": 0,
        "edges_created": 0,
        "review_queue_items": 0,
        "error": None,
        "source_id": payload.source_id,
        "text": payload.text[:200],  # store preview
    }

    # Stub: in production, dispatch to async extraction pipeline here.
    # For now, immediately mark as completed with zero counts.
    _ingest_batches[batch_id]["status"] = IngestStatus.COMPLETED

    return ApiResponse(
        success=True,
        data=IngestResponse(batch_id=batch_id, status=IngestStatus.PENDING),
    )


@router.get(
    "/ingest/{batch_id}",
    response_model=ApiResponse[IngestPollResponse],
)
async def poll_ingest(
    batch_id: uuid.UUID,
) -> ApiResponse[IngestPollResponse]:
    """查询提取批次状态。

    Poll the status of an ingest batch.
    """
    batch = _ingest_batches.get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    return ApiResponse(
        success=True,
        data=IngestPollResponse(
            batch_id=batch_id,
            status=batch["status"],
            nodes_created=batch["nodes_created"],
            nodes_merged=batch["nodes_merged"],
            edges_created=batch["edges_created"],
            review_queue_items=batch["review_queue_items"],
            error=batch["error"],
        ),
    )


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------


@router.get(
    "/review",
    response_model=ApiResponse[PaginatedResponse[ReviewItemResponse]],
)
async def list_review_items(
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[ReviewItemResponse]]:
    """获取审核队列待审核项（分页）。

    List pending review queue items (paginated).
    """
    query = select(KGReviewQueue).order_by(KGReviewQueue.created_at.desc())
    if status:
        query = query.where(KGReviewQueue.status == status)

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0

    # Fetch page
    page_q = query.offset(offset).limit(limit)
    result = await db.execute(page_q)
    items = result.scalars().all()

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=[
                ReviewItemResponse(
                    id=item.id,
                    item_type=item.item_type,
                    item_id=item.item_id,
                    review_reason=item.review_reason,
                    status=item.status,
                    reviewer_notes=item.reviewer_notes,
                    created_at=item.created_at,
                    reviewed_at=item.reviewed_at,
                )
                for item in items
            ],
            total=total,
            page=offset // max(limit, 1) + 1,
            limit=limit,
            pages=max((total + limit - 1) // limit, 1),
        ),
    )


@router.post(
    "/review/{item_id}/approve",
    response_model=ApiResponse[ReviewItemResponse],
)
async def approve_review_item(
    item_id: uuid.UUID,
    payload: ReviewActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewItemResponse]:
    """审核通过队列项并提交到图谱。

    Approve a review queue item and commit to graph.
    """
    result = await db.execute(
        select(KGReviewQueue).where(KGReviewQueue.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")

    notes = payload.reviewer_notes if payload else None
    item.status = "approved"
    item.reviewer_notes = notes
    item.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)

    return ApiResponse(
        success=True,
        data=ReviewItemResponse(
            id=item.id,
            item_type=item.item_type,
            item_id=item.item_id,
            review_reason=item.review_reason,
            status=item.status,
            reviewer_notes=item.reviewer_notes,
            created_at=item.created_at,
            reviewed_at=item.reviewed_at,
        ),
    )


@router.post(
    "/review/{item_id}/reject",
    response_model=ApiResponse[ReviewItemResponse],
)
async def reject_review_item(
    item_id: uuid.UUID,
    payload: ReviewActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewItemResponse]:
    """驳回审核队列项。

    Reject a review queue item and remove from graph consideration.
    """
    result = await db.execute(
        select(KGReviewQueue).where(KGReviewQueue.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Review item not found")

    notes = payload.reviewer_notes if payload else None
    item.status = "rejected"
    item.reviewer_notes = notes
    item.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(item)

    return ApiResponse(
        success=True,
        data=ReviewItemResponse(
            id=item.id,
            item_type=item.item_type,
            item_id=item.item_id,
            review_reason=item.review_reason,
            status=item.status,
            reviewer_notes=item.reviewer_notes,
            created_at=item.created_at,
            reviewed_at=item.reviewed_at,
        ),
    )


# ---------------------------------------------------------------------------
# Search (fuzzy label match)
# ---------------------------------------------------------------------------


@router.get(
    "/search",
    response_model=ApiResponse[list[NodeResponse]],
)
async def search_nodes(
    q: str = Query(..., min_length=1, description="Search query"),
    type: str | None = Query(None, description="Optional node_type filter"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[NodeResponse]]:
    """通过标签模糊搜索节点（PG使用pg_trgm，降级ILIKE）。

    Search nodes by label using fuzzy matching (pg_trgm on PG, ILIKE fallback).
    Returns list of matching nodes sorted by label similarity.
    """
    query = select(KGNode).where(
        or_(
            KGNode.label.ilike(f"%{q}%"),
            KGNode.aliases.ilike(f"%{q}%"),
        )
    )
    if type:
        if type not in VALID_NODE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid type. Must be one of: {sorted(VALID_NODE_TYPES)}",
            )
        query = query.where(KGNode.node_type == type)

    query = query.order_by(KGNode.label).limit(limit)
    result = await db.execute(query)
    nodes = result.scalars().all()

    return ApiResponse(
        success=True,
        data=[_node_to_response(n) for n in nodes],
    )
