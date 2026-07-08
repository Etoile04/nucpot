"""Dual-write service: relational (source of truth) ↔ Apache AGE graph.

Synchronizes ``kg_nodes`` / ``kg_edges`` tables into a per-corpus AGE graph
(``ontology_{corpus_id}``) that acts as a queryable materialized view.

Sync modes:

  ``full``        — drops and rebuilds the entire graph from relational data.
  ``incremental`` — syncs only rows where ``synced_to_graph = false``.

On sync completion, synced rows are marked with ``synced_to_graph = True`` and
``graph_synced_at = now()``.  The AGE graph is always rebuildable from
relational data (CTO Spec §2.4).

References:
  - CTO Spec §2.2–2.4: ``docs/architecture/NFM-820-ontofuel-pg-age-migration-spec.md``
  - ADR-NFM-820-1 (AGE as materialized view)
  - ADR-NFM-820-2 (graph-per-corpus)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, OntologyIdMap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SyncStats:
    """Counters for a single sync operation."""

    nodes_total: int = 0
    nodes_synced: int = 0
    nodes_failed: int = 0
    edges_total: int = 0
    edges_synced: int = 0
    edges_failed: int = 0


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a ``sync_corpus_to_graph`` call."""

    corpus_id: str
    mode: str
    graph_name: str
    stats: SyncStats
    errors: tuple[str, ...] = ()
    completed_at: datetime = field(
        default_factory=lambda: datetime.now(UTC),
    )


@dataclass(frozen=True)
class SyncStatus:
    """Snapshot of sync progress for a corpus."""

    corpus_id: str
    graph_name: str
    synced_nodes: int
    unsynced_nodes: int
    synced_edges: int
    unsynced_edges: int
    total_nodes: int
    total_edges: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph_name(corpus_id: str) -> str:
    """Derive the AGE graph name from a corpus identifier (ADR-NFM-820-2)."""
    return f"ontology_{corpus_id}"


def _escape_cypher_string(value: str | None) -> str:
    """Escape single quotes for safe Cypher string interpolation."""
    if value is None:
        return ""
    return value.replace("'", "\\'")


def _safe_json(value: dict | list | None) -> str:
    """Serialize a value to JSON, escaping for Cypher string literals."""
    if value is None:
        return "{}"
    raw = json.dumps(value, ensure_ascii=False)
    return raw.replace("\\", "\\\\").replace("'", "\\'")


# ---------------------------------------------------------------------------
# AGE graph management
# ---------------------------------------------------------------------------


async def _drop_graph(session: AsyncSession, graph_name: str) -> None:
    """Drop an AGE graph if it exists.  No-op when it does not."""
    try:
        await session.execute(
            text(
                f"SELECT drop_graph("
                f"'{_escape_cypher_string(graph_name)}', true);"
            ),
        )
    except Exception:
        # Graph may not exist — safe to ignore.
        pass


async def _create_graph(session: AsyncSession, graph_name: str) -> None:
    """Create an empty AGE graph."""
    await session.execute(
        text(
            f"SELECT create_graph("
            f"'{_escape_cypher_string(graph_name)}');"
        ),
    )


# ---------------------------------------------------------------------------
# Ontology ID resolution
# ---------------------------------------------------------------------------


