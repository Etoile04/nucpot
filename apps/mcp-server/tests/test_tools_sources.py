"""Tests for source search MCP tools.

Covers search_sources: query filtering by title/journal/doi,
limit enforcement, empty results, error handling.
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


def _make_source_item(title: str = "Paper", journal: str | None = None, doi: str | None = None) -> MagicMock:
    """Create a mock source item that behaves like an ORM model."""
    item = MagicMock()
    item.title = title
    item.journal = journal
    item.doi = doi
    item.model_dump.return_value = {
        "id": "src-1",
        "title": title,
        "journal": journal,
        "doi": doi,
    }
    return item


def _make_source_result(items: list | None = None) -> MagicMock:
    """Create a mock list_sources result."""
    result = MagicMock()
    result.items = items or []
    return result


# Patch targets
_SVC = "nfm_db.services.source_service.list_sources"
_DB = "nfm_mcp.tools.sources.get_db_session"


class TestSearchSources:
    """Unit tests for the search_sources MCP tool."""

    @pytest.mark.asyncio
    async def test_valid_query_returns_results(self) -> None:
        item = _make_source_item("Thermal conductivity of UO2", "J. Nucl. Mater.")
        mock_result = _make_source_result([item])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="UO2"))
        assert "items" in result
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Thermal conductivity of UO2"

    @pytest.mark.asyncio
    async def test_query_filters_by_title(self) -> None:
        item1 = _make_source_item("UO2 thermal conductivity")
        item2 = _make_source_item("Unrelated paper")
        mock_result = _make_source_result([item1, item2])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="UO2"))
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "UO2 thermal conductivity"

    @pytest.mark.asyncio
    async def test_query_filters_by_journal(self) -> None:
        item1 = _make_source_item("Paper", "Journal of Nuclear Materials")
        item2 = _make_source_item("Paper", "Other Journal")
        mock_result = _make_source_result([item1, item2])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="Nuclear Materials"))
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_query_filters_by_doi(self) -> None:
        item1 = _make_source_item("Paper", doi="10.1016/j.jnucmat.2024.01.001")
        item2 = _make_source_item("Paper")
        mock_result = _make_source_result([item1, item2])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="10.1016"))
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self) -> None:
        mock_result = _make_source_result([_make_source_item("Unrelated")])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="NONEXISTENT_XYZ_123"))
        assert result["total"] == 0
        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_limit_passed_to_service(self) -> None:
        mock_result = _make_source_result([])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result) as mock_svc,
        ):
            handler = _get_tool("search_sources")
            await handler(query="test", limit=15)
            call_kwargs = mock_svc.call_args[1]
            assert call_kwargs["per_page"] == 15

    @pytest.mark.asyncio
    async def test_service_error_returns_user_friendly_json(self) -> None:
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, side_effect=RuntimeError("DB down")),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="UO2"))
        assert "error" in result
        assert "Source search failed" in result["error"]
        assert "postgresql" not in result["error"].lower()

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self) -> None:
        item = _make_source_item("UO2 THERMAL CONDUCTIVITY")
        mock_result = _make_source_result([item])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = json.loads(await handler(query="uo2 thermal"))
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        mock_result = _make_source_result([])
        with (
            patch(_DB, _mock_session_gen()),
            patch(_SVC, new_callable=AsyncMock, return_value=mock_result),
        ):
            handler = _get_tool("search_sources")
            result = await handler(query="NONEXISTENT_XYZ")
        parsed = json.loads(result)
        assert parsed is not None


class TestSearchSourcesInput:
    """Tests for SearchSourcesInput validation model."""

    def test_valid_input(self) -> None:
        from nfm_mcp.tools.sources import SearchSourcesInput

        inp = SearchSourcesInput(query="UO2")
        assert inp.query == "UO2"
        assert inp.limit == 20

    def test_query_min_length(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.sources import SearchSourcesInput

        with pytest.raises(ValidationError):
            SearchSourcesInput(query="")

    def test_limit_ge_1(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.sources import SearchSourcesInput

        with pytest.raises(ValidationError):
            SearchSourcesInput(query="UO2", limit=0)

    def test_limit_le_100(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.sources import SearchSourcesInput

        with pytest.raises(ValidationError):
            SearchSourcesInput(query="UO2", limit=101)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError
        from nfm_mcp.tools.sources import SearchSourcesInput

        with pytest.raises(ValidationError):
            SearchSourcesInput(query="UO2", bad_field="x")
