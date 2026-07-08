"""Knowledge Graph query API endpoints (NFM-858).

Three query modes:
  - GET  /kg/query/property  — find nodes by property value
  - GET  /kg/query/relation  — find edges by relation type between entities
  - GET  /kg/query/path      — find paths between entities (depth ≤3)
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.kg_query import (
    PathQueryResponse,
    PropertyQueryResponse,
    RelationQueryResponse,
)
from nfm_db.services.kg_query_service import (
    path_query,
    property_query,
    relation_query,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/kg/query/property",
    response_model=ApiResponse[PropertyQueryResponse],
)
async def property_query_endpoint(
    node_type: str | None = Query(None, description="Filter by node type"),
    label: str | None = Query(None, description="Exact or fuzzy label match"),
    property_key: str | None = Query(None, description="JSON property key"),
    property_value: str | None = Query(None, description="JSON property value"),
    fuzzy: bool = Query(False, description="Use ILIKE for label search"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyQueryResponse]:
    """Find knowledge graph nodes by property value, label, or type."""
    result = await property_query(
        db,
        node_type=node_type,
        label=label,
        property_key=property_key,
        property_value=property_value,
        fuzzy=fuzzy,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/kg/query/relation",
    response_model=ApiResponse[RelationQueryResponse],
)
async def relation_query_endpoint(
    source_node_id: str | None = Query(None, description="Find edges FROM this node"),
    target_node_id: str | None = Query(None, description="Find edges TO this node"),
    relation_type: str | None = Query(None, description="Filter by relation type"),
    direction: str = Query("outgoing", description="outgoing, incoming, or both"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RelationQueryResponse]:
    """Find knowledge graph edges by relation type and/or endpoint nodes."""
    src_uuid: UUID | None = None
    if source_node_id:
        try:
            src_uuid = UUID(source_node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid source_node_id UUID")

    tgt_uuid: UUID | None = None
    if target_node_id:
        try:
            tgt_uuid = UUID(target_node_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid target_node_id UUID")

    result = await relation_query(
        db,
        source_node_id=src_uuid,
        target_node_id=tgt_uuid,
        relation_type=relation_type,
        direction=direction,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/kg/query/path",
    response_model=ApiResponse[PathQueryResponse],
)
async def path_query_endpoint(
    source_node_id: str = Query(..., description="Start node ID"),
    target_node_id: str = Query(..., description="Target node ID"),
    max_depth: int = Query(3, ge=1, le=5, description="Max hop depth"),
    relation_types: str | None = Query(None, description="Comma-separated relation types"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PathQueryResponse]:
    """Find paths between two KG nodes via multi-hop traversal."""
    try:
        src_uuid = UUID(source_node_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source_node_id UUID")

    try:
        tgt_uuid = UUID(target_node_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target_node_id UUID")

    rel_list = [r.strip() for r in relation_types.split(",") if r.strip()] if relation_types else None

    result = await path_query(
        db,
        source_node_id=src_uuid,
        target_node_id=tgt_uuid,
        max_depth=max_depth,
        relation_types=rel_list,
        limit=limit,
    )
    return ApiResponse(success=True, data=result)