async def _resolve_ontology_id(
    session: AsyncSession,
    node_id: UUID,
    corpus_id: str | None,
) -> str | None:
    """Look up the NVL ontology ID for a KG node via ``ontology_id_map``."""
    if corpus_id is None:
        return None
    stmt = select(OntologyIdMap.nvl_id).where(
        OntologyIdMap.node_id == node_id,
        OntologyIdMap.corpus_id == corpus_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Node / Edge sync
# ---------------------------------------------------------------------------


async def sync_node_to_graph(
    session: AsyncSession,
    node: KGNode,
    graph_name: str,
) -> None:
    """Create an AGE vertex from a KGNode.

    Vertex label: ``OntoNode``.
    Properties: ``id``, ``type``, ``name``, ``aliases``, ``properties`` (JSON),
    ``confidence``, ``corpus_id``, ``ontology_id`` (if mapped).
    """
    safe_label = _escape_cypher_string(node.label)
    safe_aliases = _safe_json(
        json.loads(node.aliases) if node.aliases else None,
    )
    safe_props = _safe_json(node.properties)
    safe_corpus = _escape_cypher_string(node.corpus_id or "")
    ontology_id = _escape_cypher_string(
        await _resolve_ontology_id(session, node.id, node.corpus_id),
    )

    cypher = (
        f"SELECT * FROM cypher('{graph_name}', $$ "
        f"CREATE (n:OntoNode {{"
        f"id: '{node.id}', "
        f"type: '{node.node_type}', "
        f"name: '{safe_label}', "
        f"aliases: '{safe_aliases}', "
        f"properties: '{safe_props}', "
        f"confidence: {node.confidence}, "
        f"corpus_id: '{safe_corpus}', "
        f"ontology_id: '{ontology_id}'"
        f"}}) "
        f"RETURN n $$) AS (n agtype);"
    )
    await session.execute(text(cypher))


async def sync_edge_to_graph(
    session: AsyncSession,
    edge: KGEdge,
    graph_name: str,
) -> None:
    """Create an AGE edge from a KGEdge.

    Edge label matches ``relation_type`` (hasProperty, measuredIn, etc.).
    """
    safe_props = _safe_json(edge.properties)

    cypher = (
        f"SELECT * FROM cypher('{graph_name}', $$ "
        f"MATCH (a:OntoNode {{id: '{edge.source_node_id}'}}), "
        f"(b:OntoNode {{id: '{edge.target_node_id}'}}) "
        f"CREATE (a)-[r:{edge.relation_type} {{"
        f"id: '{edge.id}', "
        f"confidence: {edge.confidence}, "
        f"properties: '{safe_props}'"
        f"}}]->(b) "
        f"RETURN r $$) AS (r agtype);"
    )
    await session.execute(text(cypher))


# ---------------------------------------------------------------------------
# Sync-bookmark updates
# ---------------------------------------------------------------------------


async def _mark_node_synced(session: AsyncSession, node_id: UUID) -> None:
    """Mark a single node as synced to the AGE graph."""
    now = datetime.now(UTC)
    await session.execute(
        update(KGNode)
        .where(KGNode.id == node_id)
        .values(synced_to_graph=True, graph_synced_at=now),
    )


async def _mark_edge_synced(session: AsyncSession, edge_id: UUID) -> None:
    """Mark a single edge as synced to the AGE graph."""
    now = datetime.now(UTC)
    await session.execute(
        update(KGEdge)
        .where(KGEdge.id == edge_id)
        .values(synced_to_graph=True, graph_synced_at=now),
    )


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


async def rebuild_ontology_graph(
    session: AsyncSession,
    corpus_id: str,
) -> SyncResult:
    """Full graph rebuild: drop → create → load all vertices → load all edges.

    Every ``kg_nodes``/``kg_edges`` row for the corpus is synced regardless
    of ``synced_to_graph`` status.
    """
    gname = _graph_name(corpus_id)
    errors: list[str] = []
    nodes_synced = 0
    nodes_failed = 0
    edges_synced = 0
    edges_failed = 0

    # 1. Drop and recreate graph
    await _drop_graph(session, gname)
    await _create_graph(session, gname)

    # 2. Load all nodes for this corpus
    node_stmt = select(KGNode).where(KGNode.corpus_id == corpus_id)
    nodes = (await session.execute(node_stmt)).scalars().all()

    for node in nodes:
        try:
            await sync_node_to_graph(session, node, gname)
            await _mark_node_synced(session, node.id)
            nodes_synced += 1
        except Exception as exc:
            nodes_failed += 1
            errors.append(f"node {node.id}: {exc}")
            logger.warning("Failed to sync node %s: %s", node.id, exc)

    # 3. Load all edges for this corpus
    edge_stmt = select(KGEdge).where(KGEdge.corpus_id == corpus_id)
    edges = (await session.execute(edge_stmt)).scalars().all()

    for edge in edges:
        try:
            await sync_edge_to_graph(session, edge, gname)
            await _mark_edge_synced(session, edge.id)
            edges_synced += 1
        except Exception as exc:
            edges_failed += 1
            errors.append(f"edge {edge.id}: {exc}")
            logger.warning("Failed to sync edge %s: %s", edge.id, exc)

    stats = SyncStats(
        nodes_total=len(nodes),
        nodes_synced=nodes_synced,
        nodes_failed=nodes_failed,
        edges_total=len(edges),
        edges_synced=edges_synced,
        edges_failed=edges_failed,
    )

    return SyncResult(
        corpus_id=corpus_id,
        mode="full",
        graph_name=gname,
        stats=stats,
        errors=tuple(errors),
    )


async def sync_corpus_to_graph(
    session: AsyncSession,
    corpus_id: str,
    mode: str = "full",
) -> SyncResult:
    """Synchronize a corpus's KG data to its AGE graph.

    Args:
        corpus_id: The corpus to sync.
        mode: ``"full"`` drops and rebuilds from all rows.
              ``"incremental"`` only syncs rows where
              ``synced_to_graph = false``.

    Returns:
        ``SyncResult`` with counts and any per-node/edge errors.

    Raises:
        ValueError: If ``mode`` is not ``"full"`` or ``"incremental"``.
    """
    if mode not in ("full", "incremental"):
        raise ValueError(
            f"Invalid sync mode {mode!r}; expected 'full' or 'incremental'"
        )

    if mode == "full":
        return await rebuild_ontology_graph(session, corpus_id)

    # Incremental mode
    gname = _graph_name(corpus_id)
    errors: list[str] = []
    nodes_synced = 0
    nodes_failed = 0
    edges_synced = 0
    edges_failed = 0

    # Ensure graph exists (idempotent)
    try:
        await _create_graph(session, gname)
    except Exception:
        pass  # Graph already exists — safe to ignore.

    # Sync unsynced nodes
    unsynced_nodes_stmt = (
        select(KGNode).where(
            KGNode.corpus_id == corpus_id,
            KGNode.synced_to_graph.is_(False),
        )
    )
    unsynced_nodes = (await session.execute(unsynced_nodes_stmt)).scalars().all()

    for node in unsynced_nodes:
        try:
            await sync_node_to_graph(session, node, gname)
            await _mark_node_synced(session, node.id)
            nodes_synced += 1
        except Exception as exc:
            nodes_failed += 1
            errors.append(f"node {node.id}: {exc}")
            logger.warning("Failed to sync node %s: %s", node.id, exc)

    # Sync unsynced edges
    unsynced_edges_stmt = (
        select(KGEdge).where(
            KGEdge.corpus_id == corpus_id,
            KGEdge.synced_to_graph.is_(False),
        )
    )
    unsynced_edges = (await session.execute(unsynced_edges_stmt)).scalars().all()

    for edge in unsynced_edges:
        try:
            await sync_edge_to_graph(session, edge, gname)
            await _mark_edge_synced(session, edge.id)
            edges_synced += 1
        except Exception as exc:
            edges_failed += 1
            errors.append(f"edge {edge.id}: {exc}")
            logger.warning("Failed to sync edge %s: %s", edge.id, exc)

    stats = SyncStats(
        nodes_total=nodes_synced + nodes_failed,
        nodes_synced=nodes_synced,
        nodes_failed=nodes_failed,
        edges_total=edges_synced + edges_failed,
        edges_synced=edges_synced,
        edges_failed=edges_failed,
    )

    return SyncResult(
        corpus_id=corpus_id,
        mode="incremental",
        graph_name=gname,
        stats=stats,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# Sync status
# ---------------------------------------------------------------------------


async def get_sync_status(
    session: AsyncSession,
    corpus_id: str,
) -> SyncStatus:
    """Return counts of synced vs. unsynced nodes/edges for a corpus."""
    gname = _graph_name(corpus_id)

    total_nodes_stmt = (
        select(func.count())
        .select_from(
            select(KGNode).where(KGNode.corpus_id == corpus_id).subquery(),
        )
    )
    total_nodes = (await session.execute(total_nodes_stmt)).scalar() or 0

    synced_nodes_stmt = (
        select(func.count())
        .select_from(
            select(KGNode).where(
                KGNode.corpus_id == corpus_id,
                KGNode.synced_to_graph.is_(True),
            ).subquery(),
        )
    )
    synced_nodes = (await session.execute(synced_nodes_stmt)).scalar() or 0

    total_edges_stmt = (
        select(func.count())
        .select_from(
            select(KGEdge).where(KGEdge.corpus_id == corpus_id).subquery(),
        )
    )
    total_edges = (await session.execute(total_edges_stmt)).scalar() or 0

    synced_edges_stmt = (
        select(func.count())
        .select_from(
            select(KGEdge).where(
                KGEdge.corpus_id == corpus_id,
                KGEdge.synced_to_graph.is_(True),
            ).subquery(),
        )
    )
    synced_edges = (await session.execute(synced_edges_stmt)).scalar() or 0

    return SyncStatus(
        corpus_id=corpus_id,
        graph_name=gname,
        synced_nodes=synced_nodes,
        unsynced_nodes=total_nodes - synced_nodes,
        synced_edges=synced_edges,
        unsynced_edges=total_edges - synced_edges,
        total_nodes=total_nodes,
        total_edges=total_edges,
    )
