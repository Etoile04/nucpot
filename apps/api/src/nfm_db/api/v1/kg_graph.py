"""Knowledge Graph subgraph endpoint (NFM-1274, NFM-4083).

``GET /api/v1/kg/graph/subgraph`` returns the depth-*n* neighbourhood subgraph
of a Knowledge Graph node.  Public read-only endpoint (no auth required).
No env vars required.

Mounted at ``/kg/graph/subgraph`` (not ``/kg/graph``) to avoid the routing
collision with ``api/v1/kg.py``'s global-pool ``/kg/graph`` endpoint that
the ``/kg/explore`` page and NFM-3825 source_id filter depend on.  See
NFM-4083 for the collision post-mortem.

The *nodeId* parameter accepts a UUID, ``type:label`` pair, or bare label
(with case-insensitive fallback).  It also accepts a ``materials.id`` UUID
which is translated to the matching ``KGNode`` via the materials table
(label bridge) before BFS resolution — see :func:`_resolve_node_id`.
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.kg import KGNode
from nfm_db.models.material import Material
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
    "/kg/graph/subgraph",
    response_model=KGGraphResponse,
    summary="Get depth-n neighbourhood subgraph for a KG node (or material)",
    responses={
        404: {"description": "Focal node (or material) not found"},
        422: {"description": "nodeId missing/empty after trim, or depth out of [1..3]"},
    },
)
async def get_kg_graph_subgraph(
    nodeId: str = Query(
        min_length=1,
        description=(
            "Focal node: KG UUID, 'type:label', bare label, OR a materials.id "
            "UUID (NFM-4083 material→focal translation)."
        ),
    ),
    depth: int = Query(default=2, ge=1, le=3),
    status: str = Query(default="active", pattern="^(active|all)$"),
    session: AsyncSession = Depends(get_db),
) -> KGGraphResponse:
    """Return the depth-limited neighbourhood subgraph around a focal node.

    The *nodeId* parameter accepts a KG-node UUID, a ``type:label`` pair, or
    a bare label (with case-insensitive fallback).  As of NFM-4083 it also
    accepts a ``materials.id`` UUID; that value is translated to the
    canonical material name and re-resolved against ``KGNode`` via the
    ``Material:<name>`` form so each material page renders its own
    subgraph instead of the global pool.

    The *depth* parameter (1–3) controls how many BFS hops are included.

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

    focal = await _resolve_node_id(session, trimmed_id, status)
    if focal is None:
        raise HTTPException(
            status_code=404,
            detail=f"KG node (or material) '{nodeId}' not found",
        )
    subgraph = await build_neighborhood_subgraph(session, focal, depth, status)
    return _to_response(focal, subgraph)


async def _resolve_node_id(
    session: AsyncSession,
    raw_node_id: str,
    status_filter: str,
) -> KGNode | None:
    """Resolve a user-supplied nodeId to a single ``KGNode``.

    Resolution order (NFM-4083):

    1. Direct resolution via :func:`resolve_focal_node` — covers a
       ``KGNode`` UUID, ``type:label``, or bare label.
    2. **Material bridge** (NFM-4083): if the trimmed value parses as a
       UUID but step 1 returned ``None``, look the value up in
       ``materials.id``.  If a material row exists, re-resolve using the
       canonical ``Material:<name>`` form so callers can pass a
       ``materials.id`` UUID directly.

    The second step is what unblocks the per-material graph page: the
    frontend has ``materials.id`` but ``kg_nodes`` uses an independent
    UUID space, so a plain UUID lookup against ``kg_nodes`` never matched.

    NFM-4093 — coverage note
    ------------------------
    The bridge relies on ``kg_nodes.label = materials.name`` (exact
    match).  Migration ``071_material_kg_bridge_coverage`` closes the
    57/112 baseline gap (2026-09-02 baseline: 55 of 112 materials had a
    working bridge).  The migration is **additive only** — it does not
    alter the resolution logic.  Same-name duplicate groups (e.g. 8×
    ``Cr-doped UO2``, 5× ``U-Mo``) are intentionally not given per-material
    kg_nodes; consolidation is tracked separately under
    NFM-4093-DUP-CONSOLIDATE.  Property-slice rows are inserted with
    ``properties.dataset_slice = true`` so the NFM-4093-DATA-CLEANUP
    follow-up can collapse them against their parent material.
    """
    focal = await resolve_focal_node(session, raw_node_id, status_filter)
    if focal is not None:
        return focal

    # NFM-4083: only attempt the materials bridge when the input is a
    # well-formed UUID that didn't already resolve to a KG node.
    try:
        material_uuid = _uuid_mod.UUID(raw_node_id)
    except (ValueError, AttributeError):
        return None

    material = await session.get(Material, material_uuid)
    if material is None:
        return None

    label = material.name
    if not label:
        return None

    bridged = await resolve_focal_node(
        session,
        f"Material:{label}",
        status_filter,
    )
    if bridged is None:
        logger.info(
            "kg_graph: materials.id=%s has name=%r but no matching Material KG node",
            material.id,
            label,
        )
    return bridged


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
