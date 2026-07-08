"""Tests for ontology_sync dual-write service (NFM-867).

Tests cover:
  - Frozen dataclass immutability (SyncResult, SyncStats, SyncStatus)
  - Helper functions (_graph_name, _escape_cypher_string, _safe_json)
  - sync_corpus_to_graph full mode
  - sync_corpus_to_graph incremental mode
  - get_sync_status counts
  - Invalid mode rejection
  - Empty corpus handling
  - Per-node/edge error tracking
  - Sync bookmark updates (synced_to_graph, graph_synced_at)

AGE Cypher calls are mocked (SQLite does not support ``cypher()``).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.ontology_sync import (
    SyncResult,
    SyncStats,
    SyncStatus,
    _escape_cypher_string,
    _graph_name,
    _safe_json,
    get_sync_status,
    sync_corpus_to_graph,
)


# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------

_CORPUS_ID = "test-corpus-001"
_NODE_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_NODE_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_NODE_C = uuid.UUID("33333333-3333-3333-3333-333333333333")
_EDGE_AB = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_EDGE_AC = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestGraphName:
    """_graph_name derives the AGE graph name per ADR-NFM-820-2."""

    def test_basic_corpus(self) -> None:
        assert _graph_name("abc") == "ontology_abc"

    def test_uuid_corpus(self) -> None:
        assert _graph_name("550e8400-e29b-41d4") == "ontology_550e8400-e29b-41d4"

    def test_empty_corpus(self) -> None:
        assert _graph_name("") == "ontology_"


class TestEscapeCypherString:
    """_escape_cypher_string prevents Cypher injection."""

    def test_none(self) -> None:
        assert _escape_cypher_string(None) == ""

    def test_simple(self) -> None:
        assert _escape_cypher_string("hello") == "hello"

    def test_single_quote(self) -> None:
        assert _escape_cypher_string("it's") == "it\\'s"

    def test_multiple_quotes(self) -> None:
        assert _escape_cypher_string("'a' 'b'") == "\\'a\\' \\'b\\'"


class TestSafeJson:
    """_safe_json serializes and escapes for Cypher string literals."""

    def test_none(self) -> None:
        assert _safe_json(None) == "{}"

    def test_dict(self) -> None:
        assert _safe_json({"key": "value"}) == '{"key": "value"}'

    def test_list(self) -> None:
        assert _safe_json(["a", "b"]) == '["a", "b"]'

    def test_escapes_quotes(self) -> None:
        result = _safe_json({"name": "O'Brien"})
        assert "O\\'Brien" in result

    def test_escapes_backslashes(self) -> None:
        result = _safe_json({"path": "C:\\Users"})
        assert "\\\\Users" in result


# ---------------------------------------------------------------------------
# Frozen dataclass tests
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """SyncResult, SyncStats, SyncStatus must be immutable."""

    def test_sync_stats_frozen(self) -> None:
        stats = SyncStats(nodes_total=5)
        with pytest.raises(AttributeError):
            stats.nodes_total = 10  # type: ignore[misc]

    def test_sync_result_frozen(self) -> None:
        result = SyncResult(
            corpus_id="c1",
            mode="full",
            graph_name="ontology_c1",
            stats=SyncStats(),
        )
        with pytest.raises(AttributeError):
            result.corpus_id = "c2"  # type: ignore[misc]

    def test_sync_status_frozen(self) -> None:
        status = SyncStatus(
            corpus_id="c1",
            graph_name="ontology_c1",
            synced_nodes=1,
            unsynced_nodes=0,
            synced_edges=0,
            unsynced_edges=1,
            total_nodes=1,
            total_edges=1,
        )
        with pytest.raises(AttributeError):
            status.total_nodes = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_sync_status tests (relational-only — works on SQLite)
# ---------------------------------------------------------------------------


class TestGetSyncStatus:
    """get_sync_status returns correct counts from relational tables."""

    @pytest.fixture
    async def seed_nodes_edges(self, db_session) -> None:
        """Insert test nodes and edges with mixed sync states."""
        db_session.add(KGNode(
            id=_NODE_A,
            node_type="Material",
            label="UO2",
            corpus_id=_CORPUS_ID,
            synced_to_graph=True,
            status="active",
        ))
        db_session.add(KGNode(
            id=_NODE_B,
            node_type="Property",
            label="Density",
            corpus_id=_CORPUS_ID,
            synced_to_graph=False,
            status="active",
        ))
        db_session.add(KGNode(
            id=_NODE_C,
            node_type="Material",
            label="PuO2",
            corpus_id="other-corpus",
            synced_to_graph=True,
            status="active",
        ))
        db_session.add(KGEdge(
            id=_EDGE_AB,
            source_node_id=_NODE_A,
            target_node_id=_NODE_B,
            relation_type="hasProperty",
            corpus_id=_CORPUS_ID,
            synced_to_graph=True,
            confidence=0.9,
        ))
        db_session.add(KGEdge(
            id=_EDGE_AC,
            source_node_id=_NODE_A,
            target_node_id=_NODE_C,
            relation_type="relatedTo",
            corpus_id=_CORPUS_ID,
            synced_to_graph=False,
            confidence=0.8,
        ))
        await db_session.flush()

    @pytest.mark.asyncio
    async def test_returns_correct_counts(
        self, db_session, seed_nodes_edges,
    ) -> None:
        status = await get_sync_status(db_session, _CORPUS_ID)

        assert status.corpus_id == _CORPUS_ID
        assert status.graph_name == f"ontology_{_CORPUS_ID}"
        assert status.total_nodes == 2
        assert status.synced_nodes == 1
        assert status.unsynced_nodes == 1
        assert status.total_edges == 2
        assert status.synced_edges == 1
        assert status.unsynced_edges == 1

    @pytest.mark.asyncio
    async def test_empty_corpus(self, db_session) -> None:
        status = await get_sync_status(db_session, "nonexistent")

        assert status.corpus_id == "nonexistent"
        assert status.total_nodes == 0
        assert status.synced_nodes == 0
        assert status.unsynced_nodes == 0
        assert status.total_edges == 0
        assert status.synced_edges == 0
        assert status.unsynced_edges == 0

    @pytest.mark.asyncio
    async def test_corpus_isolation(
        self, db_session, seed_nodes_edges,
    ) -> None:
        """Each corpus counts only its own rows."""
        other_status = await get_sync_status(db_session, "other-corpus")

        assert other_status.total_nodes == 1
        assert other_status.total_edges == 0


# ---------------------------------------------------------------------------
# sync_corpus_to_graph tests (mocked AGE Cypher)
# ---------------------------------------------------------------------------


def _make_node(
    node_id: uuid.UUID,
    label: str,
    *,
    synced: bool = False,
    corpus_id: str = _CORPUS_ID,
) -> KGNode:
    """Create a test KGNode."""
    return KGNode(
        id=node_id,
        node_type="Material",
        label=label,
        corpus_id=corpus_id,
        synced_to_graph=synced,
        status="active",
        confidence=0.95,
        properties={"formula": label},
    )


def _make_edge(
    edge_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation_type: str,
    *,
    synced: bool = False,
    corpus_id: str = _CORPUS_ID,
) -> KGEdge:
    """Create a test KGEdge."""
    return KGEdge(
        id=edge_id,
        source_node_id=source_id,
        target_node_id=target_id,
        relation_type=relation_type,
        corpus_id=corpus_id,
        synced_to_graph=synced,
        confidence=0.9,
    )


class TestSyncCorpusToGraph:
    """sync_corpus_to_graph orchestration with mocked AGE calls."""

    @pytest.mark.asyncio
    async def test_full_sync_calls_drop_create_and_sync(
        self, db_session,
    ) -> None:
        """Full mode drops graph, creates new, syncs all nodes/edges."""
        nodes = [
            _make_node(_NODE_A, "UO2"),
            _make_node(_NODE_B, "Density"),
        ]
        edges = [
            _make_edge(_EDGE_AB, _NODE_A, _NODE_B, "hasProperty"),
        ]

        for n in nodes:
            db_session.add(n)
        for e in edges:
            db_session.add(e)
        await db_session.flush()

        with (
            patch("nfm_db.services.ontology_sync._drop_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync._create_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_node_to_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_edge_to_graph", new_callable=AsyncMock),
        ):
            result = await sync_corpus_to_graph(db_session, _CORPUS_ID, mode="full")

        assert result.mode == "full"
        assert result.graph_name == f"ontology_{_CORPUS_ID}"
        assert result.stats.nodes_total == 2
        assert result.stats.nodes_synced == 2
        assert result.stats.nodes_failed == 0
        assert result.stats.edges_total == 1
        assert result.stats.edges_synced == 1
        assert result.stats.edges_failed == 0
        assert result.errors == ()

    @pytest.mark.asyncio
    async def test_incremental_sync_only_unsynced_rows(
        self, db_session,
    ) -> None:
        """Incremental mode syncs only unsynced nodes/edges."""
        db_session.add(_make_node(_NODE_A, "UO2", synced=True))
        db_session.add(_make_node(_NODE_B, "Density", synced=False))
        db_session.add(_make_node(_NODE_C, "PuO2", synced=True))
        db_session.add(_make_edge(_EDGE_AB, _NODE_A, _NODE_B, "hasProperty", synced=True))
        db_session.add(_make_edge(_EDGE_AC, _NODE_A, _NODE_C, "relatedTo", synced=False))
        await db_session.flush()

        with (
            patch("nfm_db.services.ontology_sync._create_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_node_to_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_edge_to_graph", new_callable=AsyncMock),
        ):
            result = await sync_corpus_to_graph(
                db_session, _CORPUS_ID, mode="incremental",
            )

        assert result.mode == "incremental"
        assert result.stats.nodes_total == 1  # only unsynced
        assert result.stats.nodes_synced == 1
        assert result.stats.edges_total == 1
        assert result.stats.edges_synced == 1

    @pytest.mark.asyncio
    async def test_invalid_mode_raises(self, db_session) -> None:
        """Invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid sync mode"):
            await sync_corpus_to_graph(db_session, _CORPUS_ID, mode="bad")

    @pytest.mark.asyncio
    async def test_empty_corpus_returns_zero_counts(
        self, db_session,
    ) -> None:
        """Empty corpus returns SyncResult with zero counts."""
        with (
            patch("nfm_db.services.ontology_sync._drop_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync._create_graph", new_callable=AsyncMock),
        ):
            result = await sync_corpus_to_graph(
                db_session, _CORPUS_ID, mode="full",
            )

        assert result.stats.nodes_total == 0
        assert result.stats.edges_total == 0
        assert result.errors == ()

    @pytest.mark.asyncio
    async def test_per_node_error_tracking(
        self, db_session,
    ) -> None:
        """Failed nodes are tracked in errors; successful ones still sync."""
        nodes = [
            _make_node(_NODE_A, "UO2"),
            _make_node(_NODE_B, "Density"),
            _make_node(_NODE_C, "PuO2"),
        ]

        for n in nodes:
            db_session.add(n)
        await db_session.flush()

        async def _mock_sync_node(session, node, gname):
            if node.id == _NODE_B:
                raise RuntimeError("AGE connection lost")

        with (
            patch("nfm_db.services.ontology_sync._drop_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync._create_graph", new_callable=AsyncMock),
            patch(
                "nfm_db.services.ontology_sync.sync_node_to_graph",
                new_callable=AsyncMock,
                side_effect=_mock_sync_node,
            ),
        ):
            result = await sync_corpus_to_graph(
                db_session, _CORPUS_ID, mode="full",
            )

        assert result.stats.nodes_total == 3
        assert result.stats.nodes_synced == 2
        assert result.stats.nodes_failed == 1
        assert len(result.errors) == 1
        assert "AGE connection lost" in result.errors[0]

    @pytest.mark.asyncio
    async def test_full_mode_marks_nodes_synced(
        self, db_session,
    ) -> None:
        """Full sync sets synced_to_graph=True on all synced nodes."""
        nodes = [
            _make_node(_NODE_A, "UO2"),
            _make_node(_NODE_B, "Density"),
        ]

        for n in nodes:
            db_session.add(n)
        await db_session.flush()

        with (
            patch("nfm_db.services.ontology_sync._drop_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync._create_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_node_to_graph", new_callable=AsyncMock),
            patch("nfm_db.services.ontology_sync.sync_edge_to_graph", new_callable=AsyncMock),
        ):
            await sync_corpus_to_graph(
                db_session, _CORPUS_ID, mode="full",
            )

        await db_session.flush()

        # Verify synced_to_graph was set
        from sqlalchemy import select

        result = await db_session.execute(
            select(KGNode).where(KGNode.corpus_id == _CORPUS_ID),
        )
        all_nodes = result.scalars().all()
        for node in all_nodes:
            assert node.synced_to_graph is True
            assert node.graph_synced_at is not None


