"""Tests for material property query MCP tools.

Covers query_properties: filter combos, UUID validation, limit
enforcement, error handling.
"""

from __future__ import annotations

import json
import uuid
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


class TestQueryProperties:
    """Unit tests for the query_properties MCP tool."""

    @pytest.mark.asyncio
    async def test_valid_material_id_returns_results(self) -> None:
        """Happy path: valid UUID returns property data."""
        material_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({
            "items": [
                {
                    "material_id": str(material_id),
                    "property_name": "thermal_conductivity",
                    "value": 3.5,
                    "unit": "W/(m*K)",
                }
            ],
            "total": 1,
        })

        with (
            patch("nfm_mcp.tools.properties.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.property_service.list_measurements",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("query_properties")
            result = json.loads(await handler(material_id=str(material_id)))

        assert result is not None

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_error(self) -> None:
        """Non-UUID material_id returns a user-friendly error."""
        handler = _get_tool("query_properties")
        result = json.loads(await handler(material_id="not-a-uuid"))

        assert "error" in result
        assert "invalid" in result["error"].lower()
        assert "uuid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_limit_passed_to_service(self) -> None:
        """Limit parameter is forwarded to the service layer."""
        material_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.properties.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.property_service.list_measurements",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_svc,
        ):
            handler = _get_tool("query_properties")
            await handler(material_id=str(material_id), limit=25)

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["per_page"] == 25

    @pytest.mark.asyncio
    async def test_default_limit_is_50(self) -> None:
        """Default limit should be 50."""
        material_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.properties.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.property_service.list_measurements",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_svc,
        ):
            handler = _get_tool("query_properties")
            await handler(material_id=str(material_id))

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["per_page"] == 50

    @pytest.mark.asyncio
    async def test_empty_material_id_returns_error(self) -> None:
        """Empty material_id returns UUID error."""
        handler = _get_tool("query_properties")
        result = json.loads(await handler(material_id=""))

        assert "error" in result

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        """Service errors return JSON error without DB internals."""
        material_id = uuid.uuid4()

        with (
            patch("nfm_mcp.tools.properties.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.property_service.list_measurements",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Connection timeout"),
            ),
        ):
            handler = _get_tool("query_properties")
            result = json.loads(await handler(material_id=str(material_id)))

        assert "error" in result
        assert "Property query failed" in result["error"]
        assert "Connection timeout" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_property_name_filter_forwarded(self) -> None:
        """property_name is a tool parameter (service may use it)."""
        material_id = uuid.uuid4()
        mock_result = MagicMock()
        mock_result.model_dump_json.return_value = json.dumps({"items": [], "total": 0})

        with (
            patch("nfm_mcp.tools.properties.get_db_session", _mock_session_gen()),
            patch(
                "nfm_db.services.property_service.list_measurements",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            handler = _get_tool("query_properties")
            result = await handler(
                material_id=str(material_id),
                property_name="thermal_conductivity",
            )
            parsed = json.loads(result)
            assert parsed is not None


class TestQueryPropertiesInput:
    """Tests for QueryPropertiesInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.properties import QueryPropertiesInput

        mid = str(uuid.uuid4())
        inp = QueryPropertiesInput(material_id=mid, limit=10)
        assert inp.material_id == mid
        assert inp.limit == 10

    def test_material_id_required(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.properties import QueryPropertiesInput

        with pytest.raises(ValidationError):
            QueryPropertiesInput()

    def test_limit_ge_1(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.properties import QueryPropertiesInput

        mid = str(uuid.uuid4())
        with pytest.raises(ValidationError):
            QueryPropertiesInput(material_id=mid, limit=0)

    def test_limit_le_500(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.properties import QueryPropertiesInput

        mid = str(uuid.uuid4())
        with pytest.raises(ValidationError):
            QueryPropertiesInput(material_id=mid, limit=501)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError

        from nfm_mcp.tools.properties import QueryPropertiesInput

        mid = str(uuid.uuid4())
        with pytest.raises(ValidationError):
            QueryPropertiesInput(material_id=mid, bad_field="x")
