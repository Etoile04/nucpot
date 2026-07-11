"""Integration tests for knowledge_graph MCP tool (NFM-1135 Phase B).

Tests that query_knowledge_graph produces correctly-shaped JSON when
backed by real service calls (kg_re.query_graph_nodes / query_graph_edges).
The DB session and service layer are both mocked to isolate the MCP
tool logic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


def _make_session_gen() -> Any:
    """Create a callable that returns an async generator yielding a mock session."""
    mock_session = MagicMock(spec=AsyncSession)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock()

    async def _gen() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _gen


def _make_node_dict(
    node_type: str,
    label: str,
    *,
    node_id: uuid.UUID | None = None,
    confidence: float = 0.95,
    status: str = "active",
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dict shaped like kg_re.query_graph_nodes output."""
    return {
        "id": str(node_id or uuid.uuid4()),
        "node_type": node_type,
        "label": label,
        "confidence": confidence,
        "status": status,
        "properties": properties or {},
    }


def _make_edge_dict(
    relation_type: str,
    *,
    source_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    confidence: float = 0.9,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dict shaped like kg_re.query_graph_edges output."""
    return {
        "id": str(uuid.uuid4()),
        "source_node_id": str(source_id or uuid.uuid4()),
        "target_node_id": str(target_id or uuid.uuid4()),
        "relation_type": relation_type,
        "confidence": confidence,
        "properties": properties or {},
    }


class TestQueryKnowledgeGraphTool:
    """Integration tests for the query_knowledge_graph MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges_shape(self) -> None:
        """query_knowledge_graph should return JSON with nodes and edges keys."""
        from nfm_mcp.server import create_mcp_server

        node_uo2 = _make_node_dict("Material", "UO2")
        node_tc = _make_node_dict("Property", "thermal_conductivity(UO2)")
        edge = _make_edge_dict(
            "hasProperty",
            source_id=uuid.UUID(node_uo2["id"]),
            target_id=uuid.UUID(node_tc["id"]),
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[node_uo2, node_tc],
            ) as mock_nodes,
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[edge],
            ) as mock_edges,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            result_str = await tool_fn(query="UO2", limit=20)

        mock_nodes.assert_called_once()
        mock_edges.assert_called_once()
        result = json.loads(result_str)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 2
        assert result["nodes"][0]["label"] == "UO2"
        assert len(result["edges"]) == 1
        assert result["edges"][0]["relation_type"] == "hasProperty"

    @pytest.mark.asyncio
    async def test_forwards_query_to_service(self) -> None:
        """query_knowledge_graph should forward query string to service."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_nodes,
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            await tool_fn(query="thermal conductivity", limit=10)

        call_kwargs = mock_nodes.call_args.kwargs
        assert call_kwargs["query"] == "thermal conductivity"
        assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_passes_entity_types_filter_to_service(self) -> None:
        """entity_types should be normalized to PascalCase and forwarded."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_nodes,
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            await tool_fn(
                query="UO2",
                entity_types=["material", "property"],
                limit=20,
            )

        call_kwargs = mock_nodes.call_args.kwargs
        assert call_kwargs["entity_types"] == ["Material", "Property"]

    @pytest.mark.asyncio
    async def test_normalizes_lowercase_entity_types(self) -> None:
        """Lowercase entity_type inputs should be mapped to PascalCase."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_nodes,
            patch(
                "nfm_db.services.kg_re.query_graph_edges",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_knowledge_graph"].fn
            await tool_fn(
                query="experiment",
                entity_types=["experiment", "publication"],
                limit=20,
            )

        call_kwargs = mock_nodes.call_args.kwargs
        assert call_kwargs["entity_types"] == ["Experiment", "Publication"]

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """query_knowledge_graph should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.kg_re.query_graph_nodes",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
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

        result = json.loads(result_str)
        assert "error" in result
        assert "Query failed" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_results_return_empty_arrays(self) -> None:
        """When service returns empty, result should still have nodes/edges keys."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.knowledge_graph.get_db_session", mock_session_gen),
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
            result_str = await tool_fn(query="nonexistent_xyz")

        result = json.loads(result_str)
        assert result["nodes"] == []
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_no_mock_data_imports_in_module(self) -> None:
        """knowledge_graph.py must not import from mock_data anymore."""
        import nfm_mcp.tools.knowledge_graph as kg_module

        source = open(kg_module.__file__, encoding="utf-8").read()
        assert "mock_data" not in source, (
            "knowledge_graph.py still imports from mock_data"
        )