# ---------------------------------------------------------------------------
# sync_node_to_graph Cypher query structure
# ---------------------------------------------------------------------------


class TestSyncNodeToGraph:
    """Verify sync_node_to_graph generates correct Cypher queries."""

    @pytest.mark.asyncio
    async def test_cypher_query_structure(self) -> None:
        """Cypher query contains expected vertex properties."""
        node = KGNode(
            id=_NODE_A,
            node_type="Material",
            label="UO2",
            aliases=json.dumps(["Urania"]),
            properties={"formula": "UO2"},
            confidence=0.95,
            corpus_id=_CORPUS_ID,
            status="active",
        )

        call_count = 0
        session = AsyncMock()

        async def mock_execute(stmt):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # First call is _resolve_ontology_id
                result.scalar_one_or_none = MagicMock(return_value="mat:UO2")
            return result

        session.execute = AsyncMock(side_effect=mock_execute)

        from nfm_db.services.ontology_sync import sync_node_to_graph as _sync

        await _sync(session, node, f"ontology_{_CORPUS_ID}")

        # Second execute call should be the Cypher CREATE
        cypher_call = session.execute.call_args_list[-1]
        cypher_text = str(cypher_call[0][0])
        assert "CREATE (n:OntoNode" in cypher_text
        assert f"id: '{_NODE_A}'" in cypher_text
        assert "type: 'Material'" in cypher_text
        assert "name: 'UO2'" in cypher_text
        assert "confidence: 0.95" in cypher_text
        assert "corpus_id: 'test-corpus-001'" in cypher_text
        assert "ontology_id: 'mat:UO2'" in cypher_text


class TestSyncEdgeToGraph:
    """Verify sync_edge_to_graph generates correct Cypher queries."""

    @pytest.mark.asyncio
    async def test_cypher_query_structure(self) -> None:
        """Cypher query contains MATCH, CREATE, and correct edge label."""
        edge = KGEdge(
            id=_EDGE_AB,
            source_node_id=_NODE_A,
            target_node_id=_NODE_B,
            relation_type="hasProperty",
            confidence=0.9,
            corpus_id=_CORPUS_ID,
        )
        session = AsyncMock()

        from nfm_db.services.ontology_sync import sync_edge_to_graph as _sync

        await _sync(session, edge, f"ontology_{_CORPUS_ID}")

        cypher_call = session.execute.call_args[0][0]
        cypher_text = str(cypher_call)
        assert "MATCH (a:OntoNode" in cypher_text
        assert f"id: '{_NODE_A}'" in cypher_text
        assert f"id: '{_NODE_B}'" in cypher_text
        assert "[r:hasProperty" in cypher_text
        assert f"id: '{_EDGE_AB}'" in cypher_text
