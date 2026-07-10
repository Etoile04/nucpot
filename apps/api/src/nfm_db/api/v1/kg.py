"""Knowledge Graph search endpoint (NFM-1166).

``GET /api/v1/kg/search`` provides paginated, filterable search over KG nodes.
Public read-only endpoint (no auth required).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import VALID_NODE_TYPES, KGNode
from nfm_db.schemas.kg import KGSearchItem, KGSearchResponse

router = APIRouter()


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
    limit: int = Query(default=20, ge=1, le=100, description="Page size (max 100)"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    session: AsyncSession = Depends(get_db),
) -> KGSearchResponse:
    """Search Knowledge Graph nodes with optional filters.

    Returns paginated results matching the given criteria.
    Defaults to active nodes only.
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
