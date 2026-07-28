"""Integration test: when Zotero credentials are configured, the 9
zotero_* tools show up on the server alongside the existing 9.

This is the wiring test for the 3-gaps fix: it proves the previously
dead ``register_zotero_tools`` function is now invoked from
``create_mcp_server`` when env vars are set.
"""

from __future__ import annotations

import pytest

from nfm_mcp.server import create_mcp_server


@pytest.mark.asyncio
async def test_zotero_tools_appear_when_creds_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Zotero creds set, the 9 zotero_* tools are registered."""
    monkeypatch.setenv("NFM_MCP_ZOTERO_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("NFM_MCP_ZOTERO_USER_ID", "12345")
    monkeypatch.setenv("NFM_MCP_ZOTERO_LIBRARY_TYPE", "user")

    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}

    # Existing 9 baseline tools
    assert "search_materials" in names
    assert "trigger_extraction" in names

    # 9 new Zotero tools
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
    missing = expected_zotero - names
    assert not missing, f"missing zotero tools: {missing}"
    # Total tool count = 9 baseline + 9 zotero = 18
    assert len(tools) == 18


@pytest.mark.asyncio
async def test_zotero_tools_absent_when_creds_missing() -> None:
    """Without Zotero creds, only the 9 baseline tools are registered."""
    # Explicitly clear env vars in case the host shell has them set.
    import os

    for var in (
        "NFM_MCP_ZOTERO_API_KEY",
        "NFM_MCP_ZOTERO_USER_ID",
        "NFM_MCP_ZOTERO_LIBRARY_TYPE",
    ):
        os.environ.pop(var, None)

    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    zotero_tools = {n for n in names if n.startswith("zotero_")}
    assert zotero_tools == set(), f"unexpected zotero tools: {zotero_tools}"
    assert len(tools) == 9


@pytest.mark.asyncio
async def test_zotero_tools_absent_when_only_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With only an API key but no user_id, Zotero tools are not registered."""
    monkeypatch.setenv("NFM_MCP_ZOTERO_API_KEY", "fake-key")
    monkeypatch.delenv("NFM_MCP_ZOTERO_USER_ID", raising=False)

    mcp = create_mcp_server()
    tools = await mcp.list_tools()
    zotero_tools = [t for t in tools if t.name.startswith("zotero_")]
    assert zotero_tools == []