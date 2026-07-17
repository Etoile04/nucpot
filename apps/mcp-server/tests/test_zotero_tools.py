"""Tests for Zotero tool registration (9 zotero_ tools).

Verifies all 9 tools are registered on the FastMCP server with correct
names, descriptions, and annotations.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp.server import FastMCP

from nfm_mcp.tools.zotero import register_zotero_tools
from nfm_mcp.zotero.client import ZoteroClient, format_item


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zotero_client() -> MagicMock:
    """Mock ZoteroClient."""
    return MagicMock(spec=ZoteroClient)


@pytest.fixture()
def mcp_server() -> FastMCP:
    """Fresh FastMCP instance."""
    return FastMCP("test-server")


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestZoteroToolRegistration:
    """Verify all 9 Zotero tools are registered correctly."""

    EXPECTED_TOOLS = [
        "zotero_search_library",
        "zotero_get_collections",
        "zotero_get_collection_items",
        "zotero_get_item_details",
        "zotero_get_recent_items",
        "zotero_add_article",
        "zotero_add_multiple_articles",
        "zotero_create_collection",
        "zotero_add_item_to_collection",
    ]

    @pytest.mark.asyncio
    async def test_all_tools_registered(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        register_zotero_tools(mcp_server, zotero_client)
        tools = await mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        for expected in self.EXPECTED_TOOLS:
            assert expected in tool_names, f"Missing tool: {expected}"

    @pytest.mark.asyncio
    async def test_tool_count(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        register_zotero_tools(mcp_server, zotero_client)
        tools = await mcp_server.list_tools()
        assert len(tools) == 9

    @pytest.mark.asyncio
    async def test_read_tools_have_readonly_annotation(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        read_tools = {
            "zotero_search_library",
            "zotero_get_collections",
            "zotero_get_collection_items",
            "zotero_get_item_details",
            "zotero_get_recent_items",
        }
        register_zotero_tools(mcp_server, zotero_client)
        tools = {t.name: t for t in await mcp_server.list_tools()}
        for name in read_tools:
            assert tools[name].annotations is not None
            assert tools[name].annotations.read_only_hint is True


# ---------------------------------------------------------------------------
# Format helper tests
# ---------------------------------------------------------------------------


class TestFormatItem:
    """Test the format_item helper function."""

    def test_formats_complete_item(self) -> None:
        item = {
            "key": "ABC123",
            "data": {
                "title": "Test Paper",
                "creators": [
                    {
                        "creatorType": "author",
                        "lastName": "Smith",
                        "firstName": "John",
                    },
                    {
                        "creatorType": "editor",
                        "lastName": "Jones",
                        "firstName": "Jane",
                    },
                ],
                "publicationTitle": "Nature",
                "date": "2024",
                "DOI": "10.1234/test",
            },
        }
        result = format_item(item)
        assert "[ABC123] Test Paper" in result
        assert "Smith, John" in result
        assert "Jones, Jane" not in result  # editor filtered out
        assert "Nature" in result
        assert "2024" in result
        assert "10.1234/test" in result

    def test_formats_minimal_item(self) -> None:
        item = {"key": "X1", "data": {"title": "Minimal"}}
        result = format_item(item)
        assert "[X1] Minimal" in result
        assert "Authors" not in result

    def test_handles_missing_data_key(self) -> None:
        item = {"key": "Y2", "title": "No data key"}
        result = format_item(item)
        assert "[Y2]" in result
