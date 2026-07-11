"""Knowledge Graph neighbourhood subgraph service (NFM-1280).

Provides BFS-based neighbourhood traversal for the ``GET /api/v1/kg/graph``
endpoint.  Returns a depth-limited subgraph around a focal node.

No external service dependencies — pure Postgres reads.
No env vars required.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode

logger = logging.getLogger(__name__)

MAX_NODES: int = 500
MAX_EDGES: int = 1500


@dataclass(frozen=True)
class KGSubgraph:
    """Immutable result of a neighbourhood subgraph query."""

    nodes: tuple[KGNode, ...]
    edges: tuple[KGEdge, ...]
    depth_map: dict[uuid.UUID, int] = field(default_factory=dict)


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
    """BFS from *focal* up to *depth*. Returns frozen ``KGSubgraph``.

    The BFS explores both outgoing and incoming edges (undirected
    neighbourhood).  Edges are deduplicated by (source, target, type).
    """
    visited: set[uuid.UUID] = {focal.id}
    depth_map: dict[uuid.UUID, int] = {focal.id: 0}
    seen_edge_keys: set[tuple[str, str, str]] = set()
    edges_out: list[KGEdge] = []
    queue: deque[tuple[uuid.UUID, int]] = deque([(focal.id, 0)])

    while queue and len(visited) < MAX_NODES and len(edges_out) < MAX_EDGES:
        node_id, d = queue.popleft()
        if d >= depth:
            continue

        # Collect outgoing + incoming edges in parallel
        out_stmt = select(KGEdge).where(KGEdge.source_node_id == node_id)
        in_stmt = select(KGEdge).where(KGEdge.target_node_id == node_id)
        out_result, in_result = await asyncio.gather(
            session.execute(out_stmt),
            session.execute(in_stmt),
        )
        out_rows = out_result.scalars().all()
        in_rows = in_result.scalars().all()

        for edge in list(out_rows) + list(in_rows):
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

            other_id = (
                edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
            )
            if other_id not in visited:
                if len(visited) >= MAX_NODES:
                    break
                visited.add(other_id)
                depth_map[other_id] = d + 1
                queue.append((other_id, d + 1))

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
        filtered_nodes = tuple(n for n in node_map.values() if n.status == "active")
        valid_ids = {n.id for n in filtered_nodes}
        filtered_edges = tuple(
            e for e in edges_out if e.source_node_id in valid_ids and e.target_node_id in valid_ids
        )
    else:
        filtered_nodes = tuple(node_map.values())
        filtered_edges = tuple(edges_out)

    return KGSubgraph(
        nodes=filtered_nodes,
        edges=filtered_edges,
        depth_map=dict(depth_map),
    )
