"""Knowledge Graph neighbourhood subgraph service (NFM-1274).

Provides BFS-based neighbourhood traversal for the ``GET /api/v1/kg/graph``
endpoint.  Returns a depth-limited subgraph around a focal node.

No external service dependencies — pure Postgres reads.
This module does not require any environment variables.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode

logger = logging.getLogger(__name__)

MAX_NODES: int = 500
MAX_EDGES: int = 1500


@dataclass(frozen=True)
class KGSubgraphNode:
    """A node enriched with its BFS depth for response serialization.

    ``properties`` is a copy of the underlying ``KGNode.properties`` with
    ``__depth`` injected by the service (locked contract #3) so any
    downstream consumer can render depths without recomputing from the
    edge list.
    """

    id: uuid.UUID
    label: str
    node_type: str
    status: str
    confidence: float
    source_id: uuid.UUID | None
    properties: dict[str, Any]


@dataclass(frozen=True)
class KGSubgraph:
    """Immutable result of a neighbourhood subgraph query.

    Every node already carries ``properties["__depth"]`` injected by the
    service (locked contract #3).  Nodes are pre-sorted by
    ``(depth, label)`` for deterministic serialization.
    """

    nodes: tuple[KGSubgraphNode, ...]
    edges: tuple[KGEdge, ...]


async def resolve_focal_node(
    session: AsyncSession,
    raw_node_id: str,
    status_filter: str = "active",
) -> KGNode | None:
    """Resolve a fuzzy *nodeId* into a single ``KGNode`` or ``None``.

    Resolution order:
    1. UUID string -> match by ``kg_nodes.id``.
    2. ``"type:label"`` -> match one node with that node_type + label.
    3. Bare label -> exact case-sensitive match; fall back to case-insensitive.
    """
    trimmed = raw_node_id.strip()

    # 1. Try UUID resolution
    try:
        parsed_uuid = uuid.UUID(trimmed)
        stmt = select(KGNode).where(KGNode.id == parsed_uuid)
        if status_filter == "active":
            stmt = stmt.where(KGNode.status == "active")
        result = (await session.execute(stmt)).scalars().first()
        if result is not None:
            return result
        return None
    except (ValueError, AttributeError):
        pass

    # 2. Try "type:label" form
    if ":" in trimmed:
        node_type, label = trimmed.split(":", 1)
        stmt = select(KGNode).where(
            KGNode.node_type == node_type.strip(),
            KGNode.label == label.strip(),
        )
        if status_filter == "active":
            stmt = stmt.where(KGNode.status == "active")
        result = (await session.execute(stmt)).scalars().first()
        if result is not None:
            return result
        return None

    # 3. Bare label -- exact match first, then case-insensitive fallback
    stmt = select(KGNode).where(KGNode.label == trimmed)
    if status_filter == "active":
        stmt = stmt.where(KGNode.status == "active")
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        return None  # ambiguous

    # Case-insensitive fallback
    stmt = select(KGNode).where(KGNode.label.ilike(trimmed))
    if status_filter == "active":
        stmt = stmt.where(KGNode.status == "active")
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) == 1:
        return rows[0]
    return None


async def build_neighborhood_subgraph(
    session: AsyncSession,
    focal: KGNode,
    depth: int,
    status_filter: str = "active",
) -> KGSubgraph:
    """BFS from *focal* up to *depth* using batched layer queries.

    Each BFS layer is expanded with a single batch query that fetches all
    edges touching the current frontier (both outgoing and incoming).
    This avoids per-node edge lookups and is portable to SQLite (no
    recursive CTE deadlocks).

    The BFS explores both directions (undirected neighbourhood).
    Edges are deduplicated by (source, target, relation_type).

    The returned ``KGSubgraph.nodes`` carry ``properties["__depth"]``
    injected by the service (locked contract #3) so any downstream
    consumer can render depths without recomputing from the edge list.
    """
    visited: set[uuid.UUID] = {focal.id}
    depth_map: dict[uuid.UUID, int] = {focal.id: 0}
    seen_edge_keys: set[tuple[str, str, str]] = set()
    edges_out: list[KGEdge] = []
    frontier: list[uuid.UUID] = [focal.id]

    for current_depth in range(depth):
        if not frontier or len(visited) >= MAX_NODES:
            break

        # Batch query: all edges touching the current frontier
        frontier_ids = list(frontier)
        edge_stmt = select(KGEdge).where(
            or_(
                KGEdge.source_node_id.in_(frontier_ids),
                KGEdge.target_node_id.in_(frontier_ids),
            )
        )
        rows = (await session.execute(edge_stmt)).scalars().all()

        # Process edges and discover next frontier
        next_frontier: list[uuid.UUID] = []
        for edge in rows:
            edge_key = (
                str(edge.source_node_id),
                str(edge.target_node_id),
                edge.relation_type,
            )
            if edge_key in seen_edge_keys:
                continue
            if len(edges_out) >= MAX_EDGES:
                break
            seen_edge_keys.add(edge_key)
            edges_out.append(edge)

            # Discover neighbours not yet visited
            neighbour_ids = [edge.source_node_id, edge.target_node_id]
            for neighbour_id in neighbour_ids:
                if neighbour_id in visited:
                    continue
                if len(visited) >= MAX_NODES:
                    break
                visited.add(neighbour_id)
                depth_map[neighbour_id] = current_depth + 1
                next_frontier.append(neighbour_id)

        frontier = next_frontier

    if len(visited) >= MAX_NODES:
        logger.warning(
            "kg_graph: hit MAX_NODES=%d cap during BFS from %s",
            MAX_NODES,
            focal.id,
        )
    if len(edges_out) >= MAX_EDGES:
        logger.warning(
            "kg_graph: hit MAX_EDGES=%d cap during BFS from %s",
            MAX_EDGES,
            focal.id,
        )

    # Batch-load all visited nodes
    node_map: dict[uuid.UUID, KGNode] = {}
    if visited:
        node_stmt = select(KGNode).where(KGNode.id.in_(visited))
        all_nodes = (await session.execute(node_stmt)).scalars().all()
        node_map = {n.id: n for n in all_nodes}

    # Post-hoc status filter (defensive)
    if status_filter == "active":
        filtered_node_models = [n for n in node_map.values() if n.status == "active"]
        valid_ids = {n.id for n in filtered_node_models}
        filtered_edges = tuple(
            e for e in edges_out if e.source_node_id in valid_ids and e.target_node_id in valid_ids
        )
    else:
        filtered_node_models = list(node_map.values())
        filtered_edges = tuple(edges_out)

    # Build KGSubgraphNode with __depth already injected (contract #3)
    enriched_nodes = [
        KGSubgraphNode(
            id=node.id,
            label=node.label,
            node_type=node.node_type,
            status=node.status,
            confidence=node.confidence,
            source_id=node.source_id,
            properties={**(node.properties or {}), "__depth": depth_map[node.id]},
        )
        for node in filtered_node_models
    ]

    # Deterministic ordering: by (depth, label asc)
    enriched_nodes.sort(key=lambda n: (n.properties["__depth"], n.label))

    return KGSubgraph(
        nodes=tuple(enriched_nodes),
        edges=filtered_edges,
    )
