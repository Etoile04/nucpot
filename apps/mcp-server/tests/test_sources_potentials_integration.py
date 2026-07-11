"""Integration tests for sources and potentials MCP tools (Phase B).

Tests that search_sources, get_source, query_potentials, and get_potential
produce correctly-shaped JSON when backed by real service calls.
The DB session and service layer are both mocked to isolate MCP tool logic.
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


# ── search_sources ───────────────────────────────────────────────


class TestSearchSourcesTool:
    """Integration tests for the search_sources MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_json_paginated_shape(self) -> None:
        """search_sources should return JSON matching PaginatedResponse."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_db.schemas.source import DataSourceResponse
        from nfm_mcp.server import create_mcp_server

        now = _now()
        source = DataSourceResponse(
            id=uuid.uuid4(),
            title="Thermal conductivity of UO2",
            doi="10.1016/j.jnucmat.2020.01.001",
            journal="Journal of Nuclear Materials",
            year=2020,
            source_type="journal_article",
            abstract="Measurement of UO2 thermal conductivity",
            created_at=now,
            updated_at=now,
        )
        expected = PaginatedResponse(
            items=[source],
            total=1,
            page=1,
            limit=20,
            pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        mock_svc.assert_called_once()
        result = json.loads(result_str)
        assert "items" in result
        assert "total" in result
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Thermal conductivity of UO2"

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """search_sources should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            result_str = await tool_fn(query="UO2", limit=20, offset=0)

        result = json.loads(result_str)
        assert "error" in result
        assert "Search failed" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_pagination_params(self) -> None:
        """search_sources should forward pagination params correctly."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_mcp.server import create_mcp_server

        empty_result = PaginatedResponse(
            items=[], total=0, page=3, limit=5, pages=0,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=empty_result,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            # offset=10, limit=5 => page=3
            await tool_fn(query="Zirconium", source_type="journal_article", limit=5, offset=10)

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["page"] == 3
        assert call_kwargs["per_page"] == 5
        assert call_kwargs["source_type"] == "journal_article"

    @pytest.mark.asyncio
    async def test_passes_source_type_filter(self) -> None:
        """search_sources should forward source_type to the service."""
        from nfm_db.schemas.common import PaginatedResponse
        from nfm_mcp.server import create_mcp_server

        empty_result = PaginatedResponse(
            items=[], total=0, page=1, limit=20, pages=0,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.list_sources",
                new_callable=AsyncMock,
                return_value=empty_result,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["search_sources"].fn
            await tool_fn(query="test", source_type="report")

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["source_type"] == "report"


# ── get_source ──────────────────────────────────────────────────


class TestGetSourceTool:
    """Integration tests for the get_source MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_source_detail(self) -> None:
        """get_source should return JSON matching DataSourceDetailResponse."""
        from nfm_db.schemas.source import (
            AuthorResponse,
            DataSourceAuthorResponse,
            DataSourceDetailResponse,
        )
        from nfm_mcp.server import create_mcp_server

        now = _now()
        source_id = uuid.uuid4()
        author_id = uuid.uuid4()
        link_id = uuid.uuid4()
        author = AuthorResponse(
            id=author_id,
            name="J.K. Finkelstein",
            orcid=None,
            affiliation="MIT",
            created_at=now,
            updated_at=now,
        )
        author_link = DataSourceAuthorResponse(
            id=link_id,
            data_source_id=source_id,
            author_id=author_id,
            author_order=1,
            is_corresponding=True,
            created_at=now,
            updated_at=now,
            author=author,
        )
        expected = DataSourceDetailResponse(
            id=source_id,
            title="Thermal conductivity of UO2",
            doi="10.1016/j.jnucmat.2020.01.001",
            journal="Journal of Nuclear Materials",
            year=2020,
            volume="530",
            pages="1-10",
            source_type="journal_article",
            abstract="Measurement study",
            external_url=None,
            created_at=now,
            updated_at=now,
            authors=[author_link],
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.get_source",
                new_callable=AsyncMock,
                return_value=expected,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_source"].fn
            result_str = await tool_fn(source_id=str(source_id))

        result = json.loads(result_str)
        assert result["id"] == str(source_id)
        assert result["title"] == "Thermal conductivity of UO2"
        assert "authors" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_invalid_uuid(self) -> None:
        """get_source should return error for non-UUID input."""
        from nfm_mcp.server import create_mcp_server

        mcp = create_mcp_server()
        tool_fn = mcp._tool_manager._tools["get_source"].fn
        result_str = await tool_fn(source_id="not-a-uuid")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_missing(self) -> None:
        """get_source should return error when service returns None."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.sources.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.source_service.get_source",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_source"].fn
            result_str = await tool_fn(source_id=str(uuid.uuid4()))

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]


# ── query_potentials ────────────────────────────────────────────


class TestQueryPotentialsTool:
    """Integration tests for the query_potentials MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_json_list_shape(self) -> None:
        """query_potentials should return JSON with potentials array."""
        from nfm_db.schemas.potential import PotentialListResponse, PotentialSummary
        from nfm_mcp.server import create_mcp_server

        potential = PotentialSummary(
            id=uuid.uuid4(),
            name="FINK-LUCUTA2",
            display_name="Fink-Lucuta UO2 Gibbs",
            type="Gibbs",
            elements=["U", "O"],
            description="Gibbs energy model for UO2",
        )
        expected = PotentialListResponse(
            potentials=[potential],
            total=1,
            page=1,
            limit=100,
            total_pages=1,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=expected,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_potentials"].fn
            result_str = await tool_fn(material_id="UO2")

        mock_svc.assert_called_once()
        result = json.loads(result_str)
        assert "potentials" in result
        assert "total" in result
        assert result["total"] == 1
        assert result["potentials"][0]["name"] == "FINK-LUCUTA2"

    @pytest.mark.asyncio
    async def test_handles_service_error_gracefully(self) -> None:
        """query_potentials should return JSON error on service failure."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB connection refused"),
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_potentials"].fn
            result_str = await tool_fn(material_id="UO2")

        result = json.loads(result_str)
        assert "error" in result
        assert "Query failed" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_type_and_query_filters(self) -> None:
        """query_potentials should forward type_filter and query params."""
        from nfm_db.schemas.potential import PotentialListResponse
        from nfm_mcp.server import create_mcp_server

        empty_result = PotentialListResponse(
            potentials=[], total=0, page=1, limit=100, total_pages=0,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.list_potentials",
                new_callable=AsyncMock,
                return_value=empty_result,
            ) as mock_svc,
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["query_potentials"].fn
            await tool_fn(
                material_id="UO2",
                potential_type="Gibbs",
                model_name="FINK",
            )

        call_kwargs = mock_svc.call_args[1]
        assert call_kwargs["type_filter"] == "Gibbs"
        assert call_kwargs["query"] == "FINK"


# ── get_potential ──────────────────────────────────────────────


class TestGetPotentialTool:
    """Integration tests for the get_potential MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_potential_detail(self) -> None:
        """get_potential should return JSON matching PotentialDetail."""
        from nfm_db.schemas.potential import PotentialDetail
        from nfm_mcp.server import create_mcp_server

        now = _now()
        potential_id = uuid.uuid4()
        expected = PotentialDetail(
            id=potential_id,
            name="FINK-LUCUTA2",
            display_name="Fink-Lucuta UO2 Gibbs",
            type="Gibbs",
            elements=["U", "O"],
            description="Gibbs energy model for UO2",
            applicability={
                "temperature_range": [298.15, 3138.0],
                "phases": ["fuel"],
            },
            references=[],
            created_at=now,
            updated_at=now,
        )

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.get_potential_by_id",
                new_callable=AsyncMock,
                return_value=expected,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_potential"].fn
            result_str = await tool_fn(potential_id=str(potential_id))

        result = json.loads(result_str)
        assert result["id"] == str(potential_id)
        assert result["name"] == "FINK-LUCUTA2"
        assert "applicability" in result

    @pytest.mark.asyncio
    async def test_returns_error_for_invalid_uuid(self) -> None:
        """get_potential should return error for non-UUID input."""
        from nfm_mcp.server import create_mcp_server

        mcp = create_mcp_server()
        tool_fn = mcp._tool_manager._tools["get_potential"].fn
        result_str = await tool_fn(potential_id="not-a-uuid")

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_potential_missing(self) -> None:
        """get_potential should return error when service returns None."""
        from nfm_mcp.server import create_mcp_server

        mock_session_gen = _make_session_gen()
        with (
            patch("nfm_mcp.tools.potentials.get_db_session", mock_session_gen),
            patch(
                "nfm_db.services.potential_service.get_potential_by_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mcp = create_mcp_server()
            tool_fn = mcp._tool_manager._tools["get_potential"].fn
            result_str = await tool_fn(potential_id=str(uuid.uuid4()))

        result = json.loads(result_str)
        assert "error" in result
        assert "not found" in result["error"]


# ── Temperature range helper tests (preserved from original) ──


class TestTemperatureRangeHelpers:
    """Tests for pure helper functions in potentials module."""

    def test_parse_temperature_range_valid(self) -> None:
        from nfm_mcp.tools.potentials import _parse_temperature_range

        assert _parse_temperature_range("300-3000 K") == (300.0, 3000.0)
        assert _parse_temperature_range("298.15-3138k") == (298.15, 3138.0)

    def test_parse_temperature_range_invalid(self) -> None:
        from nfm_mcp.tools.potentials import _parse_temperature_range

        assert _parse_temperature_range("not-a-range") is None
        assert _parse_temperature_range("300") is None
        assert _parse_temperature_range("abc-xyz K") is None

    def test_ranges_overlap(self) -> None:
        from nfm_mcp.tools.potentials import _ranges_overlap

        assert _ranges_overlap([300, 1500], (200, 400)) is True
        assert _ranges_overlap([300, 1500], (2000, 3000)) is False
        assert _ranges_overlap([300, 1500], (300, 1500)) is True

    def test_ranges_overlap_short(self) -> None:
        from nfm_mcp.tools.potentials import _ranges_overlap

        assert _ranges_overlap([300], (200, 400)) is False
