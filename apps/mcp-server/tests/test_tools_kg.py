"""Tests for knowledge graph query MCP tools.

Covers query_knowledge_graph: entity search, entity_type filter,
limit enforcement, error handling, and helper function tests.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_mcp.server import create_mcp_server


def _get_tool(name: str):
    mcp = create_mcp_server()
    return mcp._tool_manager._tools[name].fn


def _mock_session_gen(session: MagicMock | None = None):
    sess = session or MagicMock()

    async def _gen():
        yield sess

    return _gen


# Patch targets: lazy imports inside query_knowledge_graph
_NODES = "nfm_db.services.kg_re.query_graph_nodes"
_EDGES = "nfm_db.services.kg_re.query_graph_edges"
_DB = "nfm_mcp.tools.knowledge_graph.get_db_session"


class TestQueryKnowledgeGraph:
    """Unit tests for the query_knowledge_graph MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[{"id": "n1", "label": "UO2"}]),
            patch(_EDGES, new_callable=AsyncMock, return_value=[{"source": "n1", "target": "n2"}]),
        ):
            handler = _get_tool("query_knowledge_graph")
            result = json.loads(await handler(query="UO2"))
        assert "nodes" in result
        assert "edges" in result

    @pytest.mark.asyncio
    async def test_query_forwarded_to_service(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[]) as mock_nodes,
            patch(_EDGES, new_callable=AsyncMock, return_value=[]),
        ):
            handler = _get_tool("query_knowledge_graph")
            await handler(query="thermal conductivity")
            mock_nodes.assert_called_once()
            call_kwargs = mock_nodes.call_args[1]
            assert call_kwargs["query"] == "thermal conductivity"

    @pytest.mark.asyncio
    async def test_limit_forwarded_to_service(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[]) as mock_nodes,
            patch(_EDGES, new_callable=AsyncMock, return_value=[]),
        ):
            handler = _get_tool("query_knowledge_graph")
            await handler(query="UO2", limit=10)
            call_kwargs = mock_nodes.call_args[1]
            assert call_kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_entity_types_normalized(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[]) as mock_nodes,
            patch(_EDGES, new_callable=AsyncMock, return_value=[]),
        ):
            handler = _get_tool("query_knowledge_graph")
            await handler(query="UO2", entity_types=["material", "property"])
            call_kwargs = mock_nodes.call_args[1]
            assert call_kwargs["entity_types"] == ["Material", "Property"]

    @pytest.mark.asyncio
    async def test_no_entity_types_passes_none(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[]) as mock_nodes,
            patch(_EDGES, new_callable=AsyncMock, return_value=[]),
        ):
            handler = _get_tool("query_knowledge_graph")
            await handler(query="UO2")
            call_kwargs = mock_nodes.call_args[1]
            assert call_kwargs["entity_types"] is None

    @pytest.mark.asyncio
    async def test_db_error_returns_user_friendly_json(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, side_effect=RuntimeError("Connection lost")),
        ):
            handler = _get_tool("query_knowledge_graph")
            result = json.loads(await handler(query="UO2"))
        assert "error" in result
        assert "Query failed" in result["error"]
        assert "Connection lost" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_NODES, new_callable=AsyncMock, return_value=[]),
            patch(_EDGES, new_callable=AsyncMock, return_value=[]),
        ):
            handler = _get_tool("query_knowledge_graph")
            result = await handler(query="NONEXISTENT_XYZ")
        parsed = json.loads(result)
        assert parsed is not None


class TestNormalizeEntityTypes:
    """Tests for the _normalize_entity_types helper."""

    def test_lowercase_to_pascal_case(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        assert _normalize_entity_types(["material"]) == ["Material"]
        assert _normalize_entity_types(["property"]) == ["Property"]
        assert _normalize_entity_types(["experiment"]) == ["Experiment"]
        assert _normalize_entity_types(["condition"]) == ["Condition"]
        assert _normalize_entity_types(["publication"]) == ["Publication"]

    def test_properties_alias(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        assert _normalize_entity_types(["properties"]) == ["Property"]

    def test_none_returns_none(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        assert _normalize_entity_types(None) is None

    def test_empty_list_returns_empty_list(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        result = _normalize_entity_types([])
        assert result == []  # empty list returns empty list, not None

    def test_unknown_value_passed_through(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        result = _normalize_entity_types(["fuel", "custom"])
        assert result == ["fuel", "custom"]  # unknown values passed unchanged


    def test_mixed_known_and_unknown(self) -> None:
        from nfm_mcp.tools.knowledge_graph import _normalize_entity_types

        result = _normalize_entity_types(["material", "custom"])
        assert result == ["Material", "custom"]


class TestQueryKnowledgeGraphInput:
    """Tests for QueryKnowledgeGraphInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        inp = QueryKnowledgeGraphInput(query="UO2")
        assert inp.query == "UO2"
        assert inp.limit == 20

    def test_query_required(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        with pytest.raises(ValidationError):
            QueryKnowledgeGraphInput()

    def test_limit_ge_1(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        with pytest.raises(ValidationError):
            QueryKnowledgeGraphInput(query="UO2", limit=0)

    def test_limit_le_100(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        with pytest.raises(ValidationError):
            QueryKnowledgeGraphInput(query="UO2", limit=101)

    def test_entity_types_optional(self) -> None:
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        inp = QueryKnowledgeGraphInput(query="UO2", entity_types=None)
        assert inp.entity_types is None

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.knowledge_graph import QueryKnowledgeGraphInput

        with pytest.raises(ValidationError):
            QueryKnowledgeGraphInput(query="UO2", bad_field="x")
