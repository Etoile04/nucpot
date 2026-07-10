"""Ontology Sync Service (NFM-820 Phase 2).

Dual-write service that synchronizes relational kg_nodes/kg_edges tables
to Apache AGE graph vertices/edges. Provides:
- sync_corpus_to_graph(): Full or incremental corpus sync entry point
- rebuild_ontology_graph(): Full corpus graph rebuild from relational data
- sync_node_to_graph(): Single node sync after CRUD
- sync_edge_to_graph(): Single edge sync after CRUD
- get_sync_status(): Query sync progress for a corpus
- _create_age_graph(): Create AGE graph if not exists
- _load_age_extension(): Ensure LOAD age; SET search_path per session

Architecture (ADR-001): Relational tables are source of truth; AGE is
rebuildable materialized view. All AGE operations use raw SQL via
session.execute(text(...)) with proper per-session initialization.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode


class OntologySyncError(Exception):
    """Base exception for ontology sync failures."""


class GraphNotFoundError(OntologySyncError):
    """Raised when an AGE graph does not exist."""


@dataclass(frozen=True)
class SyncResult:
    """Immutable result of a sync operation."""

    nodes_synced: int
    edges_synced: int
    duration_ms: float


@dataclass(frozen=True)
class SyncStatus:
    """Immutable snapshot of corpus sync progress."""

    corpus_id: str
    nodes_total: int
    nodes_synced: int
    edges_total: int
    edges_synced: int


async def _load_age_extension(session: AsyncSession) -> None:
    """Ensure AGE extension is loaded and search_path is set.

    Must be called per session that touches AGE (ADR-003).
    Executes: LOAD age; SET search_path TO ag_catalog, "$current_schema"

    Note: This MUST be async because session.execute() on an AsyncSession
    returns a coroutine that must be awaited.
    """
    await session.execute(text("LOAD age;"))
    await session.execute(
        text('SET search_path TO ag_catalog, "$user", public;')
    )


def _graph_name(corpus_id: str) -> str:
    """Generate AGE graph name from corpus_id (ADR-002).

    Pattern: ontology_{corpus_id}
    Total length must be <=63 chars (AGE graph name limit).
    Empty corpus_id defaults to "default".
    """
    corpus = corpus_id or "default"
    safe_corpus = corpus.replace("-", "_")[:54]  # 54 + len("ontology_") = 63
    return f"ontology_{safe_corpus}"


def _escape_cypher_string(value: str) -> str:
    """Escape a string value for safe inclusion in Cypher $$-delimited queries.

    Handles single quotes, backslashes, and newlines within Cypher string
    literals. This is used instead of parameterized queries because AGE's
    ``cypher()`` function does not support bind parameters for the inner
    Cypher string.
    """
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("\n", "\\n")
    escaped = escaped.replace("\r", "\\r")
    return escaped


def _build_vertex_cypher(
    graph_name: str,
    node: KGNode,
    corpus_value: str,
) -> str:
    """Build a Cypher MERGE vertex statement for a KGNode.

    Uses MERGE on id for idempotent re-runs. Vertex label is ``OntoNode``
    per NVL contract.
    """
    node_id_str = str(node.id)
    node_type = _escape_cypher_string(node.node_type)
    label = _escape_cypher_string(node.label)
    aliases = (
        _escape_cypher_string(str(node.aliases))
        if node.aliases
        else "[]"
    )

    return (
        f"SELECT * FROM cypher('{graph_name}', $$ "
        f"MERGE (n:OntoNode {{id: '{node_id_str}'}}) "
        f"SET n.node_type = '{node_type}', "
        f"n.label = '{label}', "
        f"n.aliases = '{aliases}', "
        f"n.confidence = {node.confidence}, "
        f"n.corpus_id = '{corpus_value}' "
        f"$$) AS (v agtype);"
    )


def _build_edge_cypher(
    graph_name: str,
    edge: KGEdge,
    corpus_value: str,
) -> str:
    """Build a Cypher MATCH...MERGE edge statement for a KGEdge.

    Uses the edge's relation_type as the Cypher edge label (e.g.
    ``hasProperty``, ``measuredIn``) per NVL contract, rather than a
    generic ``RELATION`` label. The label is sanitized to alphanumeric
    plus underscore since AGE requires this for edge labels.
    """
    source_id_str = str(edge.source_node_id)
    target_id_str = str(edge.target_node_id)
    relation_type = _escape_cypher_string(edge.relation_type)
    safe_label = "".join(
        c if c.isalnum() or c == "_" else "_"
        for c in edge.relation_type
    )

    return (
        f"SELECT * FROM cypher('{graph_name}', $$ "
        f"MATCH (source:OntoNode {{id: '{source_id_str}'}}) "
        f"MATCH (target:OntoNode {{id: '{target_id_str}'}}) "
        f"MERGE (source)-[r:{safe_label} {{"
        f"relation_type: '{relation_type}', "
        f"confidence: {edge.confidence}, "
        f"corpus_id: '{corpus_value}'"
        f"}}]->(target) "
        f"$$) AS (v agtype);"
    )


async def _graph_exists(session: AsyncSession, graph_name: str) -> bool:
    """Check whether an AGE graph exists in ag_catalog.ag_graph.

    Uses parameterized query for the graph name (PostgreSQL native).
    """
    result = await session.execute(
        text("SELECT 1 FROM ag_catalog.ag_graph WHERE graph_name = :gn"),
        {"gn": graph_name},
    )
    return result.fetchone() is not None


async def _create_age_graph(session: AsyncSession, corpus_id: str) -> None:
    """Create an AGE graph if it does not already exist.

    Uses CREATE GRAPH IF NOT EXISTS to avoid errors on repeated calls.
    Per-session AGE initialization is handled by _load_age_extension().
    """
    await _load_age_extension(session)
    graph_name = _graph_name(corpus_id)

    await session.execute(
        text(f"CREATE GRAPH IF NOT EXISTS {graph_name}")
    )
    await session.commit()


async def _drop_age_graph(session: AsyncSession, corpus_id: str) -> None:
    """Drop an AGE graph if it exists.

    Used before rebuild to ensure clean materialization.
    """
    await _load_age_extension(session)
    graph_name = _graph_name(corpus_id)

    await session.execute(
        text(f"DROP GRAPH IF EXISTS {graph_name}")
    )
    await session.commit()


async def rebuild_ontology_graph(
    session: AsyncSession,
    corpus_id: str,
) -> SyncResult:
    """Full graph rebuild from relational data for a corpus.

    Process:
    1. Drop existing graph (if any)
    2. Create fresh graph
    3. Load all nodes for corpus_id
    4. Materialize vertices in AGE
    5. Load all edges for corpus_id
    6. Materialize edges in AGE
    7. Update synced_to_graph flags and timestamps

    Args:
        session: Database session with AGE extension available
        corpus_id: Corpus to rebuild (empty string = use default corpus)

    Returns:
        SyncResult with nodes_synced, edges_synced, duration_ms

    Raises:
        OntologySyncError: If AGE operations fail
    """
    start_time = time.perf_counter()
    await _load_age_extension(session)

    corpus_filter = corpus_id if corpus_id else None
    corpus_value = corpus_id or "default"
    graph_name = _graph_name(corpus_value)

    # Drop and recreate graph
    await _drop_age_graph(session, corpus_value)
    await _create_age_graph(session, corpus_value)
    await _load_age_extension(session)  # re-init after DDL

    # Load and materialize nodes
    nodes_result = await session.execute(
        select(KGNode).where(KGNode.corpus_id == corpus_filter)
    )
    nodes = list(nodes_result.scalars().all())

    for node in nodes:
        cypher_sql = _build_vertex_cypher(graph_name, node, corpus_value)
        await session.execute(text(cypher_sql))

        node.synced_to_graph = True
        node.graph_synced_at = func.now()

    # Load and materialize edges
    edges_result = await session.execute(
        select(KGEdge).where(KGEdge.corpus_id == corpus_filter)
    )
    edges = list(edges_result.scalars().all())

    for edge in edges:
        cypher_sql = _build_edge_cypher(graph_name, edge, corpus_value)
        await session.execute(text(cypher_sql))

        edge.synced_to_graph = True
        edge.graph_synced_at = func.now()

    await session.commit()

    duration = time.perf_counter() - start_time
    return SyncResult(
        nodes_synced=len(nodes),
        edges_synced=len(edges),
        duration_ms=duration * 1000,
    )


async def sync_corpus_to_graph(
    session: AsyncSession,
    corpus_id: str,
    mode: str = "full",
) -> SyncResult:
    """Synchronize a corpus to its AGE graph.

    Args:
        session: Database session with AGE extension available
        corpus_id: Corpus to sync (empty string = use default corpus)
        mode: ``"full"`` drops and rebuilds the entire graph;
              ``"incremental"`` only syncs rows where synced_to_graph=False

    Returns:
        SyncResult with nodes_synced, edges_synced, duration_ms

    Raises:
        ValueError: If mode is not "full" or "incremental"
        OntologySyncError: If AGE operations fail
    """
    if mode not in ("full", "incremental"):
        raise ValueError(
            f"Invalid sync mode: {mode!r}. Must be 'full' or 'incremental'."
        )

    if mode == "full":
        return await rebuild_ontology_graph(session, corpus_id)

    return await _incremental_sync(session, corpus_id)


async def _incremental_sync(
    session: AsyncSession,
    corpus_id: str,
) -> SyncResult:
    """Sync only unsynced nodes and edges to the AGE graph.

    Does not drop the graph — appends/updates missing vertices and edges.
    """
    start_time = time.perf_counter()
    await _load_age_extension(session)

    corpus_filter = corpus_id if corpus_id else None
    corpus_value = corpus_id or "default"
    graph_name = _graph_name(corpus_value)

    # Ensure graph exists before incremental sync
    if not await _graph_exists(session, graph_name):
        await _create_age_graph(session, corpus_value)
        await _load_age_extension(session)

    # Load unsynced nodes
    unsynced_nodes = await session.execute(
        select(KGNode).where(
            KGNode.corpus_id == corpus_filter,
            KGNode.synced_to_graph.is_(False),  # type: ignore[union-attr]
        )
    )
    nodes = list(unsynced_nodes.scalars().all())

    for node in nodes:
        cypher_sql = _build_vertex_cypher(graph_name, node, corpus_value)
        await session.execute(text(cypher_sql))

        node.synced_to_graph = True
        node.graph_synced_at = func.now()

    # Load unsynced edges
    unsynced_edges = await session.execute(
        select(KGEdge).where(
            KGEdge.corpus_id == corpus_filter,
            KGEdge.synced_to_graph.is_(False),  # type: ignore[union-attr]
        )
    )
    edges = list(unsynced_edges.scalars().all())

    for edge in edges:
        cypher_sql = _build_edge_cypher(graph_name, edge, corpus_value)
        await session.execute(text(cypher_sql))

        edge.synced_to_graph = True
        edge.graph_synced_at = func.now()

    await session.commit()

    duration = time.perf_counter() - start_time
    return SyncResult(
        nodes_synced=len(nodes),
        edges_synced=len(edges),
        duration_ms=duration * 1000,
    )


async def sync_node_to_graph(
    session: AsyncSession,
    node_id: uuid.UUID,
) -> None:
    """Sync a single node to AGE after CRUD operations.

    Creates or updates the vertex in the appropriate corpus graph.
    Raises GraphNotFoundError if the target graph does not exist.
    """
    await _load_age_extension(session)

    node_result = await session.execute(
        select(KGNode).where(KGNode.id == node_id)
    )
    node = node_result.scalar_one_or_none()

    if not node:
        raise OntologySyncError(f"Node not found: {node_id}")

    corpus_id = node.corpus_id or "default"
    graph_name = _graph_name(corpus_id)

    if not await _graph_exists(session, graph_name):
        raise GraphNotFoundError(f"Graph not found: {graph_name}")

    cypher_sql = _build_vertex_cypher(graph_name, node, corpus_id)
    await session.execute(text(cypher_sql))

    node.synced_to_graph = True
    node.graph_synced_at = func.now()
    await session.commit()


async def sync_edge_to_graph(
    session: AsyncSession,
    edge_id: uuid.UUID,
) -> None:
    """Sync a single edge to AGE after CRUD operations.

    Creates or updates the edge in the appropriate corpus graph.
    Raises GraphNotFoundError if the target graph does not exist.
    """
    await _load_age_extension(session)

    edge_result = await session.execute(
        select(KGEdge).where(KGEdge.id == edge_id)
    )
    edge = edge_result.scalar_one_or_none()

    if not edge:
        raise OntologySyncError(f"Edge not found: {edge_id}")

    corpus_id = edge.corpus_id or "default"
    graph_name = _graph_name(corpus_id)

    if not await _graph_exists(session, graph_name):
        raise GraphNotFoundError(f"Graph not found: {graph_name}")

    cypher_sql = _build_edge_cypher(graph_name, edge, corpus_id)
    await session.execute(text(cypher_sql))

    edge.synced_to_graph = True
    edge.graph_synced_at = func.now()
    await session.commit()


async def get_sync_status(
    session: AsyncSession,
    corpus_id: str,
) -> SyncStatus:
    """Return sync progress for a corpus.

    Counts total vs synced nodes and edges for the given corpus.
    An empty string corpus_id queries the default corpus.
    """
    corpus_filter = corpus_id if corpus_id else None

    total_nodes = await session.execute(
        select(func.count()).select_from(KGNode).where(
            KGNode.corpus_id == corpus_filter
        )
    )
    synced_nodes = await session.execute(
        select(func.count()).select_from(KGNode).where(
            KGNode.corpus_id == corpus_filter,
            KGNode.synced_to_graph.is_(True),  # type: ignore[union-attr]
        )
    )

    total_edges = await session.execute(
        select(func.count()).select_from(KGEdge).where(
            KGEdge.corpus_id == corpus_filter
        )
    )
    synced_edges = await session.execute(
        select(func.count()).select_from(KGEdge).where(
            KGEdge.corpus_id == corpus_filter,
            KGEdge.synced_to_graph.is_(True),  # type: ignore[union-attr]
        )
    )

    return SyncStatus(
        corpus_id=corpus_id or "default",
        nodes_total=total_nodes.scalar_one(),
        nodes_synced=synced_nodes.scalar_one(),
        edges_total=total_edges.scalar_one(),
        edges_synced=synced_edges.scalar_one(),
    )


# ---------------------------------------------------------------------------
# Backward-compatible aliases (deprecated — callers should migrate)
# ---------------------------------------------------------------------------

# TODO(NFM-867): Remove these aliases after kg_re.py and ontology.py migrate.
sync_node = sync_node_to_graph
sync_edge = sync_edge_to_graph
rebuild_graph = rebuild_ontology_graph
