"""Knowledge Graph endpoints (NFM-1166 / NFM-858).

Currently exposed:

* ``GET  /api/v1/kg/search``        — paginated, filterable search over KG nodes.
* ``GET  /api/v1/kg/query/property``  — find nodes by property value.
* ``GET  /api/v1/kg/query/relations`` — find edges by relation type and direction.
* ``POST /api/v1/kg/query/path``      — bounded BFS traversal between two nodes.

The three ``/query/*`` endpoints wire their request via Pydantic models
(``Depends(...)`` for query-param-backed endpoints, request body for the
POST path query) so the validators (``Literal[...]``, ``ge=...``,
``le=...``) declared on the schemas actually run — see NFM-858 review L2.

All endpoints are public read-only; no auth required.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import VALID_NODE_TYPES, KGNode
from nfm_db.schemas.common import PaginationParams
from nfm_db.schemas.kg import KGSearchItem, KGSearchResponse
from nfm_db.schemas.kg_query import (
    PathQueryRequest,
    PathQueryResponse,
    PropertyQueryRequest,
    PropertyQueryResponse,
    RelationQueryRequest,
    RelationQueryResponse,
)
from nfm_db.services.kg_query_service import (
    path_query as run_path_query,
)
from nfm_db.services.kg_query_service import (
    property_query as run_property_query,
)
from nfm_db.services.kg_query_service import (
    relation_query as run_relation_query,
)

router = APIRouter(tags=["知识图谱"])


def _parse_aliases(raw: str | None) -> list[str]:
    """Safely parse the JSON-encoded aliases column into a list."""
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return []
    except (json.JSONDecodeError, TypeError):
        return []


@router.get(
    "/kg/search",
    response_model=KGSearchResponse,
    summary="Search Knowledge Graph nodes",
)
async def search_kg_nodes(
    q: str | None = Query(default=None, description="Search term (ILIKE on label + aliases)"),
    type: str | None = Query(default=None, description="Filter by node_type"),
    status: str = Query(default="active", description="Filter by status"),
    pagination: PaginationParams = Depends(PaginationParams),
    _offset: int | None = Query(default=None, ge=0, alias="offset", deprecated=True, description="已弃用: 请使用 page 参数"),
    _limit: int | None = Query(default=None, ge=1, le=100, alias="limit", deprecated=True, description="已弃用: 请使用 per_page 参数"),
    session: AsyncSession = Depends(get_db),
) -> KGSearchResponse:
    """Search Knowledge Graph nodes with optional filters.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100 (已弃用 limit/offset 参数)

    Returns paginated results matching the given criteria.
    Defaults to active nodes only.
    """
    if _limit is not None:
        effective_page = ((_offset or 0) // _limit) + 1
        pagination = PaginationParams(page=effective_page, per_page=_limit)
    effective_limit = _limit if _limit is not None else pagination.per_page
    effective_offset = _offset if _offset is not None else pagination.offset
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
        select(KGNode)
        .where(*base_filter)
        .order_by(KGNode.label.asc())
        .limit(effective_limit)
        .offset(effective_offset)
    )
    rows = (await session.execute(data_stmt)).scalars().all()

    items = [_build_search_item(row) for row in rows]

    return KGSearchResponse(
        items=items,
        total=total,
        limit=effective_limit,
        offset=effective_offset,
    )


def _build_search_item(node: KGNode) -> KGSearchItem:
    """Map a KGNode ORM row to a KGSearchItem Pydantic schema."""
    return KGSearchItem(
        id=str(node.id),
        node_type=node.node_type,
        label=node.label,
        aliases=_parse_aliases(node.aliases),
        properties=node.properties or {},
        confidence=node.confidence,
        status=node.status,
        source_id=str(node.source_id) if node.source_id else None,
    )


# ---------------------------------------------------------------------------
# NFM-858: Three KG query modes
#
# These endpoints intentionally consume their request as Pydantic
# ``BaseModel`` instances (either as ``Depends(...)`` for query-param
# endpoints or as a request body for the POST path query).  That way the
# ``Literal[...]``, ``ge=``, and ``le=`` validators declared on the
# schemas run automatically — see NFM-858 review L2.
# ---------------------------------------------------------------------------


@router.get(
    "/kg/query/property",
    response_model=PropertyQueryResponse,
    summary="Find KG nodes by property value",
)
async def property_query_endpoint(
    request: PropertyQueryRequest = Depends(),
    session: AsyncSession = Depends(get_db),
) -> PropertyQueryResponse:
    """Find KG nodes by node_type, label, and/or JSON-property key/value.

    All filters are optional; combining ``property_key`` and
    ``property_value`` matches nodes whose JSON properties contain an
    entry equal to ``property_value``.  ``label`` is an exact match
    unless ``fuzzy=true``, in which case ``ILIKE`` substring matching is
    used.
    """
    return await run_property_query(
        session,
        node_type=request.node_type,
        label=request.label,
        property_key=request.property_key,
        property_value=request.property_value,
        fuzzy=request.fuzzy,
        limit=request.limit,
        offset=request.offset,
    )


@router.get(
    "/kg/query/relations",
    response_model=RelationQueryResponse,
    summary="Find KG edges by relation type and direction",
)
async def relation_query_endpoint(
    request: RelationQueryRequest = Depends(),
    session: AsyncSession = Depends(get_db),
) -> RelationQueryResponse:
    """Find KG edges filtered by relation type and/or endpoint nodes.

    ``direction`` is constrained by the schema to one of
    ``{"outgoing", "incoming", "both"}``; invalid values are rejected
    with HTTP 422 by Pydantic before this handler runs.
    """
    return await run_relation_query(
        session,
        source_node_id=request.source_node_id,
        target_node_id=request.target_node_id,
        relation_type=request.relation_type,
        direction=request.direction,
        limit=request.limit,
        offset=request.offset,
    )


@router.post(
    "/kg/query/path",
    response_model=PathQueryResponse,
    summary="Find bounded paths between two KG nodes",
)
async def path_query_endpoint(
    request: PathQueryRequest,
    session: AsyncSession = Depends(get_db),
) -> PathQueryResponse:
    """Find bounded paths between two KG nodes via Apache AGE / BFS.

    ``max_depth`` is hard-capped at 3 by the schema.  Service-layer
    ``ValueError`` (e.g. unknown ``relation_types``) is mapped to
    HTTP 400.
    """
    try:
        return await run_path_query(
            session,
            source_node_id=request.source_node_id,
            target_node_id=request.target_node_id,
            max_depth=request.max_depth,
            relation_types=request.relation_types,
            limit=request.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
