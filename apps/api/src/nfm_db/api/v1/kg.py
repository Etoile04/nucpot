"""Knowledge Graph search endpoint (NFM-1166, NFM-1222).

``GET /api/v1/kg/search`` provides paginated, filterable search over KG nodes.
When ``mode=lightrag`` is specified, the query is routed through the LightRAG
semantic query bridge instead of the standard ILIKE search.
Public read-only endpoint (no auth required).
"""

from __future__ import annotations

import logging
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import VALID_NODE_TYPES, KGNode
from nfm_db.schemas.common import PaginationParams
from nfm_db.schemas.kg import KGSearchItem, KGSearchResponse, SemanticQueryResponse
from nfm_db.services.kg_utils import parse_aliases

logger = logging.getLogger(__name__)

router = APIRouter(tags=["知识图谱"])


@router.get(
    "/kg/search",
    summary="Search Knowledge Graph nodes",
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
    _offset: int | None = Query(default=None, ge=0, alias="offset", deprecated=True, description="已弃用: 请使用 page 参数"),
    _limit: int | None = Query(default=None, ge=1, le=100, alias="limit", deprecated=True, description="已弃用: 请使用 per_page 参数"),
    session: AsyncSession = Depends(get_db),
) -> Union[KGSearchResponse, SemanticQueryResponse]:
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


# ---------------------------------------------------------------------------
# Semantic query bridge (NFM-1222)
# ---------------------------------------------------------------------------


async def _semantic_query(
    *,
    q: str,
    limit: int,
    session: AsyncSession,
) -> Union[KGSearchResponse, SemanticQueryResponse]:
    """Route a search query through LightRAG with automatic fallback.

    When LightRAG is healthy, returns a ``SemanticQueryResponse``.
    When LightRAG is unavailable, falls back to structured search
    and returns a ``KGSearchResponse``.
    """
    from nfm_db.services.lightrag_client import is_lightrag_configured

    if not is_lightrag_configured():
        logger.info("LightRAG not configured — falling back to structured search")
        return await _structured_search(
            q=q, type=None, status="active", limit=limit, offset=0, session=session,
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
            q=q, type=None, status="active", limit=limit, offset=0, session=session,
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
        select(KGNode)
        .where(*base_filter)
        .order_by(KGNode.label.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(data_stmt)).scalars().all()

    items = [_build_search_item(row) for row in rows]

    return KGSearchResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
