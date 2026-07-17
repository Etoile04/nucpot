"""Tests for ontology browsing MCP tools.

Covers browse_ontology: query search, parent_id lookup, entity_type
filter, limit enforcement, empty results, error handling.
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


def _make_ontology_node(
    node_id: str = "onto-1",
    label: str = "Node",
    node_type: str = "material",
) -> MagicMock:
    node = MagicMock()
    node.id = node_id
    node.label = label
    node.type = node_type
    node.model_dump.return_value = {
        "id": node_id,
        "label": label,
        "type": node_type,
    }
    return node


def _make_relationship(
    from_id: str = "n1",
    to_id: str = "n2",
    rel_type: str = "has_child",
) -> MagicMock:
    rel = MagicMock()
    rel.from_ = from_id
    rel.to = to_id
    rel.model_dump.return_value = {
        "from": from_id,
        "to": to_id,
        "type": rel_type,
    }
    return rel


def _make_ontology_result(
    nodes: list | None = None,
    relationships: list | None = None,
) -> MagicMock:
    result = MagicMock()
    result.nodes = nodes or [_make_ontology_node()]
    result.relationships = relationships or []
    result.stats = MagicMock()
    result.stats.model_dump.return_value = {"total_nodes": len(result.nodes)}
    return result


# Patch target: the lazy import inside browse_ontology
_SVC = "nfm_db.services.ontology_service.derive_ontology_graph"
_DB = "nfm_mcp.tools.ontology.get_db_session"


class TestBrowseOntology:
    """Unit tests for the browse_ontology MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_nodes_and_relationships(self) -> None:
        mock_result = _make_ontology_result()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("browse_ontology")
            result = json.loads(await handler())
        assert "nodes" in result
        assert "relationships" in result
        assert "stats" in result

    @pytest.mark.asyncio
    async def test_query_forwarded_as_corpus_id(self) -> None:
        mock_result = _make_ontology_result()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result) as mock_svc,
        ):
            handler = _get_tool("browse_ontology")
            await handler(query="fuel")
            call_kwargs = mock_svc.call_args[1]
            assert call_kwargs["corpus_id"] == "fuel"

    @pytest.mark.asyncio
    async def test_default_corpus_id_when_no_query(self) -> None:
        mock_result = _make_ontology_result()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result) as mock_svc,
        ):
            handler = _get_tool("browse_ontology")
            await handler()
            call_kwargs = mock_svc.call_args[1]
            assert call_kwargs["corpus_id"] == "nfmd/ref-gap-fill"

    @pytest.mark.asyncio
    async def test_entity_type_filter(self) -> None:
        node1 = _make_ontology_node("n1", "UO2", "material")
        node2 = _make_ontology_node("n2", "Property", "property")
        mock_result = _make_ontology_result([node1, node2], [])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("browse_ontology")
            result = json.loads(await handler(entity_type="material"))
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_parent_id_filter(self) -> None:
        node1 = _make_ontology_node("onto-root", "Root", "root")
        node2 = _make_ontology_node("onto-fuel", "Fuel", "material_category")
        mock_result = _make_ontology_result([node1, node2], [])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("browse_ontology")
            result = json.loads(await handler(parent_id="onto-root"))
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "onto-root"

    @pytest.mark.asyncio
    async def test_limit_enforced_on_nodes(self) -> None:
        nodes = [_make_ontology_node(f"n-{i}") for i in range(100)]
        mock_result = _make_ontology_result(nodes)
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("browse_ontology")
            result = json.loads(await handler(limit=5))
        assert len(result["nodes"]) <= 5

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, side_effect=RuntimeError("Service unavailable")),
        ):
            handler = _get_tool("browse_ontology")
            result = json.loads(await handler(query="fuel"))
        assert "error" in result
        assert "Ontology browse failed" in result["error"]
        assert "Service unavailable" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        mock_result = _make_ontology_result()
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("browse_ontology")
            result = await handler()
        parsed = json.loads(result)
        assert parsed is not None


class TestBrowseOntologyInput:
    """Tests for BrowseOntologyInput validation model."""

    def test_all_params_optional(self) -> None:
        from nfm_mcp.tools.ontology import BrowseOntologyInput

        inp = BrowseOntologyInput()
        assert inp.query is None
        assert inp.entity_type is None
        assert inp.parent_id is None
        assert inp.limit == 50

    def test_limit_ge_1(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.ontology import BrowseOntologyInput

        with pytest.raises(ValidationError):
            BrowseOntologyInput(limit=0)

    def test_limit_le_200(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.ontology import BrowseOntologyInput

        with pytest.raises(ValidationError):
            BrowseOntologyInput(limit=201)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.ontology import BrowseOntologyInput

        with pytest.raises(ValidationError):
            BrowseOntologyInput(query="test", bad_field="x")
