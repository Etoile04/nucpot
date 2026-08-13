"""Tests for NFM source management tools.

Verifies the ``search_sources`` tool registration and behavior.
Previous versions of this file tested ``_extract_creators`` and
``_item_to_source_data`` helpers as well as ``nfm_import_from_zotero``
and ``nfm_batch_import_from_zotero`` tools, but those were removed
when the source tools were refactored to delegate to the nfm_db
service layer.
"""

from __future__ import annotations

import typing

import pytest
from mcp.server import FastMCP

from nfm_mcp.tools.sources import register_source_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_server() -> FastMCP:
    return FastMCP("test-server")


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestSourceToolRegistration:
    """Verify NFM source tools are registered."""

    EXPECTED_TOOLS: typing.ClassVar[list[str]] = [
        "search_sources",
    ]

    @pytest.mark.asyncio
    async def test_all_tools_registered(self, mcp_server: FastMCP) -> None:
        register_source_tools(mcp_server)
        tools = await mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        for expected in self.EXPECTED_TOOLS:
            assert expected in tool_names, f"Missing tool: {expected}"

    @pytest.mark.asyncio
    async def test_tool_count(self, mcp_server: FastMCP) -> None:
        register_source_tools(mcp_server)
        tools = await mcp_server.list_tools()
        # Only 1 source tool expected (search_sources)
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_search_sources_has_readonly_annotation(
        self, mcp_server: FastMCP
    ) -> None:
        register_source_tools(mcp_server)
        tools = {t.name: t for t in await mcp_server.list_tools()}
        assert tools["search_sources"].annotations is not None
        assert tools["search_sources"].annotations.readOnlyHint is True
