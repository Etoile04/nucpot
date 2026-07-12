"""Integration tests for materials MCP tools (Phase B).

Tests that search_materials and get_material produce correctly-shaped
JSON when backed by real service calls.  The DB session and service
layer are both mocked to isolate the MCP tool logic.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── search_materials ────────────────────────────────────────────


class TestSearchMaterialsTool:
    """Integration tests for the search_materials MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_json_paginated_shape(self) -> None:
        """search_materials should return JSON matching PaginatedResponse."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_db.schemas.material import MaterialResponse
        from nfm_mcp.server import create_mcp_server

        now = _now()
        material = MaterialResponse(
            id=uuid.uuid4(),
            name="Uranium Dioxide",
            formula="UO2",
            crystal_structure="Fluorite (Fm-3m)",
            category_id=None,
            description="Primary nuclear fuel material.",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        expected = PaginatedResponse(
            items=[material],
            total=1,
            page=1,
            limit=20,
            pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.materials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_materials"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        mock_svc.assert_called_once()
        result = json.loads(result_str)
        assert "items" in result
        assert "total" in result
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "Uranium Dioxide"
        assert result["items"][0]["formula"] == "UO2"

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """search_materials should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.materials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_materials"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        result = json.loads(result_str)
        assert "error" in result
        assert "Search failed" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_query_and_pagination(self) -> None:
        """search_materials should forward query and pagination params."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_mcp.server import create_mcp_server

        empty_result = PaginatedResponse(
            items=[], total=0, page=3, limit=5, pages=0,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.materials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.material_service.search_materials",
                new_callable=AsyncMock,
                return_value=empty_result,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_materials"].fn
            # offset=10, limit=5 => page=3
            await tool_fn(query="Zirconium", limit=5, offset=10)

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["query"] == "Zirconium"
        assert call_kwargs["limit"] == 5
        assert call_kwargs["page"] == 3


# ── get_material ─────────────────────────────────────────────────


class TestGetMaterialTool:
    """Integration tests for the get_material MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_material_detail(self) -> None:
        """get_material should return JSON matching MaterialDetailResponse."""
        from nfm_db.schemas.material import MaterialDetailResponse
        from nfm_mcp.server import create_mcp_server

        now = _now()
        material_id = uuid.uuid4()
        expected = MaterialDetailResponse(
            id=material_id,
            name="UO2",
            formula="UO2",
            crystal_structure="Fluorite",
            category_id=None,
            description="Test material",
            is_active=True,
            aliases=[],
            composition=[],
            created_at=now,
            updated_at=now,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.materials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.material_service.get_material",
                new_callable=AsyncMock,
                return_value=expected,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_material"].fn
            result_str = await tool_fn(material_id=str(material_id))

        result = json.loads(result_str)
        assert result["id"] == str(material_id)
        assert result["name"] == "UO2"
        assert "aliases" in result
        assert "composition" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_invalid_uuid(self) -> None:
        """get_material should return error for non-UUID input."""
        from nfm_mcp.server import create_mcp_server

        mcp = create_mcp_server()
        tool_fn = mcp._tool_manager._tools["get_material"].fn
        result_str = await tool_fn(material_id="UO2")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_material_missing(self) -> None:
        """get_material should return error when service returns None."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.materials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.material_service.get_material",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_material"].fn
            result_str = await tool_fn(material_id=str(uuid.uuid4()))

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]
