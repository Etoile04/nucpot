"""Tests for material search and retrieval MCP tools.

Covers search_materials and get_material: happy path, input validation,
edge cases, error handling, and JSON output structure.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_mcp.server import create_mcp_server


# ── Helpers ──────────────────────────────────────────────────────


def _get_tool(name: str):
    """Get a registered tool callable by name."""
    mcp = create_mcp_server()
    return mcp._tool_manager._tools[name].fn


def _mock_session_gen(session: MagicMock | None = None):
    """Create a mock get_db_session that yields *session*."""
    sess = session or MagicMock()

    async def _gen():
        yield sess

    return _gen


# ── search_materials ────────────────────────────────────────────


class TestSearchMaterials:
    """Unit tests for the search_materials MCP tool."""

    @pytest.mark.asyncio
    async def test_valid_query_returns_results(self) -> None:
        """Happy path: valid query returns JSON with search results."""
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({
            "items": [{"id": str(uuid.uuid4()), "name": "UO2"}],
            "total": 1,
            "page": 1,
        })

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("search_materials")
            result = json.loads(await handler(query="UO2"))

        assert "items" in result or "total" in result or isinstance(result, list)

    @pytest.mark.asyncio
    async def test_limit_parameter_passed_through(self) -> None:
        """Tool passes limit to the service layer."""
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_search,
        ):
            handler = _get_tool("search_materials")
            await handler(query="fuel", limit=10)

        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args
        assert call_kwargs[1]["limit"] == 10

    @pytest.mark.asyncio
    async def test_default_limit_is_20(self) -> None:
        """Default limit should be 20 when not specified."""
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_search,
        ):
            handler = _get_tool("search_materials")
            await handler(query="test")

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["limit"] == 20

    @pytest.mark.asyncio
    async def test_pagination_offset_calculates_page(self) -> None:
        """Offset should be converted to page number."""
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_search,
        ):
            handler = _get_tool("search_materials")
            await handler(query="test", offset=40, limit=20)

        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["page"] == 3  # offset=40, limit=20 -> page=3

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        """Service errors return JSON error, not stack traces."""
        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection lost"),
            ),
        ):
            handler = _get_tool("search_materials")
            result = json.loads(await handler(query="UO2"))

        assert "error" in result
        assert "Search failed" in result["error"]
        assert "DB connection lost" in result["error"]
        # No DB credentials should leak
        assert "postgresql" not in result["error"].lower()
        assert "password" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_empty_results_returns_valid_json(self) -> None:
        """Empty search results still return valid JSON."""
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0, "page": 1})

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("search_materials")
            result = await handler(query="nonexistent_xyz_123")
            # Should not raise — valid JSON string
            parsed = json.loads(result)
            assert parsed is not None


# ── get_material ────────────────────────────────────────────────


class TestGetMaterial:
    """Unit tests for the get_material MCP tool."""

    @pytest.mark.asyncio
    async def test_valid_uuid_returns_detail(self) -> None:
        """Valid UUID returns material detail JSON."""
        material_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({
            "id": str(material_id),
            "name": "UO2",
            "composition": "UO2",
        })

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.get_material",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("get_material")
            result = json.loads(await handler(material_id=str(material_id)))

        assert result["id"] == str(material_id)
        assert result["name"] == "UO2"

    @pytest.mark.asyncio
    async def test_invalid_uuid_format_returns_error(self) -> None:
        """Non-UUID material_id returns a user-friendly error."""
        handler = _get_tool("get_material")
        result = json.loads(await handler(material_id="not-a-uuid"))

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_valid_uuid_not_found_returns_error(self) -> None:
        """Valid UUID format but no matching material returns error."""
        material_id = uuid.uuid4()

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.get_material",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            handler = _get_tool("get_material")
            result = json.loads(await handler(material_id=str(material_id)))

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        """Service errors return JSON error, not stack traces."""
        material_id = uuid.uuid4()

        with (
            patch("nfm_mcp.tools.materials.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.material_service.get_material",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Connection refused"),
            ),
        ):
            handler = _get_tool("get_material")
            result = json.loads(await handler(material_id=str(material_id)))

        assert "error" in result
        assert "Lookup failed" in result["error"]
        # No DB internals leak
        assert "postgresql://" not in result["error"]

    @pytest.mark.asyncio
    async def test_empty_string_id_returns_error(self) -> None:
        """Empty material_id returns UUID format error."""
        handler = _get_tool("get_material")
        result = json.loads(await handler(material_id=""))

        assert "error" in result


# ── Helper function ───────────────────────────────────────────


class TestMaterialTypeToCategoryId:
    """Tests for the _material_type_to_category_id helper."""

    def test_returns_none_for_known_types(self) -> None:
        """Currently all types return None (future iteration)."""
        from nfm_mcp.tools.materials import _material_type_to_category_id

        assert _material_type_to_category_id("fuel") is None
        assert _material_type_to_category_id("cladding") is None
        assert _material_type_to_category_id("coolant") is None

    def test_returns_none_for_unknown_types(self) -> None:
        """Unknown types also return None."""
        from nfm_mcp.tools.materials import _material_type_to_category_id

        assert _material_type_to_category_id("unknown_type") is None


# ── Pydantic models ────────────────────────────────────────────


class TestSearchMaterialsInput:
    """Tests for SearchMaterialsInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.materials import SearchMaterialsInput

        inp = SearchMaterialsInput(query="UO2", limit=10)
        assert inp.query == "UO2"
        assert inp.limit == 10

    def test_query_min_length(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.materials import SearchMaterialsInput

        with pytest.raises(ValidationError):
            SearchMaterialsInput(query="")

    def test_limit_ge_1(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.materials import SearchMaterialsInput

        with pytest.raises(ValidationError):
            SearchMaterialsInput(query="UO2", limit=0)

    def test_limit_le_100(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.materials import SearchMaterialsInput

        with pytest.raises(ValidationError):
            SearchMaterialsInput(query="UO2", limit=101)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.materials import SearchMaterialsInput

        with pytest.raises(ValidationError):
            SearchMaterialsInput(query="UO2", extra_field="bad")

    def test_whitespace_stripped(self) -> None:
        from nfm_mcp.tools.materials import SearchMaterialsInput

        inp = SearchMaterialsInput(query="  UO2  ")
        assert inp.query == "UO2"
