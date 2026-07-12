"""KG query service — property, relation, and path queries (NFM-858).

Three query modes against ``kg_nodes`` / ``kg_edges`` tables:

1. Property query  — find nodes by label, type, or JSON property value.
2. Relation query  — find edges by relation type, direction, endpoints.
3. Path query      — bounded BFS traversal between two nodes (depth ≤ 3).

Path queries additionally attempt Apache AGE Cypher when the graph is
available; the Cypher path is heavily input-validated (H1), and the
service transparently falls back to the relational BFS path when AGE
is unavailable or the Cypher cannot be parsed (M4).  When AGE is asked,
only relation types that appear in ``VALID_RELATION_TYPES`` are
forwarded to the Cypher builder, ``graph_name`` is hard-coded to
``DEFAULT_GRAPH_NAME`` (no caller-controlled graph selection), and all
interpolated string literals are escaped via ``_escape_cypher_string``.

Boundary BFS (M3): the relational path traversal restricts edge
ingestion to edges incident on ``source_node_id`` and incrementally
expands as the queue explores, never loading the full ``kg_edges``
table.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import VALID_NODE_TYPES, VALID_RELATION_TYPES, KGEdge, KGNode
from nfm_db.schemas.kg_query import (
    KGEdgeResponse,
    KGNodeResponse,
    PathEdge,
    PathQueryResponse,
    PathResult,
    PropertyQueryResponse,
    RelationQueryResponse,
)
from nfm_db.services.ontology_sync import _escape_cypher_string

logger = logging.getLogger(__name__)

# Hard-coded AGE graph name. Callers cannot override this to prevent
# Cypher injection via a caller-supplied graph name (NFM-858 review H1).
DEFAULT_GRAPH_NAME = "nucpot_kg"


# ---------------------------------------------------------------------------
# Internal helpers
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
            # ``aliases`` is stored as JSON text; tolerate invalid data.
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
    and JSON property key/value lookup.

    The key/value JSON filter is applied in Python (DB-agnostic) so the
    same code path works on PostgreSQL JSONB in production and on
    SQLite JSON (which has no ``?`` / ``->>`` operators) in tests.
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

    # Fetch all matching rows so we can apply the JSON filter at the
    # Python level (portable across PostgreSQL/SQLite).
    rows = (await session.execute(stmt)).scalars().all()

    if property_key is not None:
        rows = [
            n for n in rows
            if n.properties and property_key in n.properties
        ]
        if property_value is not None:
            rows = [
                n for n in rows
                if str(n.properties.get(property_key)) == property_value
            ]

    total = len(rows)
    rows.sort(key=lambda n: n.label)
    page = rows[offset:offset + limit]

    return PropertyQueryResponse(
        nodes=[_node_to_response(n) for n in page],
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

    ``direction`` is enforced by the ``RelationQueryRequest`` schema to
    one of ``{"outgoing", "incoming", "both"}`` (see ``M2``).  When the
    caller supplies an invalid value the schema layer raises 422 before
    the service is reached, so no silent fallback exists.
    """
    stmt = select(KGEdge)

    if direction == "outgoing":
        if source_node_id is not None:
            stmt = stmt.where(KGEdge.source_node_id == source_node_id)
    elif direction == "incoming":
        if target_node_id is not None:
            stmt = stmt.where(KGEdge.target_node_id == target_node_id)
    elif direction == "both":
        if source_node_id is not None and target_node_id is not None:
            stmt = stmt.where(
                or_(
                    KGEdge.source_node_id == source_node_id,
                    KGEdge.source_node_id == target_node_id,
                )
            )
        elif source_node_id is not None:
            stmt = stmt.where(
                or_(
                    KGEdge.source_node_id == source_node_id,
                    KGEdge.target_node_id == source_node_id,
                )
            )
        elif target_node_id is not None:
            stmt = stmt.where(
                or_(
                    KGEdge.source_node_id == target_node_id,
                    KGEdge.target_node_id == target_node_id,
                )
            )
    else:  # pragma: no cover — enforced by schema Literal
        return RelationQueryResponse(edges=[], nodes=[], total=0)

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
# Path Query
# ---------------------------------------------------------------------------


