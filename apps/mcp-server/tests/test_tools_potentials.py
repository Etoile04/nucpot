"""Tests for thermodynamic potential query MCP tools.

Covers query_potentials: material_id filter, potential_type filter,
model_name filter, empty results, error handling.
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


class TestQueryPotentials:
    """Unit tests for the query_potentials MCP tool."""

    @pytest.mark.asyncio
    async def test_valid_material_returns_results(self) -> None:
        """Happy path: returns potential model records."""
        mock_result = MagicMock()
        mock_result.potentials = []
        mock_result.model_dump_json.return_value = json.dumps({"potentials": []})

        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("query_potentials")
            result = json.loads(await handler(material_id="UO2"))

        assert "potentials" in result or isinstance(result, list)

    @pytest.mark.asyncio
    async def test_model_name_used_as_search_query(self) -> None:
        """When model_name is provided, it is used as the search query."""
        mock_result = MagicMock()
        mock_result.potentials = []
        mock_result.model_dump_json.return_value = json.dumps({"potentials": []})

        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_svc,
        ):
            handler = _get_tool("query_potentials")
            await handler(material_id="UO2", model_name="FINK")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["query"] == "FINK"

    @pytest.mark.asyncio
    async def test_material_id_used_as_fallback_query(self) -> None:
        """Without model_name, material_id is used as search query."""
        mock_result = MagicMock()
        mock_result.potentials = []
        mock_result.model_dump_json.return_value = json.dumps({"potentials": []})

        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_svc,
        ):
            handler = _get_tool("query_potentials")
            await handler(material_id="UO2")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["query"] == "UO2"

    @pytest.mark.asyncio
    async def test_potential_type_filter_forwarded(self) -> None:
        """potential_type is forwarded to service layer."""
        mock_result = MagicMock()
        mock_result.potentials = []
        mock_result.model_dump_json.return_value = json.dumps({"potentials": []})

        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_svc,
        ):
            handler = _get_tool("query_potentials")
            await handler(material_id="UO2", potential_type="Gibbs")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["type_filter"] == "Gibbs"

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        """Service errors return JSON error without DB internals."""
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Service unavailable"),
            ),
        ):
            handler = _get_tool("query_potentials")
            result = json.loads(await handler(material_id="UO2"))

        assert "error" in result
        assert "Potential query failed" in result["error"]
        assert "Service unavailable" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        """Result is always valid JSON regardless of data."""
        mock_result = MagicMock()
        mock_result.potentials = []
        mock_result.model_dump_json.return_value = json.dumps({"potentials": []})

        with (
            patch("nfm_mcp.tools.potentials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("query_potentials")
            result = await handler(material_id="NONEXISTENT_XYZ")

        parsed = json.loads(result)
        assert parsed is not None


class TestQueryPotentialsInput:
    """Tests for QueryPotentialsInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.potentials import QueryPotentialsInput

        inp = QueryPotentialsInput(material_id="UO2")
        assert inp.material_id == "UO2"

    def test_material_id_required(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.potentials import QueryPotentialsInput

        with pytest.raises(ValidationError):
            QueryPotentialsInput()

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.potentials import QueryPotentialsInput

        with pytest.raises(ValidationError):
            QueryPotentialsInput(material_id="UO2", bad_field="x")
