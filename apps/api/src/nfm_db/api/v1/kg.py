"""Knowledge Graph query endpoints.

Read-only routes for KG nodes and edges with caching, timeout,
and unified pagination (PaginationParams from NFM-1072.2).

Endpoints:
  GET /kg/nodes              — list nodes (filterable by type, corpus)
  GET /kg/nodes/{node_id}     — single node detail
  GET /kg/nodes/{node_id}/edges — edges from a node (1-hop)
  GET /kg/edges              — list edges by relation type
  GET /kg/cache-stats        — cache hit/miss metrics
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import VALID_NODE_TYPES, VALID_RELATION_TYPES
from nfm_db.schemas.common import ApiResponse, PaginationParams
from nfm_db.services.kg_query_service import (
    KGNodeNotFoundError,
    KGQueryTimeoutError,
    get_cache_metrics,
    invalidate_cache,
    list_edges_by_relation,
    list_nodes_by_type,
    get_edges_from_node,
    get_node_by_id,
)

router = APIRouter(prefix="/kg", tags=["知识图谱"])

# ---------------------------------------------------------------------------
# Cache-stats endpoint
# ---------------------------------------------------------------------------


@router.get("/cache-stats", response_model=ApiResponse[dict[str, Any]])
async def kg_cache_stats() -> ApiResponse[dict[str, Any]]:
    """Return KG query cache performance metrics (hits, misses, hit-rate)."""
    metrics = get_cache_metrics()
    return ApiResponse(
        success=True,
        data={
            "hits": metrics.hits,
            "misses": metrics.misses,
            "evictions": metrics.evictions,
            "hit_rate": round(metrics.hit_rate, 4),
        },
    )


@router.post("/cache/invalidate", response_model=ApiResponse[dict[str, Any]])
async def kg_cache_invalidate() -> ApiResponse[dict[str, Any]]:
    """Clear the KG query cache. Call after data mutations."""
    invalidate_cache()
    return ApiResponse(success=True, data={"status": "cleared"})


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@router.get("/nodes", response_model=ApiResponse[dict[str, Any]])
async def list_kg_nodes(
    response: Response,
    pagination: PaginationParams = Depends(),
    node_type: str | None = Query(default=None),
    corpus_id: str | None = Query(default=None),
    status: str = Query(default="active"),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    """List KG nodes with optional type/corpus filter.

    Uses unified PaginationParams (NFM-1072.2).
    Response includes ``X-Response-Time`` header (ms).
    """
    if node_type is not None and node_type not in VALID_NODE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid node_type: {node_type!r}. "
            f"Valid: {sorted(VALID_NODE_TYPES)}",
        )

    start = time.monotonic()
    try:
        result = await list_nodes_by_type(
            session,
            node_type=node_type,
            corpus_id=corpus_id,
            status=status,
            page=pagination.page,
            per_page=pagination.per_page,
        )
    except KGQueryTimeoutError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        raise HTTPException(
            status_code=504,
            detail=str(exc),
        ) from None
    else:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return ApiResponse(success=True, data=result)


@router.get(
    "/nodes/{node_id}",
    response_model=ApiResponse[dict[str, Any]],
)
async def get_kg_node(
    response: Response,
    node_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    """Get a single KG node by UUID.

    Response includes ``X-Response-Time`` header (ms).
    """
    start = time.monotonic()
    try:
        result = await get_node_by_id(session, node_id)
    except KGNodeNotFoundError:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        raise HTTPException(
            status_code=404,
            detail=f"KG node not found: {node_id}",
        ) from None
    except KGQueryTimeoutError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        raise HTTPException(
            status_code=504,
            detail=str(exc),
        ) from None
    else:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return ApiResponse(success=True, data=result)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


@router.get(
    "/nodes/{node_id}/edges",
    response_model=ApiResponse[dict[str, Any]],
)
async def get_kg_node_edges(
    response: Response,
    node_id: UUID,
    pagination: PaginationParams = Depends(),
    direction: str = Query(default="outgoing"),
    relation_type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    """Get edges from a KG node (1-hop, paginated).

    Args:
        direction: 'outgoing' or 'incoming'.

    Uses unified PaginationParams (NFM-1072.2).
    Response includes ``X-Response-Time`` header (ms).
    """
    if direction not in ("outgoing", "incoming"):
        raise HTTPException(
            status_code=422,
            detail="direction must be 'outgoing' or 'incoming'",
        )

    start = time.monotonic()
    try:
        result = await get_edges_from_node(
            session,
            source_node_id=node_id,
            relation_type=relation_type,
            direction=direction,
            page=pagination.page,
            per_page=pagination.per_page,
        )
    except KGQueryTimeoutError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        raise HTTPException(
            status_code=504,
            detail=str(exc),
        ) from None
    else:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return ApiResponse(success=True, data=result)


@router.get("/edges", response_model=ApiResponse[dict[str, Any]])
async def list_kg_edges(
    response: Response,
    pagination: PaginationParams = Depends(),
    relation_type: str = Query(...),
    corpus_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    """List KG edges by relation type (paginated).

    Uses unified PaginationParams (NFM-1072.2).
    Response includes ``X-Response-Time`` header (ms).
    """
    if relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid relation_type: {relation_type!r}. "
            f"Valid: {sorted(VALID_RELATION_TYPES)}",
        )

    start = time.monotonic()
    try:
        result = await list_edges_by_relation(
            session,
            relation_type=relation_type,
            corpus_id=corpus_id,
            page=pagination.page,
            per_page=pagination.per_page,
        )
    except KGQueryTimeoutError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        raise HTTPException(
            status_code=504,
            detail=str(exc),
        ) from None
    else:
        elapsed_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        return ApiResponse(success=True, data=result)
