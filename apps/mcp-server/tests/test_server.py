"""Tests for NFM MCP server scaffolding.

Covers server creation, tool registration, Settings loading,
and dependency injection stubs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_mcp.deps import Settings, get_settings
from nfm_mcp.server import EXPECTED_TOOL_NAMES, create_mcp_server


# ── Settings ────────────────────────────────────────────────────


class TestSettings:
    """Tests for the Settings configuration class."""

    def test_default_values(self) -> None:
        s = Settings(_env_file=None)
        assert s.transport == "stdio"
        assert s.port == 8002
        assert s.host == "127.0.0.1"
        assert s.log_level == "INFO"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFM_MCP_TRANSPORT", "sse")
        monkeypatch.setenv("NFM_MCP_PORT", "9000")
        s = Settings(_env_file=None)
        assert s.transport == "sse"
        assert s.port == 9000

    def test_invalid_port_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NFM_MCP_PORT", "abc")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_get_settings_returns_settings(self) -> None:
        assert isinstance(get_settings(), Settings)


# ── Server creation ──────────────────────────────────────────────


class TestServerCreation:
    """Tests for server instantiation and tool registration."""

    def test_create_server_returns_fastmcp(self) -> None:
        mcp = create_mcp_server()
        assert mcp.name == "nfm_mcp"

    def test_all_tools_registered(self) -> None:
        mcp = create_mcp_server()
        registered = list(mcp._tool_manager._tools.keys())
        for name in EXPECTED_TOOL_NAMES:
            assert name in registered, (
                f"Tool {name!r} not registered. Got: {registered}"
            )

    def test_tool_count_matches(self) -> None:
        mcp = create_mcp_server()
        registered = list(mcp._tool_manager._tools.keys())
        assert len(registered) == len(EXPECTED_TOOL_NAMES), (
            f"Expected {len(EXPECTED_TOOL_NAMES)} tools, "
            f"got {len(registered)}: {registered}"
        )


# ── Dependency injection ─────────────────────────────────────────


class TestDependencyInjection:
    """Tests for the dependency injection module."""

    @pytest.mark.asyncio
    async def test_get_db_session_yields_none(self) -> None:
        """Phase A stub — get_db_session should yield None."""
        from nfm_mcp.deps import get_db_session

        session = None
        async for s in get_db_session():
            session = s
        assert session is None
