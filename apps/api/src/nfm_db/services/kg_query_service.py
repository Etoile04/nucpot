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
import re
from collections import deque
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, VALID_NODE_TYPES, VALID_RELATION_TYPES
from nfm_db.schemas.kg_query import (
    KGEdgeResponse,
    KGNodeResponse,
    MAX_PATH_DEPTH,
    PathEdge,
    PathQueryResponse,
    PathResult,
    PropertyQueryResponse,
    RelationQueryResponse,
)

if TYPE_CHECKING:
    from nfm_db.schemas.kg_query import Direction

logger = logging.getLogger(__name__)

# Default AGE graph name for the nucpot knowledge graph.
DEFAULT_GRAPH_NAME = "nucpot_kg"

# Strict grammar for the AGE `graph_name` argument to ``cypher(...)``.
# ``cypher()`` requires a SQL literal string for the graph name (it cannot
# be parameter-bound), so we enforce a strict allowlist at the service
# boundary instead.
_GRAPH_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

# Cap on the BFS exploration budget per query (keeps memory bounded at
# scale even on adversarial inputs).
_MAX_EXPLORATION_BUDGET = 200


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
        # Use the portable bracket accessor: ``JSON_EXTRACT`` on SQLite,
        # ``properties->>'key'`` on PostgreSQL — works on both without
        # dialect-specific branching.
        if property_value is not None:
            stmt = stmt.where(
                KGNode.properties[property_key].as_string() == property_value  # type: ignore[index]
            )
        else:
            # Key-presence check: value is non-null in the JSON column.
            stmt = stmt.where(KGNode.properties[property_key].is_not(None))  # type: ignore[index]

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
    direction: Direction = "outgoing",
    limit: int = 20,
    offset: int = 0,
) -> RelationQueryResponse:
    """Find KG edges by relation type and/or endpoint nodes.

    ``direction`` is restricted to ``"outgoing" | "incoming" | "both"``
    by the Pydantic ``Direction`` literal. The API request schema
    validates this at the boundary; the literal type here is
    defense-in-depth for direct service callers.
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
    # The graph name and relation labels are interpolated as SQL / Cypher
    # literals (they cannot be parameter-bound via SQLAlchemy ``text()``
    # in a portable way), so they MUST be allowlist-validated here.
    if not _GRAPH_NAME_RE.fullmatch(graph_name):
        logger.warning("Rejected AGE path query: invalid graph_name=%r", graph_name)
        return None

    if relation_types:
        unknown = [r for r in relation_types if r not in VALID_RELATION_TYPES]
        if unknown:
            logger.warning(
                "Rejected AGE path query: unknown relation_types=%s", unknown
            )
            return None
        rel_labels = "|".join(relation_types)
        rel_filter = f"[{rel_labels}]"
    else:
        rel_filter = ""

    depth_pattern = "*1.." + str(max_depth)
    cypher = (
        f"SELECT * FROM cypher('{graph_name}', $$ "
        f"MATCH path = (a)-{rel_filter}{depth_pattern}->(b) "
        f"WHERE id(a) = '{source_node_id}' AND id(b) = '{target_node_id}' "
        f"RETURN path $$) AS (p agtype);"
    )

    try:
        # ``bindparam`` is not strictly required for the SQL literal
        # above (the two UUIDs were already injected into the Cypher
        # text), but binding them as parameters prevents any future
        # refactor from re-introducing injection. We pass the bound
        # params explicitly so the SQL string remains a static literal.
        stmt = text(cypher).bindparams(
            bindparam("src", source_node_id),
            bindparam("tgt", target_node_id),
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        if row is None:
            return None

        raw = row[0]
        # AGE returns agtype as a string; try to parse it as JSON. If
        # the underlying DB does not have AGE installed, ``raw`` will be
        # a string like ``'ERROR:  function cypher ... does not exist'``
        # which JSON parsing rejects — we fall back to BFS in that case.
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(
                    "AGE Cypher returned non-JSON agtype; "
                    "using relational BFS for consistent output"
                )
                return None
        else:
            parsed = raw

        # Empty result list (no path found) — return empty list rather
        # than None so the caller does NOT trigger a redundant BFS pass.
        if isinstance(parsed, list) and not parsed:
            return []

        # We did parse a result but converting an AGE graph-path object
        # into ``PathResult`` requires loading the corresponding nodes
        # from the relational tables (those properties are the source
        # of truth). Defer to ``_build_path_results`` so the BFS path
        # and the AGE path return the same shape.
        if isinstance(parsed, list):
            node_id_paths: list[list[UUID]] = []
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                ids = entry.get("node_ids")
                if isinstance(ids, list):
                    node_id_paths.append([UUID(str(i)) for i in ids])
            return await _build_path_results(
                session,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                found_paths=node_id_paths,
            )

        logger.debug(
            "AGE Cypher returned unexpected payload shape; "
            "using relational BFS for consistent output"
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("AGE Cypher unavailable (%s); using relational BFS fallback", exc)
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

    Attempts Apache AGE Cypher first; falls back to a layer-by-layer
    relational BFS that only fetches edges adjacent to the current
    frontier — never the full ``kg_edges`` table.
    """
    # Clamp depth at the service boundary too, so internal callers that
    # bypass the Pydantic request schema are still safe.
    max_depth = max(1, min(max_depth, MAX_PATH_DEPTH))

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

    # Layer-by-layer relational BFS. We only fetch edges adjacent to
    # the current frontier at each step, so total work is bounded by
    # O(max_depth × |edges adjacent to reachable nodes|) rather than
    # O(|kg_edges|).
    rel_set: frozenset[str] | None = (
        frozenset(relation_types) if relation_types else None
    )
    if rel_set is not None:
        # Defense in depth: validate against the known relation allowlist.
        rel_set = frozenset(r for r in rel_set if r in VALID_RELATION_TYPES)

    adjacency: dict[UUID, list[tuple[UUID, str]]] = {}
    visited_nodes: set[UUID] = {source_node_id}
    frontier: set[UUID] = {source_node_id}
    explored_edges = 0

    for _ in range(max_depth):
        if not frontier:
            break
        if explored_edges >= _MAX_EXPLORATION_BUDGET:
            # Safety valve: cap total BFS edge exploration so an
            # adversarial input cannot exhaust memory.
            logger.debug(
                "BFS exploration budget exhausted after %d edges", explored_edges
            )
            break

        stmt = select(KGEdge).where(KGEdge.source_node_id.in_(frontier))  # type: ignore[union-attr]
        if rel_set is not None:
            stmt = stmt.where(KGEdge.relation_type.in_(rel_set))  # type: ignore[union-attr]
        rows = (await session.execute(stmt)).scalars().all()

        next_frontier: set[UUID] = set()
        for edge in rows:
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge.relation_type),
            )
            explored_edges += 1
            if edge.target_node_id not in visited_nodes:
                next_frontier.add(edge.target_node_id)
                visited_nodes.add(edge.target_node_id)

        # Also surface reverse-direction edges so the BFS can answer
        # "find a path between A and B" regardless of edge orientation.
        rev_stmt = select(KGEdge).where(KGEdge.target_node_id.in_(frontier))  # type: ignore[union-attr]
        if rel_set is not None:
            rev_stmt = rev_stmt.where(KGEdge.relation_type.in_(rel_set))  # type: ignore[union-attr]
        rev_rows = (await session.execute(rev_stmt)).scalars().all()
        for edge in rev_rows:
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, edge.relation_type),
            )
            explored_edges += 1
            if edge.source_node_id not in visited_nodes:
                next_frontier.add(edge.source_node_id)
                visited_nodes.add(edge.source_node_id)

        frontier = next_frontier

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
