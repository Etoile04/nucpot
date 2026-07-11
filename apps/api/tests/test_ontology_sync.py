"""Tests for Ontology Sync Service (NFM-820).

Unit and integration tests for the dual-write sync service that synchronizes
relational kg_nodes/kg_edges tables to Apache AGE graph vertices/edges.
Coverage target: >=80% for NFM-820 acceptance criteria.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.ontology_sync import (
    GraphNotFoundError,
    OntologySyncError,
    SyncResult,
    SyncStatus,
    _build_edge_cypher,
    _build_vertex_cypher,
    _escape_cypher_string,
    _graph_name,
    get_sync_status,
    rebuild_ontology_graph,
    sync_corpus_to_graph,
    sync_edge_to_graph,
    sync_node_to_graph,
)


class TestEscapeCypherString:
    """Tests for _escape_cypher_string helper."""

    def test_plain_string_unchanged(self) -> None:
        assert _escape_cypher_string("hello") == "hello"

    def test_single_quote_escaped(self) -> None:
        assert _escape_cypher_string("it's") == "it\\'s"

    def test_backslash_escaped(self) -> None:
        assert _escape_cypher_string("path\\to") == "path\\\\to"

    def test_newline_escaped(self) -> None:
        assert _escape_cypher_string("line1\nline2") == "line1\\nline2"

    def test_carriage_return_escaped(self) -> None:
        assert _escape_cypher_string("line1\rline2") == "line1\\rline2"

    def test_combined_escapes(self) -> None:
        result = _escape_cypher_string("it's a \\path\\ with\nnewline")
        assert "\\'" in result
        assert "\\\\" in result
        assert "\\n" in result

    def test_empty_string(self) -> None:
        assert _escape_cypher_string("") == ""


class TestBuildVertexCypher:
    """Tests for _build_vertex_cypher helper."""

    def test_basic_vertex_cypher(self) -> None:
        node = KGNode(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            node_type="Material",
            label="Uranium",
            confidence=0.95,
        )
        result = _build_vertex_cypher("ontology_test", node, "test")

        assert "OntoNode" in result
        assert "MERGE" in result
        assert "00000000-0000-0000-0000-000000000001" in result
        assert "Material" in result
        assert "Uranium" in result
        assert "0.95" in result

    def test_vertex_escapes_quotes(self) -> None:
        node = KGNode(
            id=uuid.uuid4(),
            node_type="Material",
            label="O'Brien's Alloy",
            confidence=1.0,
        )
        result = _build_vertex_cypher("ontology_test", node, "test")
        assert "\\'" in result
        assert "O\\'Brien\\'s Alloy" in result

    def test_vertex_with_null_aliases(self) -> None:
        node = KGNode(
            id=uuid.uuid4(),
            node_type="Property",
            label="Density",
            aliases=None,
            confidence=1.0,
        )
        result = _build_vertex_cypher("ontology_test", node, "test")
        assert "aliases = '[]'" in result

    def test_vertex_with_aliases(self) -> None:
        node = KGNode(
            id=uuid.uuid4(),
            node_type="Property",
            label="Density",
            aliases='["Mass Density"]',
            confidence=1.0,
        )
        result = _build_vertex_cypher("ontology_test", node, "test")
        assert "Mass Density" in result


class TestBuildEdgeCypher:
    """Tests for _build_edge_cypher helper."""

    def test_basic_edge_cypher(self) -> None:
        source_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        target_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
        edge = KGEdge(
            id=uuid.uuid4(),
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type="hasProperty",
            confidence=0.9,
        )
        result = _build_edge_cypher("ontology_test", edge, "test")

        assert "OntoNode" in result
        assert "hasProperty" in result
        assert "MERGE" in result
        assert str(source_id) in result
        assert str(target_id) in result

    def test_edge_label_sanitized(self) -> None:
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        edge = KGEdge(
            id=uuid.uuid4(),
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type="has-property!",
            confidence=1.0,
        )
        result = _build_edge_cypher("ontology_test", edge, "test")
        # Non-alphanumeric chars should be replaced with underscore
        assert "[r:has_property_ {" in result

    def test_edge_uses_relation_type_as_label(self) -> None:
        """Edge label should be the relation_type, not generic RELATION."""
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        edge = KGEdge(
            id=uuid.uuid4(),
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type="measuredIn",
            confidence=1.0,
        )
        result = _build_edge_cypher("ontology_test", edge, "test")
        assert "measuredIn" in result
        assert "RELATION" not in result


class TestGraphName:
    """Tests for _graph_name helper function."""

    def test_basic_corpus_id(self) -> None:
        assert _graph_name("nucpot-v1") == "ontology_nucpot_v1"

    def test_corpus_id_with_hyphens(self) -> None:
        assert _graph_name("test-corpus-id") == "ontology_test_corpus_id"

    def test_long_corpus_id_truncation(self) -> None:
        long_id = "a" * 70
        result = _graph_name(long_id)
        assert len(result) == 63
        assert result.startswith("ontology_")
        assert result == f"ontology_{long_id[:54]}"

    def test_empty_corpus_id(self) -> None:
        assert _graph_name("") == "ontology_default"

    def test_none_corpus_id(self) -> None:
        assert _graph_name(None) == "ontology_default"  # type: ignore[arg-type]


class TestLoadAgeExtension:
    """Tests for _load_age_extension function."""

    @pytest.mark.asyncio
    async def test_load_age_extension_is_async(self, mock_session: AsyncSession) -> None:
        """Test that _load_age_extension is properly async."""
        from nfm_db.services.ontology_sync import _load_age_extension

        await _load_age_extension(mock_session)

        assert mock_session.execute.called
        calls = mock_session.execute.call_args_list

        first_sql = str(calls[0][0][0])
        assert "LOAD age" in first_sql

        second_sql = str(calls[1][0][0])
        assert "SET search_path" in second_sql


class TestRebuildOntologyGraph:
    """Tests for rebuild_ontology_graph function."""

    @pytest.mark.asyncio
    async def test_rebuild_graph_basic_flow(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rebuild_ontology_graph(mock_session, "test-corpus")

        assert isinstance(result, SyncResult)
        assert result.nodes_synced == 0
        assert result.edges_synced == 0
        assert isinstance(result.duration_ms, float)

    @pytest.mark.asyncio
    async def test_rebuild_graph_with_null_corpus(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rebuild_ontology_graph(mock_session, "")

        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_rebuild_graph_returns_sync_result(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await rebuild_ontology_graph(mock_session, "corpus")

        # SyncResult is a frozen dataclass
        assert hasattr(result, "nodes_synced")
        assert hasattr(result, "edges_synced")
        assert hasattr(result, "duration_ms")


class TestSyncCorpusToGraph:
    """Tests for sync_corpus_to_graph entry point."""

    @pytest.mark.asyncio
    async def test_full_mode(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await sync_corpus_to_graph(mock_session, "test", mode="full")

        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_incremental_mode(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await sync_corpus_to_graph(mock_session, "test", mode="incremental")

        assert isinstance(result, SyncResult)

    @pytest.mark.asyncio
    async def test_invalid_mode_raises_value_error(self, mock_session: AsyncSession) -> None:
        with pytest.raises(ValueError, match="Invalid sync mode"):
            await sync_corpus_to_graph(mock_session, "test", mode="invalid")

    @pytest.mark.asyncio
    async def test_default_mode_is_full(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await sync_corpus_to_graph(mock_session, "test")

        assert isinstance(result, SyncResult)


class TestSyncNodeToGraph:
    """Tests for sync_node_to_graph function."""

    @pytest.mark.asyncio
    async def test_sync_node_not_found(self, mock_session: AsyncSession) -> None:
        node_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with pytest.raises(OntologySyncError, match="Node not found"):
            await sync_node_to_graph(mock_session, node_id)

    @pytest.mark.asyncio
    async def test_sync_node_graph_not_found(self, mock_session: AsyncSession) -> None:
        node_id = uuid.uuid4()

        mock_node = KGNode(
            id=node_id,
            node_type="Material",
            label="Test",
            corpus_id="test-corpus",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_node
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "nfm_db.services.ontology_sync._graph_exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(GraphNotFoundError, match="Graph not found"):
                await sync_node_to_graph(mock_session, node_id)


class TestSyncEdgeToGraph:
    """Tests for sync_edge_to_graph function."""

    @pytest.mark.asyncio
    async def test_sync_edge_not_found(self, mock_session: AsyncSession) -> None:
        edge_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with pytest.raises(OntologySyncError, match="Edge not found"):
            await sync_edge_to_graph(mock_session, edge_id)

    @pytest.mark.asyncio
    async def test_sync_edge_graph_not_found(self, mock_session: AsyncSession) -> None:
        edge_id = uuid.uuid4()
        node_id = uuid.uuid4()

        mock_edge = KGEdge(
            id=edge_id,
            source_node_id=node_id,
            target_node_id=uuid.uuid4(),
            relation_type="testRelation",
            corpus_id="test-corpus",
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_edge
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "nfm_db.services.ontology_sync._graph_exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(GraphNotFoundError, match="Graph not found"):
                await sync_edge_to_graph(mock_session, edge_id)


class TestGetSyncStatus:
    """Tests for get_sync_status function."""

    @pytest.mark.asyncio
    async def test_get_sync_status_returns_sync_status(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 10
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await get_sync_status(mock_session, "test-corpus")

        assert isinstance(result, SyncStatus)
        assert result.corpus_id == "test-corpus"
        assert result.nodes_total == 10
        assert result.nodes_synced == 10
        assert result.edges_total == 10
        assert result.edges_synced == 10

    @pytest.mark.asyncio
    async def test_get_sync_status_default_corpus(self, mock_session: AsyncSession) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await get_sync_status(mock_session, "")

        assert isinstance(result, SyncStatus)
        assert result.corpus_id == "default"


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_sync_result_is_frozen(self) -> None:
        result = SyncResult(nodes_synced=5, edges_synced=3, duration_ms=100.0)
        with pytest.raises(AttributeError):
            result.nodes_synced = 10  # type: ignore[misc]

    def test_sync_result_fields(self) -> None:
        result = SyncResult(nodes_synced=1, edges_synced=2, duration_ms=50.5)
        assert result.nodes_synced == 1
        assert result.edges_synced == 2
        assert result.duration_ms == 50.5


class TestSyncStatus:
    """Tests for SyncStatus dataclass."""

    def test_sync_status_is_frozen(self) -> None:
        status = SyncStatus(
            corpus_id="test",
            nodes_total=10,
            nodes_synced=5,
            edges_total=8,
            edges_synced=3,
        )
        with pytest.raises(AttributeError):
            status.nodes_total = 20  # type: ignore[misc]


class TestOntologySyncError:
    """Tests for OntologySyncError exception."""

    def test_ontology_sync_error_message(self) -> None:
        with pytest.raises(OntologySyncError, match="test error"):
            raise OntologySyncError("test error")


class TestGraphNotFoundError:
    """Tests for GraphNotFoundError exception."""

    def test_graph_not_found_error_message(self) -> None:
        with pytest.raises(GraphNotFoundError, match="graph not found"):
            raise GraphNotFoundError("graph not found")

    def test_graph_not_found_is_ontology_sync_error(self) -> None:
        with pytest.raises(OntologySyncError):
            raise GraphNotFoundError("test")


class TestBackwardCompatibleAliases:
    """Tests that old function names still work via aliases."""

    def test_sync_node_alias_exists(self) -> None:
        from nfm_db.services.ontology_sync import sync_node
        assert sync_node is sync_node_to_graph

    def test_sync_edge_alias_exists(self) -> None:
        from nfm_db.services.ontology_sync import sync_edge
        assert sync_edge is sync_edge_to_graph

    def test_rebuild_graph_alias_exists(self) -> None:
        from nfm_db.services.ontology_sync import rebuild_graph
        assert rebuild_graph is rebuild_ontology_graph


# Fixtures
@pytest.fixture
def mock_session() -> AsyncSession:
    """Create a mock AsyncSession for testing."""
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session
