"""Tests for invoking NFM source MCP tool functions.

Verifies the actual behavior of nfm_search_sources, nfm_import_from_zotero,
and nfm_batch_import_from_zotero through the FastMCP server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp.server import FastMCP

from nfm_mcp.tools.sources import register_source_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zotero_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mcp_server(zotero_client: MagicMock) -> FastMCP:
    server = FastMCP("test-server")
    register_source_tools(server, zotero_client)
    return server


def _extract_text(result) -> str:
    """Extract text content from MCP tool call result."""
    content_list = result[0] if result else []
    return content_list[0].text if content_list else ""


# ---------------------------------------------------------------------------
# nfm_search_sources invocation tests
# ---------------------------------------------------------------------------


class TestSearchSourcesInvocation:
    """Test nfm_search_sources tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_results(
        self, mcp_server: FastMCP
    ) -> None:
        with patch("nfm_mcp.tools.sources.semantic_search") as mock_search:
            mock_search.return_value = [
                {
                    "id": "doc1",
                    "document": "Paper about uranium",
                    "metadata": {
                        "title": "Uranium Paper",
                        "journal": "JNM",
                        "year": "2024",
                        "doi": "10.1/a",
                    },
                    "distance": 0.15,
                },
            ]
            result = await mcp_server.call_tool(
                "nfm_search_sources", {"query": "uranium fuel"}
            )
            text = _extract_text(result)
            assert "1 source(s)" in text
            assert "Uranium Paper" in text

    @pytest.mark.asyncio
    async def test_no_results(
        self, mcp_server: FastMCP
    ) -> None:
        with patch("nfm_mcp.tools.sources.semantic_search") as mock_search:
            mock_search.return_value = []
            result = await mcp_server.call_tool(
                "nfm_search_sources", {"query": "nonexistent"}
            )
            text = _extract_text(result)
            assert "No sources found" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP
    ) -> None:
        with patch("nfm_mcp.tools.sources.semantic_search") as mock_search:
            mock_search.side_effect = RuntimeError("chroma error")
            result = await mcp_server.call_tool(
                "nfm_search_sources", {"query": "test"}
            )
            text = _extract_text(result)
            assert "Error:" in text


# ---------------------------------------------------------------------------
# nfm_import_from_zotero invocation tests
# ---------------------------------------------------------------------------


class TestImportFromZoteroInvocation:
    """Test nfm_import_from_zotero tool behavior."""

    SAMPLE_ITEM = {
        "key": "Z1",
        "data": {
            "title": "Import Test Paper",
            "itemType": "journalArticle",
            "date": "2024",
            "DOI": "10.9999/test",
            "publicationTitle": "Test Journal",
            "abstractNote": "An abstract for testing.",
            "url": "https://doi.org/10.9999/test",
            "creators": [
                {"creatorType": "author", "firstName": "Alice", "lastName": "Smith"},
            ],
        },
    }

    @pytest.mark.asyncio
    async def test_success(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_item.return_value = self.SAMPLE_ITEM
        result = await mcp_server.call_tool(
            "nfm_import_from_zotero", {"zotero_key": "Z1"}
        )
        text = _extract_text(result)
        assert "Import Test Paper" in text
        assert "10.9999/test" in text
        assert "Authors    : 1" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_item.side_effect = Exception("not found")
        result = await mcp_server.call_tool(
            "nfm_import_from_zotero", {"zotero_key": "BAD"}
        )
        text = _extract_text(result)
        assert "Error:" in text


# ---------------------------------------------------------------------------
# nfm_batch_import_from_zotero invocation tests
# ---------------------------------------------------------------------------


class TestBatchImportInvocation:
    """Test nfm_batch_import_from_zotero tool behavior."""

    SAMPLE_ITEMS = [
        {
            "key": "B1",
            "data": {
                "title": "Batch Paper One",
                "itemType": "journalArticle",
                "date": "2024",
                "creators": [
                    {"creatorType": "author", "firstName": "Bob", "lastName": "Jones"},
                ],
            },
        },
        {
            "key": "B2",
            "data": {
                "title": "Batch Paper Two",
                "itemType": "journalArticle",
                "date": "2023",
                "creators": [],
            },
        },
    ]

    @pytest.mark.asyncio
    async def test_from_collection(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.return_value = self.SAMPLE_ITEMS
        result = await mcp_server.call_tool(
            "nfm_batch_import_from_zotero", {"collection_key": "COL1"}
        )
        text = _extract_text(result)
        assert "2 source(s)" in text
        assert "Batch Paper One" in text

    @pytest.mark.asyncio
    async def test_from_recent(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_recent_items.return_value = self.SAMPLE_ITEMS
        result = await mcp_server.call_tool(
            "nfm_batch_import_from_zotero", {}
        )
        text = _extract_text(result)
        assert "2 source(s)" in text
        assert "Batch Paper One" in text

    @pytest.mark.asyncio
    async def test_empty_results(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.return_value = []
        result = await mcp_server.call_tool(
            "nfm_batch_import_from_zotero", {"collection_key": "EMPTY"}
        )
        text = _extract_text(result)
        assert "No items found" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.side_effect = Exception("err")
        result = await mcp_server.call_tool(
            "nfm_batch_import_from_zotero", {"collection_key": "X"}
        )
        text = _extract_text(result)
        assert "Error:" in text
