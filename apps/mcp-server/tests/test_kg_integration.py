"""Integration tests for knowledge_graph MCP tool (wired to real services).

Tests that query_knowledge_graph produces correctly-shaped JSON when
backed by real service calls.  The DB session and service layer are
both mocked to isolate the MCP tool logic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ── Helpers ──────────────────────────────────────────────────────


def _make_session_gen():
    """Create a callable that returns an async generator yielding a mock session.

    Patches get_db_session which is called as ``async for db in get_db_session()``.
    """
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    async def _gen() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _gen


def _make_kg_node(
    label: str = "Uranium Dioxide",
    node_type: str = "Material",
    node_id: uuid.UUID | None = None,
) -> MagicMock:
    """Create a mock KGNode with realistic fields."""
    node = MagicMock()
    node.id = node_id or uuid.uuid4()
    node.node_type = node_type
    node.label = label
    node.aliases = '["UO2"]'
    node.properties = {}
    node.confidence = 0.95
    node.status = "active"
    node.source_id = None
    node.corpus_id = None
    node.created_at = datetime.now(UTC)
    node.updated_at = datetime.now(UTC)
    return node


def _make_kg_edge(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation_type: str = "hasProperty",
) -> MagicMock:
    """Create a mock KGEdge with realistic fields."""
    edge = MagicMock()
    edge.id = uuid.uuid4()
    edge.source_node_id = source_id
    edge.target_node_id = target_id
    edge.relation_type = relation_type
    edge.properties = {}
    edge.confidence = 0.9
    edge.source_id = None
    edge.corpus_id = None
    edge.created_at = datetime.now(UTC)
    edge.updated_at = datetime.now(UTC)
    return edge


# ── Happy path tests ──────────────────────────────────────────────


class TestQueryKnowledgeGraphTool:
    """Integration tests for the query_knowledge_graph MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges_shape(self) -> None:
        """Tool returns JSON with 'nodes' and 'edges' keys."""
        from nfm_mcp.server import create_mcp_server

        node_id = uuid.uuid4()
        edge_target_id = uuid.uuid4()
        mock_nodes = [_make_kg_node(node_id=node_id)]
        mock_edges = [_make_kg_edge(node_id, edge_target_id)]

        mock_session_gen = _make_session_gen()
        with (
            patch(
                "nfm_mcp.tools.knowledge_graph.get_db_session",
                mock_session_gen,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=mock_nodes,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=mock_edges,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="UO2", limit=20)

        result = json.loads(result_str)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 1
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["label"] == "Uranium Dioxide"
        assert result["nodes"][0]["node_type"] == "Material"
        assert result["edges"][0]["relation_type"] == "hasProperty"

    @pytest.mark.asyncio
    async def test_normalizes_entity_type_aliases(self) -> None:
        """Tool normalizes lowercase entity types to canonical DB values."""
        from nfm_mcp.server import create_mcp_server

        mock_nodes = [_make_kg_node()]
        mock_edges = []

        mock_session_gen = _make_session_gen()
        with (
            patch(
                "nfm_mcp.tools.knowledge_graph.get_db_session",
                mock_session_gen,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=mock_nodes,
            ) as mock_query_nodes,
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=mock_edges,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            await tool_fn(query="test", entity_types=["material", "property"])

        # Verify the service was called with normalized types
        call_kwargs = mock_query_nodes.call_args[1]
        assert call_kwargs["entity_types"] == ["Material", "Property"]

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        """Tool returns empty nodes/edges when no matches found."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch(
                "nfm_mcp.tools.knowledge_graph.get_db_session",
                mock_session_gen,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="nonexistent material xyz")

        result = json.loads(result_str)
        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_node_serialization_includes_all_fields(self) -> None:
        """Serialized nodes include id, node_type, label, confidence, status."""
        from nfm_mcp.server import create_mcp_server

        node_id = uuid.uuid4()
        source_id = uuid.uuid4()
        mock_node = _make_kg_node(node_id=node_id)
        mock_node.source_id = source_id
        mock_node.aliases = '["UO2", "urania"]'
        mock_node.properties = {"element": "U"}

        mock_session_gen = _make_session_gen()
        with (
            patch(
                "nfm_mcp.tools.knowledge_graph.get_db_session",
                mock_session_gen,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[mock_node],
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="UO2")

        node = json.loads(result_str)["nodes"][0]
        assert node["id"] == str(node_id)
        assert node["source_id"] == str(source_id)
        assert node["aliases"] == '["UO2", "urania"]'
        assert node["properties"] == {"element": "U"}
        assert node["confidence"] == 0.95
        assert node["status"] == "active"


# ── Error path tests ─────────────────────────────────────────────


class TestQueryKnowledgeGraphErrors:
    """Error-path tests for the query_knowledge_graph MCP tool."""

    @pytest.mark.asyncio
    async def test_handles_db_connection_error(self) -> None:
        """Tool returns JSON error when DB session fails."""
        from nfm_mcp.server import create_mcp_server

        async def _failing_gen() -> AsyncGenerator[AsyncSession, None]:
            raise RuntimeError("DB connection refused")
            yield  # pragma: no cover

        with patch(
            "nfm_mcp.tools.knowledge_graph.get_db_session",
            _failing_gen,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="UO2")

        result = json.loads(result_str)
        assert "error" in result
        assert "Knowledge graph query failed" in result["error"]

    @pytest.mark.asyncio
    async def test_handles_service_layer_exception(self) -> None:
        """Tool returns JSON error when query service throws."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch(
                "nfm_mcp.tools.knowledge_graph.get_db_session",
                mock_session_gen,
            ),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                side_effect=ValueError("Invalid entity type"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="bad")

        result = json.loads(result_str)
        assert "error" in result
