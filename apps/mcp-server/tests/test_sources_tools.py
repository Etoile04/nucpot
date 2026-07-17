"""Tests for NFM source management tools.

Verifies nfm_search_sources, nfm_import_from_zotero,
and nfm_batch_import_from_zotero tool registration and behavior.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp.server import FastMCP

from nfm_mcp.tools.sources import (
    _extract_creators,
    _item_to_source_data,
    register_source_tools,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zotero_client() -> MagicMock:
    return MagicMock(spec=object)


@pytest.fixture()
def mcp_server() -> FastMCP:
    return FastMCP("test-server")


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestSourceToolRegistration:
    """Verify NFM source tools are registered."""

    EXPECTED_TOOLS = [
        "nfm_search_sources",
        "nfm_import_from_zotero",
        "nfm_batch_import_from_zotero",
    ]

    @pytest.mark.asyncio
    async def test_all_tools_registered(self, mcp_server: FastMCP, zotero_client: MagicMock) -> None:
        register_source_tools(mcp_server, zotero_client)
        tools = await mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        for expected in self.EXPECTED_TOOLS:
            assert expected in tool_names, f"Missing tool: {expected}"

    @pytest.mark.asyncio
    async def test_tool_count(self, mcp_server: FastMCP, zotero_client: MagicMock) -> None:
        register_source_tools(mcp_server, zotero_client)
        tools = await mcp_server.list_tools()
        # Only 3 source tools expected (zotero tools not registered here)
        assert len(tools) == 3


# ---------------------------------------------------------------------------
# Data extraction tests
# ---------------------------------------------------------------------------


SAMPLE_ZOTERO_ITEM = {
    "key": "ITEM_001",
    "data": {
        "title": "Nuclear Fuel Performance Analysis",
        "itemType": "journalArticle",
        "creators": [
            {
                "creatorType": "author",
                "firstName": "Alice",
                "lastName": "Johnson",
            },
            {
                "creatorType": "author",
                "firstName": "Bob",
                "lastName": "Smith",
            },
            {
                "creatorType": "editor",
                "firstName": "Carol",
                "lastName": "Davis",
            },
        ],
        "date": "2024",
        "DOI": "10.1016/j.nucengdes.2024.01.001",
        "publicationTitle": "Journal of Nuclear Materials",
        "abstractNote": "This paper analyzes fuel performance...",
        "url": "https://doi.org/10.1016/j.nucengdes.2024.01.001",
    },
}


class TestExtractCreators:
    """Test _extract_creators helper."""

    def test_extracts_authors_only(self) -> None:
        creators = _extract_creators(SAMPLE_ZOTERO_ITEM)
        assert len(creators) == 2
        assert creators[0] == {"firstName": "Alice", "lastName": "Johnson"}
        assert creators[1] == {"firstName": "Bob", "lastName": "Smith"}

    def test_empty_creators(self) -> None:
        creators = _extract_creators({"data": {"creators": []}})
        assert creators == []

    def test_no_data_key(self) -> None:
        creators = _extract_creators({"creators": []})
        assert creators == []


class TestItemToSourceData:
    """Test _item_to_source_data conversion."""

    def test_converts_full_item(self) -> None:
        result = _item_to_source_data(SAMPLE_ZOTERO_ITEM)
        assert result["external_key"] == "ITEM_001"
        assert result["title"] == "Nuclear Fuel Performance Analysis"
        assert result["doi"] == "10.1016/j.nucengdes.2024.01.001"
        assert result["journal"] == "Journal of Nuclear Materials"
        assert result["year"] == 2024
        assert result["abstract"] == "This paper analyzes fuel performance..."
        assert result["source_type"] == "journalArticle"
        assert len(result["authors"]) == 2

    def test_handles_missing_year(self) -> None:
        item = {
            "key": "X1",
            "data": {
                "title": "No Year Paper",
                "date": "forthcoming",
                "itemType": "journalArticle",
            },
        }
        result = _item_to_source_data(item)
        assert result["year"] is None

    def test_handles_missing_doi(self) -> None:
        item = {
            "key": "X2",
            "data": {
                "title": "No DOI Paper",
                "itemType": "journalArticle",
            },
        }
        result = _item_to_source_data(item)
        assert result["doi"] == ""