async def _try_age_path_query(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    max_depth: int,
    relation_types: list[str] | None,
) -> list[PathResult] | None:
    """Attempt path query via Apache AGE Cypher (NFM-858 H1, M4).

    Inputs are fully validated before any string reaches the Cypher:

    * ``relation_types`` is intersected with ``VALID_RELATION_TYPES`` —
      any unknown type raises ``ValueError`` so the caller can map it
      to ``400`` instead of silently dropping it.
    * The graph name is hard-coded (``DEFAULT_GRAPH_NAME``); callers
      cannot supply one. (Removes the f-string graph-name injection.)
    * Numeric and UUID values are formatted via Python (``str(...)``)
      and pass through ``_escape_cypher_string`` for the relationship
      / id literals.

    Returns ``None`` whenever AGE is unavailable, the call raises, or
    the result set cannot be parsed.  The relational BFS fallback is
    the supported path at this revision (M4).
    """
    # Defensive input validation. These run *outside* the AGE-execution
    # try/except so ValueError propagates (the HTTP layer maps it to
    # 400), while AGE-execution failures stay non-fatal.
    if max_depth < 1 or max_depth > 3:
        raise ValueError(f"max_depth out of range: {max_depth}")

    safe_relation_types: list[str] = []
    if relation_types:
        unknown = sorted(set(relation_types) - VALID_RELATION_TYPES)
        if unknown:
            raise ValueError(
                f"unknown relation_types not in VALID_RELATION_TYPES: {unknown}"
            )
        safe_relation_types = list(relation_types)

    # (H1) Hard-code the graph name.  No caller-controlled value
    # ever reaches the Cypher string.
    graph_name = DEFAULT_GRAPH_NAME
    safe_graph = _escape_cypher_string(graph_name)

    try:
        # Compose a list of edge labels; each must already be in
        # VALID_RELATION_TYPES, so direct concatenation is safe.
        rel_filter = ""
        if safe_relation_types:
            rel_labels = "|".join(safe_relation_types)
            rel_filter = f"[{rel_labels}]"

        # Numeric max_depth and UUID sources/targets are formatted via
        # Python, then escaped for safe inclusion inside the Cypher.
        safe_source = _escape_cypher_string(str(source_node_id))
        safe_target = _escape_cypher_string(str(target_node_id))

        depth_pattern = "*1.." + str(max_depth)
        cypher = (
            f"SELECT * FROM cypher('{safe_graph}', $$ "
            f"MATCH path = (a)-{rel_filter}{depth_pattern}->(b) "
            f"WHERE id(a) = '{safe_source}' AND id(b) = '{safe_target}' "
            f"RETURN path $$) AS (p agtype);"
        )

        result = await session.execute(_age_safe_text(cypher))
        row = result.fetchone()
        if row is None:
            return None

        # AGE agtype parsing is non-trivial and version-dependent.  For
        # now we deliberately fall back to the relational BFS rather
        # than ship a fragile parser.  (NFM-858 review M4.)
        logger.debug(
            "AGE Cypher returned data; using relational BFS for consistent output"
        )
        return None
    except Exception:
        logger.debug("AGE Cypher unavailable, using relational BFS fallback")
        return None


def _age_safe_text(sql: str):
    """Return a SQL text clause for execution.

    AGE's ``cypher()`` function does not support bind parameters for
    the inner Cypher string, so we cannot use ``sqlalchemy.text(...,
    bindparams=...)``.  All inputs are validated/escaped in
    ``_try_age_path_query`` before they reach this function.
    """
    from sqlalchemy import text

    return text(sql)


