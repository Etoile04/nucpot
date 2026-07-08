"""KG query service — property, relation, and path queries (NFM-858).

Three query modes against kg_nodes / kg_edges tables:
  1. Property query  — find nodes by label, type, or JSON property value
  2. Relation query — find edges by relation type, direction, endpoints
  3. Path query     — BFS/DFS traversal between two nodes (depth ≤ max_depth)

Path queries also attempt Apache AGE Cypher when the graph is available,
falling back to relational BFS when AGE is not installed or the graph
has not been synced.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, VALID_NODE_TYPES, VALID_RELATION_TYPES
from nfm_db.schemas.kg_query import (
    KGEdgeResponse,
    KGNodeResponse,
    PathEdge,
    PathQueryResponse,
    PathResult,
    PropertyQueryResponse,
    RelationQueryResponse,
)

logger = logging.getLogger(__name__)

# Default AGE graph name for the nucpot knowledge graph.
DEFAULT_GRAPH_NAME = "nucpot_kg"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_response(node: KGNode) -> KGNodeResponse:
    """Convert a KGNode ORM instance to a response schema."""
    aliases: list[str] = []
    if node.aliases:
        try:
            parsed = json.loads(node.aliases)
            if isinstance(parsed, list):
                aliases = parsed
        except (json.JSONDecodeError, TypeError):
            aliases = []

    return KGNodeResponse(
        id=node.id,
        node_type=node.node_type,
        label=node.label,
        aliases=aliases,
        properties=node.properties or {},
        confidence=node.confidence,
    )


def _edge_to_response(edge: KGEdge) -> KGEdgeResponse:
    """Convert a KGEdge ORM instance to a response schema."""
    return KGEdgeResponse(
        id=edge.id,
        source_node_id=edge.source_node_id,
        target_node_id=edge.target_node_id,
        relation_type=edge.relation_type,
        properties=edge.properties or {},
        confidence=edge.confidence,
    )


# ---------------------------------------------------------------------------
# Property Query
# ---------------------------------------------------------------------------


async def property_query(
    session: AsyncSession,
    *,
    node_type: str | None = None,
    label: str | None = None,
    property_key: str | None = None,
    property_value: str | None = None,
    fuzzy: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> PropertyQueryResponse:
    """Find KG nodes by property values.

    Supports exact and fuzzy (ILIKE) label matching, node type filtering,
    and JSON property key/value lookup via PostgreSQL ``properties->>'key'``.
    """
    stmt = select(KGNode).where(KGNode.status == "active")

    if node_type is not None:
        if node_type not in VALID_NODE_TYPES:
            return PropertyQueryResponse(nodes=[], total=0)
        stmt = stmt.where(KGNode.node_type == node_type)

    if label is not None:
        if fuzzy:
            stmt = stmt.where(KGNode.label.ilike(f"%{label}%"))
        else:
            stmt = stmt.where(KGNode.label == label)

    if property_key is not None:
        stmt = stmt.where(KGNode.properties.has_key(property_key))  # type: ignore[union-attr]
        if property_value is not None:
            stmt = stmt.where(
                KGNode.properties.op("->>")(property_key) == property_value
            )

    # Count total (before pagination)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.order_by(KGNode.label).offset(offset).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    return PropertyQueryResponse(
        nodes=[_node_to_response(n) for n in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# Relation Query
# ---------------------------------------------------------------------------


async def relation_query(
    session: AsyncSession,
    *,
    source_node_id: UUID | None = None,
    target_node_id: UUID | None = None,
    relation_type: str | None = None,
    direction: str = "outgoing",
    limit: int = 20,
    offset: int = 0,
) -> RelationQueryResponse:
    """Find KG edges by relation type and/or endpoint nodes.

    ``direction`` controls which end of the edge is matched:
      - ``outgoing``: edges where source_node_id matches
      - ``incoming``: edges where target_node_id matches
      - ``both``: edges where either endpoint matches
    """
    stmt = select(KGEdge)

    if direction == "outgoing" and source_node_id is not None:
        stmt = stmt.where(KGEdge.source_node_id == source_node_id)
    elif direction == "incoming" and target_node_id is not None:
        stmt = stmt.where(KGEdge.target_node_id == target_node_id)
    elif direction == "both":
        if source_node_id is not None:
            stmt = stmt.where(
                or_(
                    KGEdge.source_node_id == source_node_id,
                    KGEdge.target_node_id == source_node_id,
                )
            )
    else:
        if source_node_id is not None:
            stmt = stmt.where(KGEdge.source_node_id == source_node_id)
        if target_node_id is not None:
            stmt = stmt.where(KGEdge.target_node_id == target_node_id)

    if relation_type is not None:
        if relation_type not in VALID_RELATION_TYPES:
            return RelationQueryResponse(edges=[], nodes=[], total=0)
        stmt = stmt.where(KGEdge.relation_type == relation_type)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(KGEdge.relation_type).offset(offset).limit(limit)
    edges = (await session.execute(stmt)).scalars().all()

    # Collect unique node IDs from edges and batch-load nodes
    node_ids: set[UUID] = set()
    for edge in edges:
        node_ids.add(edge.source_node_id)
        node_ids.add(edge.target_node_id)

    nodes_map: dict[UUID, KGNode] = {}
    if node_ids:
        node_stmt = select(KGNode).where(KGNode.id.in_(node_ids))  # type: ignore[arg-type]
        node_rows = (await session.execute(node_stmt)).scalars().all()
        nodes_map = {n.id: n for n in node_rows}

    return RelationQueryResponse(
        edges=[_edge_to_response(e) for e in edges],
        nodes=[_node_to_response(nodes_map[nid]) for nid in sorted(node_ids) if nid in nodes_map],
        total=total,
    )


# ---------------------------------------------------------------------------
# Path Query (BFS on relational tables, with AGE Cypher attempt)
# ---------------------------------------------------------------------------


async def _try_age_path_query(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    max_depth: int,
    relation_types: list[str] | None,
    graph_name: str,
) -> list[PathResult] | None:
    """Attempt path query via Apache AGE Cypher.

    Returns ``None`` when AGE is not available or parsing fails,
    signalling the caller to fall back to relational BFS.
    """
    try:
        rel_filter = ""
        if relation_types:
            rel_labels = "|".join(relation_types)
            rel_filter = f"[{rel_labels}]"

        depth_pattern = "*1.." + str(max_depth)
        cypher = (
            f"SELECT * FROM cypher('{graph_name}', $$ "
            f"MATCH path = (a)-{rel_filter}{depth_pattern}->(b) "
            f"WHERE id(a) = '{source_node_id}' AND id(b) = '{target_node_id}' "
            f"RETURN path $$) AS (p agtype);"
        )

        result = await session.execute(text(cypher))
        row = result.fetchone()
        if row is None:
            return None

        # AGE agtype parsing is complex and format-version dependent.
        # For reliability, log and fall back to relational BFS.
        logger.debug("AGE Cypher returned data; using relational BFS for consistent output")
        return None
    except Exception:
        logger.debug("AGE Cypher unavailable, using relational BFS fallback")
        return None


async def _build_path_results(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    found_paths: list[list[UUID]],
) -> list[PathResult]:
    """Convert node-ID paths from BFS into PathResult objects."""
    results: list[PathResult] = []

    all_node_ids: set[UUID] = set()
    for path in found_paths:
        all_node_ids.update(path)

    nodes_map: dict[UUID, KGNode] = {}
    if all_node_ids:
        node_stmt = select(KGNode).where(KGNode.id.in_(all_node_ids))  # type: ignore[arg-type]
        node_rows = (await session.execute(node_stmt)).scalars().all()
        nodes_map = {n.id: n for n in node_rows}

    all_edge_pairs: set[tuple[UUID, UUID]] = set()
    for path in found_paths:
        for i in range(len(path) - 1):
            all_edge_pairs.add((path[i], path[i + 1]))

    edges_map: dict[tuple[UUID, UUID], KGEdge] = {}
    if all_edge_pairs:
        conditions = [
            (KGEdge.source_node_id == src, KGEdge.target_node_id == tgt)
            for src, tgt in all_edge_pairs
        ]
        combined = [and_(*pair) for pair in conditions]
        edge_stmt = select(KGEdge).where(or_(*combined))
        edge_rows = (await session.execute(edge_stmt)).scalars().all()
        for edge in edge_rows:
            edges_map[(edge.source_node_id, edge.target_node_id)] = edge

    for path in found_paths:
        path_nodes = [nodes_map[nid] for nid in path if nid in nodes_map]
        path_edges = []
        for i in range(len(path) - 1):
            pair = (path[i], path[i + 1])
            edge = edges_map.get(pair)
            if edge:
                path_edges.append(
                    PathEdge(
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        relation_type=edge.relation_type,
                    )
                )

        results.append(
            PathResult(
                nodes=[_node_to_response(n) for n in path_nodes],
                edges=path_edges,
                length=len(path_edges),
            )
        )

    return results


def _bfs_find_paths(
    adjacency: dict[UUID, list[tuple[UUID, str]]],
    *,
    source: UUID,
    target: UUID,
    max_depth: int,
    relation_types: frozenset[str] | None,
    limit: int,
) -> list[list[UUID]]:
    """BFS to find all simple paths up to max_depth hops."""
    paths: list[list[UUID]] = []
    if source == target:
        return paths

    queue: deque[tuple[UUID, list[UUID], frozenset[UUID]]] = deque()
    queue.append((source, [source], frozenset({source})))

    while queue and len(paths) < limit:
        current, path, visited = queue.popleft()

        if len(path) - 1 >= max_depth:
            continue

        for neighbor, rel_type in adjacency.get(current, []):
            if neighbor in visited:
                continue
            if relation_types and rel_type not in relation_types:
                continue

            new_path = [*path, neighbor]
            if neighbor == target:
                paths.append(new_path)
                if len(paths) >= limit:
                    return paths
            elif len(new_path) - 1 < max_depth:
                queue.append((neighbor, new_path, visited | {neighbor}))

    return paths


async def path_query(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    max_depth: int = 3,
    relation_types: list[str] | None = None,
    limit: int = 10,
    graph_name: str = DEFAULT_GRAPH_NAME,
) -> PathQueryResponse:
    """Find paths between two KG nodes.

    Attempts Apache AGE Cypher first; falls back to relational BFS
    when AGE is not available or parsing is not yet implemented.
    """
    # Try AGE Cypher first
    age_results = await _try_age_path_query(
        session,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        max_depth=max_depth,
        relation_types=relation_types,
        graph_name=graph_name,
    )
    if age_results is not None:
        return PathQueryResponse(paths=age_results, total=len(age_results))

    # Relational BFS fallback
    edge_stmt = select(KGEdge)
    edge_rows = (await session.execute(edge_stmt)).scalars().all()

    adjacency: dict[UUID, list[tuple[UUID, str]]] = {}
    for edge in edge_rows:
        adjacency.setdefault(edge.source_node_id, []).append(
            (edge.target_node_id, edge.relation_type),
        )

    rel_set: frozenset[str] | None = frozenset(relation_types) if relation_types else None

    found_paths = _bfs_find_paths(
        adjacency,
        source=source_node_id,
        target=target_node_id,
        max_depth=max_depth,
        relation_types=rel_set,
        limit=limit,
    )

    path_results = await _build_path_results(
        session,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        found_paths=found_paths,
    )

    return PathQueryResponse(paths=path_results, total=len(path_results))
