"""Knowledge Graph subgraph endpoint (NFM-1274).

``GET /api/v1/kg/graph`` returns the depth-*n* neighbourhood subgraph of a
Knowledge Graph node.  Public read-only endpoint (no auth required).
No env vars required.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import KGNode
from nfm_db.schemas.kg import (
    KGGraphEdge,
    KGGraphNode,
    KGGraphResponse,
)
from nfm_db.services.kg_graph import (
    KGSubgraph,
    build_neighborhood_subgraph,
    resolve_focal_node,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/kg/graph",
    response_model=KGGraphResponse,
    summary="Get depth-n neighbourhood subgraph for a KG node",
    responses={
        404: {"description": "Focal node not found"},
        422: {"description": "nodeId missing/empty after trim, or depth out of [1..3]"},
    },
)
async def get_kg_graph(
    nodeId: str = Query(
        min_length=1, description="Focal node: UUID, 'type:label', or label"
    ),
    depth: int = Query(default=2, ge=1, le=3),
    status: str = Query(default="active", pattern="^(active|all)$"),
    session: AsyncSession = Depends(get_db),
) -> KGGraphResponse:
    """Return the depth-limited neighbourhood subgraph around a focal node.

    The *nodeId* parameter accepts a UUID, a ``type:label`` pair, or a bare
    label (with case-insensitive fallback).  The *depth* parameter (1–3)
    controls how many BFS hops are included.

    ``nodeId`` is whitespace-trimmed at the validation layer; an empty
    result after trim returns ``422 nodeId must not be empty`` rather than
    falling through to a misleading 404.
    """
    trimmed_id = nodeId.strip()
    if not trimmed_id:
        raise HTTPException(
            status_code=422,
            detail="nodeId must not be empty",
        )

    focal = await resolve_focal_node(session, trimmed_id, status)
    if focal is None:
        raise HTTPException(
            status_code=404,
            detail=f"KG node '{nodeId}' not found",
        )
    subgraph = await build_neighborhood_subgraph(session, focal, depth, status)
    return _to_response(focal, subgraph)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _to_response(
    focal: KGNode,
    subgraph: KGSubgraph,
) -> KGGraphResponse:
    """Project a ``KGSubgraph`` into the API response schema.

    ``properties.__depth`` is already injected by the service (locked
    contract #3), so this is a direct field mapping.  Nodes come back
    pre-sorted from the service; edges are sorted here for determinism.
    """
    node_items: list[KGGraphNode] = [
        KGGraphNode(
            id=str(node.id),
            label=node.label,
            type=node.node_type,
            properties=dict(node.properties),
            status=node.status,
            confidence=node.confidence,
            source_id=str(node.source_id) if node.source_id else None,
        )
        for node in subgraph.nodes
    ]

    edge_items: list[KGGraphEdge] = sorted(
        (
            KGGraphEdge(
                source=str(edge.source_node_id),
                target=str(edge.target_node_id),
                type=edge.relation_type,
                properties=dict(edge.properties or {}),
                confidence=edge.confidence,
            )
            for edge in subgraph.edges
        ),
        key=lambda e: (e.source, e.target, e.type),
    )

    return KGGraphResponse(
        focal={"id": str(focal.id), "depth": 0},
        nodes=node_items,
        edges=edge_items,
    )
