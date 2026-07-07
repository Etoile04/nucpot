"""Tests for invoking Zotero MCP tool functions.

Verifies the actual behavior of each registered tool by calling
them through the FastMCP server with a mocked ZoteroClient.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp.server import FastMCP

from nfm_mcp.tools.zotero import register_zotero_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def zotero_client() -> MagicMock:
    """Mock ZoteroClient."""
    return MagicMock()


@pytest.fixture()
def mcp_server(zotero_client: MagicMock) -> FastMCP:
    """FastMCP with all 9 Zotero tools registered."""
    server = FastMCP("test-server")
    register_zotero_tools(server, zotero_client)
    return server


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_text(result) -> str:
    """Extract text content from MCP tool call result."""
    content_list = result[0] if result else []
    return content_list[0].text if content_list else ""


# ---------------------------------------------------------------------------
# Read tool invocation tests
# ---------------------------------------------------------------------------


class TestSearchLibraryInvocation:
    """Test zotero_search_library tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_results(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.search_items.return_value = [
            {"key": "A1", "data": {"title": "Paper A"}},
        ]
        result = await mcp_server.call_tool("zotero_search_library", {"query": "uranium"})
        text = _extract_text(result)
        assert "Paper A" in text
        assert "1 item(s)" in text

    @pytest.mark.asyncio
    async def test_returns_no_results(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.search_items.return_value = []
        result = await mcp_server.call_tool("zotero_search_library", {"query": "nothing"})
        text = _extract_text(result)
        assert "No results found" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.search_items.side_effect = RuntimeError("connection failed")
        result = await mcp_server.call_tool("zotero_search_library", {"query": "test"})
        text = _extract_text(result)
        assert "Error:" in text
        assert "connection failed" in text


class TestGetCollectionsInvocation:
    """Test zotero_get_collections tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_collections(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collections.return_value = [
            {"key": "C1", "data": {"name": "My Papers", "numItems": 5}},
        ]
        result = await mcp_server.call_tool("zotero_get_collections", {})
        text = _extract_text(result)
        assert "My Papers" in text
        assert "5 items" in text

    @pytest.mark.asyncio
    async def test_empty_collections(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collections.return_value = []
        result = await mcp_server.call_tool("zotero_get_collections", {})
        text = _extract_text(result)
        assert "no collections" in text.lower()

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collections.side_effect = Exception("api error")
        result = await mcp_server.call_tool("zotero_get_collections", {})
        text = _extract_text(result)
        assert "Error:" in text


class TestGetCollectionItemsInvocation:
    """Test zotero_get_collection_items tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_items(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.return_value = [
            {"key": "X1", "data": {"title": "Item X"}},
        ]
        result = await mcp_server.call_tool(
            "zotero_get_collection_items", {"collection_key": "ABC123"}
        )
        text = _extract_text(result)
        assert "Item X" in text

    @pytest.mark.asyncio
    async def test_empty_collection(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.return_value = []
        result = await mcp_server.call_tool(
            "zotero_get_collection_items", {"collection_key": "EMPTY"}
        )
        text = _extract_text(result)
        assert "No items found" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_collection_items.side_effect = Exception("fail")
        result = await mcp_server.call_tool(
            "zotero_get_collection_items", {"collection_key": "X"}
        )
        text = _extract_text(result)
        assert "Error:" in text


class TestGetItemDetailsInvocation:
    """Test zotero_get_item_details tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_details(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_item.return_value = {
            "data": {"title": "Deep Paper", "DOI": "10.1234/x"},
        }
        result = await mcp_server.call_tool(
            "zotero_get_item_details", {"item_key": "K1"}
        )
        text = _extract_text(result)
        assert "Deep Paper" in text
        assert "10.1234/x" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_item.side_effect = Exception("not found")
        result = await mcp_server.call_tool(
            "zotero_get_item_details", {"item_key": "BAD"}
        )
        text = _extract_text(result)
        assert "Error:" in text


class TestGetRecentItemsInvocation:
    """Test zotero_get_recent_items tool behavior."""

    @pytest.mark.asyncio
    async def test_returns_items(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_recent_items.return_value = [
            {"key": "R1", "data": {"title": "Recent Paper"}},
        ]
        result = await mcp_server.call_tool("zotero_get_recent_items", {})
        text = _extract_text(result)
        assert "Recent Paper" in text
        assert "1 item(s)" in text

    @pytest.mark.asyncio
    async def test_empty_recent(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_recent_items.return_value = []
        result = await mcp_server.call_tool("zotero_get_recent_items", {})
        text = _extract_text(result)
        assert "No items found" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.get_recent_items.side_effect = Exception("timeout")
        result = await mcp_server.call_tool("zotero_get_recent_items", {})
        text = _extract_text(result)
        assert "Error:" in text


# ---------------------------------------------------------------------------
# Write tool invocation tests
# ---------------------------------------------------------------------------


class TestAddArticleInvocation:
    """Test zotero_add_article tool behavior."""

    @pytest.mark.asyncio
    async def test_success(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_article.return_value = {
            "successful": {"0": {"key": "NEW1"}},
        }
        result = await mcp_server.call_tool(
            "zotero_add_article",
            {"title": "Test Paper", "doi": "10.1/a"},
        )
        text = _extract_text(result)
        assert "Test Paper" in text
        assert "NEW1" in text

    @pytest.mark.asyncio
    async def test_failure_response(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_article.return_value = {"successful": {}}
        result = await mcp_server.call_tool(
            "zotero_add_article", {"title": "Fail Paper"}
        )
        text = _extract_text(result)
        assert "Failed" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_article.side_effect = Exception("api down")
        result = await mcp_server.call_tool(
            "zotero_add_article", {"title": "Err"}
        )
        text = _extract_text(result)
        assert "Error:" in text


class TestAddMultipleArticlesInvocation:
    """Test zotero_add_multiple_articles tool behavior."""

    @pytest.mark.asyncio
    async def test_success(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_multiple_articles.return_value = {
            "successful": {"0": {"key": "A"}, "1": {"key": "B"}},
            "failed": {},
        }
        result = await mcp_server.call_tool(
            "zotero_add_multiple_articles",
            {"articles": [{"title": "A"}, {"title": "B"}]},
        )
        text = _extract_text(result)
        assert "2 of 2" in text

    @pytest.mark.asyncio
    async def test_partial_failure(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_multiple_articles.return_value = {
            "successful": {"0": {"key": "A"}},
            "failed": {"1": {"key": "B"}},
        }
        result = await mcp_server.call_tool(
            "zotero_add_multiple_articles",
            {"articles": [{"title": "A"}, {"title": "B"}]},
        )
        text = _extract_text(result)
        assert "1 of 2" in text
        assert "Failed: 1" in text

    @pytest.mark.asyncio
    async def test_with_collection(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_multiple_articles.return_value = {
            "successful": {},
            "failed": {},
        }
        result = await mcp_server.call_tool(
            "zotero_add_multiple_articles",
            {"articles": [{"title": "X"}], "collection_key": "C1"},
        )
        text = _extract_text(result)
        assert "C1" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_multiple_articles.side_effect = Exception("err")
        result = await mcp_server.call_tool(
            "zotero_add_multiple_articles", {"articles": []}
        )
        text = _extract_text(result)
        assert "Error:" in text


class TestCreateCollectionInvocation:
    """Test zotero_create_collection tool behavior."""

    @pytest.mark.asyncio
    async def test_success(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.create_collection.return_value = {
            "successful": {"0": {"key": "COL1"}},
        }
        result = await mcp_server.call_tool(
            "zotero_create_collection", {"name": "New Folder"}
        )
        text = _extract_text(result)
        assert "New Folder" in text
        assert "COL1" in text

    @pytest.mark.asyncio
    async def test_failure(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.create_collection.return_value = {"successful": {}}
        result = await mcp_server.call_tool(
            "zotero_create_collection", {"name": "Bad"}
        )
        text = _extract_text(result)
        assert "Failed" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.create_collection.side_effect = Exception("err")
        result = await mcp_server.call_tool(
            "zotero_create_collection", {"name": "X"}
        )
        text = _extract_text(result)
        assert "Error:" in text


class TestAddItemToCollectionInvocation:
    """Test zotero_add_item_to_collection tool behavior."""

    @pytest.mark.asyncio
    async def test_success(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        result = await mcp_server.call_tool(
            "zotero_add_item_to_collection",
            {"item_key": "I1", "collection_key": "C1"},
        )
        text = _extract_text(result)
        assert "I1" in text
        assert "C1" in text

    @pytest.mark.asyncio
    async def test_handles_exception(
        self, mcp_server: FastMCP, zotero_client: MagicMock
    ) -> None:
        zotero_client.add_item_to_collection.side_effect = Exception("err")
        result = await mcp_server.call_tool(
            "zotero_add_item_to_collection",
            {"item_key": "I1", "collection_key": "C1"},
        )
        text = _extract_text(result)
        assert "Error:" in text
