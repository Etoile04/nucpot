"""Tests for the main server module.

Verifies server initialization, register_all_tools wiring,
and that all 12 tools are present on the global server instance.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from mcp.server import FastMCP

from nfm_mcp.server import mcp, register_all_tools


# ---------------------------------------------------------------------------
# Server registration tests
# ---------------------------------------------------------------------------


class TestRegisterAllTools:
    """Verify register_all_tools wires everything correctly."""

    @pytest.mark.asyncio
    async def test_registers_all_12_tools(self) -> None:
        test_mcp = FastMCP("test-wiring")
        with patch.dict(os.environ, {
            "ZOTERO_API_KEY": "test-key",
            "ZOTERO_USER_ID": "12345",
            "ZOTERO_LIB_TYPE": "user",
        }):
            register_all_tools(test_mcp)
        tools = await test_mcp.list_tools()
        assert len(tools) == 12
        names = {t.name for t in tools}
        expected_zotero = {
            "zotero_search_library",
            "zotero_get_collections",
            "zotero_get_collection_items",
            "zotero_get_item_details",
            "zotero_get_recent_items",
            "zotero_add_article",
            "zotero_add_multiple_articles",
            "zotero_create_collection",
            "zotero_add_item_to_collection",
        }
        expected_nfm = {
            "nfm_search_sources",
            "nfm_import_from_zotero",
            "nfm_batch_import_from_zotero",
        }
        assert names == expected_zotero | expected_nfm

    @pytest.mark.asyncio
    async def test_uses_env_vars_for_client(self) -> None:
        test_mcp = FastMCP("test-env")
        with patch.dict(os.environ, {
            "ZOTERO_API_KEY": "my-key",
            "ZOTERO_USER_ID": "99",
            "ZOTERO_LIB_TYPE": "group",
        }):
            with patch(
                "nfm_mcp.zotero.client.ZoteroClient"
            ) as mock_client_cls:
                register_all_tools(test_mcp)
                mock_client_cls.assert_called_once_with(
                    api_key="my-key",
                    user_id="99",
                    library_type="group",
                )

    @pytest.mark.asyncio
    async def test_default_env_values(self) -> None:
        test_mcp = FastMCP("test-defaults")
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "nfm_mcp.zotero.client.ZoteroClient"
            ) as mock_client_cls:
                register_all_tools(test_mcp)
                mock_client_cls.assert_called_once_with(
                    api_key="",
                    user_id="",
                    library_type="user",
                )


# ---------------------------------------------------------------------------
# Global server instance tests
# ---------------------------------------------------------------------------


class TestGlobalServer:
    """Verify the module-level server instance."""

    def test_global_mcp_is_fastmcp(self) -> None:
        assert isinstance(mcp, FastMCP)

    def test_server_name(self) -> None:
        assert mcp.name == "nfm-mcp-server"