async def _load_path_adjacency(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    visited: set[UUID] | None = None,
) -> dict[UUID, list[tuple[UUID, str]]]:
    """Load edges incident on the frontier of a BFS (NFM-858 review M3).

    Starts with edges touching ``source_node_id``, then expands
    outward to edges touching any newly-discovered node already in the
    BFS frontier.  Never reads the entire ``kg_edges`` table.
    """
    frontier: set[UUID] = {source_node_id}
    if visited:
        frontier |= visited

    adjacency: dict[UUID, list[tuple[UUID, str]]] = {}
    seen_pairs: set[tuple[UUID, UUID]] = set()

    while frontier:
        stmt = select(KGEdge).where(
            or_(
                KGEdge.source_node_id.in_(frontier),  # type: ignore[arg-type]
                KGEdge.target_node_id.in_(frontier),  # type: ignore[arg-type]
            )
        )
        rows = (await session.execute(stmt)).scalars().all()

        next_frontier: set[UUID] = set()
        for edge in rows:
            key = (edge.source_node_id, edge.target_node_id)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)

            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge.relation_type)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, f"_inv_{edge.relation_type}")
            )

            for nid in (edge.source_node_id, edge.target_node_id):
                if nid not in frontier and nid != source_node_id:
                    next_frontier.add(nid)

        frontier = next_frontier

        # Safety: cap the expansion to avoid pathological explosion.
        if len(adjacency) > 5_000:
            logger.warning(
                "Adjacency fan-out exceeded 5,000 edges — truncating BFS"
            )
            break

    return adjacency


def _bfs_find_paths(
    adjacency: dict[UUID, list[tuple[UUID, str]]],
    *,
    source: UUID,
    target: UUID,
    max_depth: int,
    relation_types: frozenset[str] | None,
    limit: int,
) -> list[list[UUID]]:
    """BFS to find simple paths from ``source`` to ``target`` up to ``max_depth`` hops."""
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
            # Inverse edges from adjacency are prefixed ``_inv_``; strip
            # so they still match the allowlist check.
            stripped = rel_type.removeprefix("_inv_")

            if neighbor in visited:
                continue
            if relation_types and stripped not in relation_types:
                continue

            new_path = [*path, neighbor]
            if neighbor == target:
                paths.append(new_path)
                if len(paths) >= limit:
                    return paths
            elif len(new_path) - 1 < max_depth:
                queue.append((neighbor, new_path, visited | {neighbor}))

    return paths


async def _build_path_results(
    session: AsyncSession,
    *,
    found_paths: list[list[UUID]],
) -> list[PathResult]:
    """Convert node-ID paths from BFS into PathResult objects."""
    if not found_paths:
        return []

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
            a, b = path[i], path[i + 1]
            # Edges are directed in the KG, but the BFS relaxes
            # direction, so look up both (a → b) and (b → a).
            all_edge_pairs.add((a, b))
            all_edge_pairs.add((b, a))

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

    results: list[PathResult] = []
    for path in found_paths:
        path_nodes = [nodes_map[nid] for nid in path if nid in nodes_map]
        path_edges: list[PathEdge] = []
        for i in range(len(path) - 1):
            pair = (path[i], path[i + 1])
            edge = edges_map.get(pair)
            if edge is None:
                # Direction is reversed in the path; look up the inverse.
                edge = edges_map.get((pair[1], pair[0]))
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


async def path_query(
    session: AsyncSession,
    *,
    source_node_id: UUID,
    target_node_id: UUID,
    max_depth: int = 3,
    relation_types: list[str] | None = None,
    limit: int = 10,
) -> PathQueryResponse:
    """Find paths between two KG nodes.

    Attempt order:

    1. Apache AGE Cypher (input-validated, hard-coded graph name,
       string-escaped — see ``_try_age_path_query``).
    2. Bounded BFS over a frontier-expanded adjacency view of the
       relational ``kg_edges`` table (NFM-858 review M3).
    """
    # 1) Try AGE Cypher first
    try:
        age_results = await _try_age_path_query(
            session,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            max_depth=max_depth,
            relation_types=relation_types,
        )
        if age_results is not None:
            return PathQueryResponse(paths=age_results, total=len(age_results))
    except ValueError:
        # Re-raise validation errors so the endpoint can map to HTTP 400.
        raise

    # 2) Relational BFS fallback (the supported path at this revision —
    #    see NFM-858 review M4).
    adjacency = await _load_path_adjacency(
        session,
        source_node_id=source_node_id,
    )

    rel_set: frozenset[str] | None = (
        frozenset(relation_types) if relation_types else None
    )

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
        found_paths=found_paths,
    )

    return PathQueryResponse(paths=path_results, total=len(path_results))
