"""Knowledge Graph search, semantic query, and review queue endpoints.

Search (NFM-1166, NFM-1222):
  ``GET /api/v1/kg/search`` provides paginated, filterable search over KG nodes.
  When ``mode=lightrag`` is specified, the query is routed through the LightRAG
  semantic query bridge instead of the standard ILIKE search.
  Public read-only endpoint (no auth required).

Review queue (NFM-859):
  - GET  /kg/review/queue          — list pending review items
  - POST /kg/review/{id}/approve   — approve and add to KG
  - POST /kg/review/{id}/reject    — reject with reason
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.api.v1.auth import require_reviewer
from nfm_db.database import get_db
from nfm_db.models.kg import VALID_NODE_TYPES, KGEdge, KGNode
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginationParams
from nfm_db.schemas.kg import (
    GraphEdgeItem,
    GraphNodeItem,
    KGGraphResponse,
    KGNodeDetail,
    KGRelationsResponse,
    KGSearchItem,
    KGSearchResponse,
    RelationEdgeItem,
    SemanticQueryResponse,
)
from nfm_db.services.kg_utils import parse_aliases
from nfm_db.services.review_queue_service import (
    approve_review_item,
    list_pending_reviews,
    reject_review_item,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["知识图谱"])


@router.get(
    "/kg/search",
    summary="搜索知识图谱节点",
    description="支持关键词模糊搜索和LightRAG语义查询的知识图谱节点搜索接口。\n\nSearch KG nodes with ILIKE fuzzy match or LightRAG semantic query.",
)
async def search_kg_nodes(
    q: str | None = Query(default=None, description="Search term (ILIKE on label + aliases)"),
    type: str | None = Query(default=None, description="Filter by node_type"),
    status: str = Query(default="active", description="Filter by status"),
    mode: str | None = Query(
        default=None,
        description="Query mode: omit or 'structured' for ILIKE search, 'lightrag' for semantic query",
    ),
    pagination: PaginationParams = Depends(PaginationParams),
    _offset: int | None = Query(
        default=None, ge=0, alias="offset", deprecated=True, description="已弃用: 请使用 page 参数"
    ),
    _limit: int | None = Query(
        default=None,
        ge=1,
        le=100,
        alias="limit",
        deprecated=True,
        description="已弃用: 请使用 per_page 参数",
    ),
    session: AsyncSession = Depends(get_db),
) -> KGSearchResponse | SemanticQueryResponse:
    """Search Knowledge Graph nodes with optional filters.

    When ``mode=lightrag``, the query is routed through the LightRAG
    semantic query bridge.  When LightRAG is unavailable, the endpoint
    falls back to the standard structured (ILIKE) search automatically.

    Returns paginated results matching the given criteria.
    Defaults to active nodes only.
    """
    # Resolve effective pagination (supports new page/per_page + deprecated limit/offset aliases)
    if _limit is not None:
        effective_page = ((_offset or 0) // _limit) + 1
        pagination = PaginationParams(page=effective_page, per_page=_limit)
    effective_limit = _limit if _limit is not None else pagination.per_page
    effective_offset = _offset if _offset is not None else pagination.offset

    # Semantic query bridge (NFM-1222)
    if mode == "lightrag" and q is not None:
        return await _semantic_query(q=q, limit=effective_limit, session=session)

    # Standard structured search (existing logic + merged pagination)
    return await _structured_search(
        q=q,
        type=type,
        status=status,
        limit=effective_limit,
        offset=effective_offset,
        session=session,
    )


def _build_search_item(node: KGNode) -> KGSearchItem:
    """Map a KGNode ORM row to a KGSearchItem Pydantic schema."""
    return KGSearchItem(
        id=str(node.id),
        node_type=node.node_type,
        label=node.label,
        aliases=parse_aliases(node.aliases),
        properties=node.properties or {},
        confidence=node.confidence,
        status=node.status,
        source_id=str(node.source_id) if node.source_id else None,
    )


def _build_node_detail(node: KGNode) -> KGNodeDetail:
    """Map a KGNode ORM row to a KGNodeDetail Pydantic schema."""
    return KGNodeDetail(
        id=str(node.id),
        node_type=node.node_type,
        label=node.label,
        aliases=parse_aliases(node.aliases),
        properties=node.properties or {},
        confidence=node.confidence,
        status=node.status,
        source_id=str(node.source_id) if node.source_id else None,
    )


def _build_relation_edge(edge: KGEdge) -> RelationEdgeItem:
    """Map a KGEdge ORM row to a RelationEdgeItem, eager-loading both endpoints."""
    return RelationEdgeItem(
        id=str(edge.id),
        relation_type=edge.relation_type,
        confidence=edge.confidence,
        properties=edge.properties or {},
        source_node=_build_search_item(edge.source_node),
        target_node=_build_search_item(edge.target_node),
    )


# ---------------------------------------------------------------------------
# Semantic query bridge (NFM-1222)
# ---------------------------------------------------------------------------


async def _semantic_query(
    *,
    q: str,
    limit: int,
    session: AsyncSession,
) -> KGSearchResponse | SemanticQueryResponse:
    """Route a search query through LightRAG with automatic fallback.

    When LightRAG is healthy, returns a ``SemanticQueryResponse``.
    When LightRAG is unavailable, falls back to structured search
    and returns a ``KGSearchResponse``.
    """
    from nfm_db.services.lightrag_client import is_lightrag_configured

    if not is_lightrag_configured():
        logger.info("LightRAG not configured — falling back to structured search")
        return await _structured_search(
            q=q,
            type=None,
            status="active",
            limit=limit,
            offset=0,
            session=session,
        )

    try:
        from nfm_db.services.rag_provider import RAGProviderSelector

        selector = RAGProviderSelector(db_session=session)
        result = await selector.query(query=q, limit=limit)

        return SemanticQueryResponse(
            response=result.response,
            references=result.references,
            entities=result.entities,
            relationships=result.relationships,
            provider=result.provider,
            fallback=result.fallback,
        )
    except Exception:
        logger.warning(
            "LightRAG semantic query failed — falling back to structured search",
            exc_info=True,
        )
        return await _structured_search(
            q=q,
            type=None,
            status="active",
            limit=limit,
            offset=0,
            session=session,
        )


async def _structured_search(
    *,
    q: str | None,
    type: str | None,
    status: str,
    limit: int,
    offset: int,
    session: AsyncSession,
) -> KGSearchResponse:
    """Run the standard structured (ILIKE) search over KG nodes.

    Extracted from the original ``search_kg_nodes`` to enable reuse
    by the semantic query fallback path.
    """
    # Validate type filter against valid node types
    if type is not None and type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid node_type: '{type}'. "
                f"Must be one of: {', '.join(sorted(VALID_NODE_TYPES))}"
            ),
        )

    # Build base query: default to active status
    base_filter = [KGNode.status == status]

    # Add ILIKE search on label and aliases
    if q is not None:
        pattern = f"%{q}%"
        base_filter.append(
            or_(
                KGNode.label.ilike(pattern),
                KGNode.aliases.ilike(pattern),
            )
        )

    # Add type filter
    if type is not None:
        base_filter.append(KGNode.node_type == type)

    # Count query
    count_stmt = select(func.count()).select_from(KGNode).where(*base_filter)
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Data query with pagination
    data_stmt = (
        select(KGNode).where(*base_filter).order_by(KGNode.label.asc()).limit(limit).offset(offset)
    )
    rows = (await session.execute(data_stmt)).scalars().all()

    items = [_build_search_item(row) for row in rows]

    return KGSearchResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ===========================================================================
# Graph visualization endpoint (NFM-1093)
# ===========================================================================

# Map KGNode.node_type → GraphCanvas GraphNodeType
_NODE_TYPE_MAP: dict[str, str] = {
    "Material": "material",
    "Property": "property",
    "Experiment": "entity",
    "Condition": "entity",
    "Publication": "entity",
    "Measurement": "entity",
}


def _build_graph_node(node: KGNode, edge_count: dict[str, int]) -> GraphNodeItem:
    """Map a KGNode to a GraphNodeItem for the visualization response."""
    return GraphNodeItem(
        id=str(node.id),
        label=node.label,
        type=_NODE_TYPE_MAP.get(node.node_type, "default"),
        size=node.confidence,
        color="",
        child_count=edge_count.get(str(node.id), 0),
    )


def _build_graph_edge(edge: KGEdge) -> GraphEdgeItem:
    """Map a KGEdge to a GraphEdgeItem for the visualization response."""
    return GraphEdgeItem(
        id=str(edge.id),
        source=str(edge.source_node_id),
        target=str(edge.target_node_id),
        label=edge.relation_type,
        type=edge.relation_type,
    )


@router.get(
    "/kg/graph",
    response_model=ApiResponse[KGGraphResponse],
    summary="Get graph data for visualization",
)
async def get_kg_graph(
    type: str | None = Query(
        None, description="Optional filter by node_type (Material, Property, etc.)"
    ),
    limit: int = Query(200, ge=1, le=500, description="Max nodes to return"),
    edge_limit: int = Query(500, ge=1, le=2000, description="Max edges to return"),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[KGGraphResponse]:
    """Return nodes and edges formatted for force-directed graph visualization.

    Returns a flat list of nodes (mapped to GraphCanvas GraphNode types)
    and edges suitable for rendering in the interactive /kg/explore page.
    Paginates both nodes and edges independently to stay within
    bundle-size limits.
    """
    # Validate type filter
    if type is not None and type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid node_type: '{type}'. "
                f"Must be one of: {', '.join(sorted(VALID_NODE_TYPES))}"
            ),
        )

    # Build node query
    node_filter = [KGNode.status == "active"]
    if type is not None:
        node_filter.append(KGNode.node_type == type)

    # Count nodes
    node_count_stmt = select(func.count()).select_from(KGNode).where(*node_filter)
    total_nodes: int = (await session.execute(node_count_stmt)).scalar_one()

    # Fetch nodes
    node_stmt = select(KGNode).where(*node_filter).order_by(KGNode.label.asc()).limit(limit)
    node_rows = (await session.execute(node_stmt)).scalars().all()

    node_ids = [n.id for n in node_rows]

    # Fetch edges for these nodes (both outgoing and incoming)
    graph_nodes: list[GraphNodeItem] = []
    graph_edges: list[GraphEdgeItem] = []
    total_edges: int = 0

    if node_ids:
        # Count edges per node for child_count
        edge_count_stmt = (
            select(
                KGEdge.source_node_id,
                func.count().label("cnt"),
            )
            .where(
                or_(
                    KGEdge.source_node_id.in_(node_ids),
                    KGEdge.target_node_id.in_(node_ids),
                )
            )
            .group_by(KGEdge.source_node_id)
        )
        edge_count_rows = (await session.execute(edge_count_stmt)).all()
        edge_count_map = {str(row[0]): row[1] for row in edge_count_rows}

        # Fetch edges
        edge_filter = [
            or_(
                KGEdge.source_node_id.in_(node_ids),
                KGEdge.target_node_id.in_(node_ids),
            )
        ]
        total_edges_stmt = select(func.count()).select_from(KGEdge).where(*edge_filter)
        total_edges = (await session.execute(total_edges_stmt)).scalar_one()

        edge_stmt = select(KGEdge).where(*edge_filter).limit(edge_limit)
        edge_rows = (await session.execute(edge_stmt)).scalars().all()

        graph_nodes = [_build_graph_node(n, edge_count_map) for n in node_rows]
        graph_edges = [_build_graph_edge(e) for e in edge_rows]

    return ApiResponse(
        success=True,
        data=KGGraphResponse(
            nodes=graph_nodes,
            edges=graph_edges,
            total_nodes=total_nodes,
            total_edges=total_edges,
        ),
    )


# ===========================================================================
# Node detail & relations endpoints (NFM-1099)
#
# IMPORTANT: /kg/nodes/{node_id}/relations MUST be declared before
# /kg/nodes/{node_type}/{node_id}, otherwise FastAPI matches the literal
# "relations" segment as the {node_type} path parameter.
# ===========================================================================


@router.get(
    "/kg/nodes/{node_id}/relations",
    response_model=ApiResponse[KGRelationsResponse],
    summary="获取知识图谱节点的关联关系",
    description="返回指定节点的所有入边和出边，包含关联节点的摘要信息。\n\nReturn all edges where the given node participates as source or target, with neighbor node summaries.",
)
async def get_kg_node_relations(
    node_id: UUID,
    relation_type: str | None = Query(
        None, description="Optional filter by relation_type (e.g. contains)"
    ),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[KGRelationsResponse]:
    """Return all edges where the given node participates as either source or target.

    Eager-loads both endpoints so the response can include lightweight node
    summaries for navigation without an N+1 query pattern.
    """
    # Verify the node exists before listing edges (clear 404 vs ambiguous empty).
    node_check = await session.execute(select(KGNode.id).where(KGNode.id == node_id))
    if node_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Node not found")

    # Query both outgoing and incoming edges in a single statement.
    base_filter = [
        or_(
            KGEdge.source_node_id == node_id,
            KGEdge.target_node_id == node_id,
        )
    ]
    if relation_type is not None:
        base_filter.append(KGEdge.relation_type == relation_type)

    # Count for pagination metadata.
    count_stmt = select(func.count()).select_from(KGEdge).where(*base_filter)
    total: int = (await session.execute(count_stmt)).scalar_one()

    # Data query with eager-loaded endpoints.
    data_stmt = (
        select(KGEdge)
        .options(
            selectinload(KGEdge.source_node),
            selectinload(KGEdge.target_node),
        )
        .where(*base_filter)
        .order_by(KGEdge.relation_type.asc(), KGEdge.id.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(data_stmt)).scalars().all()

    items = [_build_relation_edge(row) for row in rows]

    return ApiResponse(
        success=True,
        data=KGRelationsResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        ),
    )


@router.get(
    "/kg/nodes/{node_type}/{node_id}",
    response_model=ApiResponse[KGNodeDetail],
    summary="按类型和ID获取知识图谱节点",
    description="返回指定类型和ID的单个知识图谱节点详情。\n\nReturn a single KG node scoped by both type and ID.",
)
async def get_kg_node(
    node_type: str,
    node_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[KGNodeDetail]:
    """Return a single KG node scoped by both type and ID.

    The node_type segment validates the type classification before the
    database query so callers receive a 400 for clearly invalid types.
    A mismatch (valid type but node has a different type) yields 404.
    """
    if node_type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid node_type: '{node_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_NODE_TYPES))}"
            ),
        )

    stmt = select(KGNode).where(KGNode.id == node_id)
    node = (await session.execute(stmt)).scalar_one_or_none()

    if node is None or node.node_type != node_type:
        raise HTTPException(status_code=404, detail="Node not found")

    return ApiResponse(success=True, data=_build_node_detail(node))


# ===========================================================================
# Review Queue endpoints (NFM-859 B2.6)
# ===========================================================================


class ApproveRequest(BaseModel):
    """Request body for approving a review item."""

    reviewer_notes: str | None = Field(
        None,
        description="Optional notes from the reviewer",
    )


class RejectRequest(BaseModel):
    """Request body for rejecting a review item."""

    reason: str = Field(
        ...,
        min_length=1,
        description="Reason for rejection",
    )


class ReviewQueueResponse(BaseModel):
    """Response envelope for the review queue listing."""

    items: list[dict] = Field(default_factory=list)
    total: int = 0


@router.get(
    "/kg/review/queue",
    response_model=ApiResponse[ReviewQueueResponse],
    summary="获取待审核队列",
    description="列出知识图谱中待审核的实体和关系条目。\n\nList pending items in the KG review queue.",
)
async def list_review_queue(
    item_type: str | None = Query(None, description="Filter by item type (entity/relation)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewQueueResponse]:
    """List pending items in the KG review queue."""
    items, total = await list_pending_reviews(
        db,
        item_type=item_type,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(
        success=True,
        data=ReviewQueueResponse(items=items, total=total),
    )


@router.post(
    "/kg/review/{review_id}/approve",
    response_model=ApiResponse[dict],
    summary="批准审核条目",
    description="批准待审核的实体或关系，将其纳入正式知识图谱。\n\nApprove a pending review item and promote it to the live KG.",
)
async def approve_review(
    review_id: UUID,
    current_user: Annotated[User, Depends(require_reviewer)],
    body: ApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Approve a pending review item and promote it to the live KG."""
    result = await approve_review_item(
        db,
        review_id=review_id,
        reviewer_notes=body.reviewer_notes if body else None,
    )
    if "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result["error"],
        )
    return ApiResponse(success=True, data=result)


@router.post(
    "/kg/review/{review_id}/reject",
    response_model=ApiResponse[dict],
    summary="驳回审核条目",
    description="驳回待审核的实体或关系，需提供驳回理由。\n\nReject a pending review item with a reason.",
)
async def reject_review(
    review_id: UUID,
    current_user: Annotated[User, Depends(require_reviewer)],
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Reject a pending review item with a reason."""
    result = await reject_review_item(
        db,
        review_id=review_id,
        reason=body.reason,
    )
    if "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result["error"],
        )
    return ApiResponse(success=True, data=result)
