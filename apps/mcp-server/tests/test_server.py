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

    @pytest.mark.parametrize(
        "env_var,value,field",
        [
            ("NFM_MCP_DATABASE_URL", "sqlite+aiosqlite:///test.db", "database_url"),
            ("NFM_MCP_DATABASE_POOL_SIZE", "10", "database_pool_size"),
            ("NFM_MCP_DATABASE_POOL_TIMEOUT", "60.0", "database_pool_timeout"),
            ("NFM_MCP_API_BASE_URL", "http://custom:9999/v1", "api_base_url"),
            ("NFM_MCP_API_TIMEOUT", "45.0", "api_timeout"),
            ("NFM_MCP_KG_SERVICE_URL", "http://custom-kg:8002", "kg_service_url"),
            ("NFM_MCP_TRANSPORT", "sse", "transport"),
            ("NFM_MCP_HOST", "0.0.0.0", "host"),
            ("NFM_MCP_PORT", "9090", "port"),
            ("NFM_MCP_LOG_LEVEL", "DEBUG", "log_level"),
            ("NFM_MCP_LLM_SERVICE_URL", "http://custom-llm:8004", "llm_service_url"),
        ],
    )
    def test_all_env_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_var: str,
        value: str,
        field: str,
    ) -> None:
        monkeypatch.setenv(env_var, value)
        s = Settings(_env_file=None)
        actual = getattr(s, field)
        int_fields = {"database_pool_size", "port"}
        float_fields = {"database_pool_timeout", "api_timeout"}
        if field in int_fields:
            expected: str | int | float = int(value)
        elif field in float_fields:
            expected = float(value)
        else:
            expected = value
        assert actual == expected

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

    def test_settings_has_database_url(self) -> None:
        """Settings must expose database_url for the async engine."""
        s = Settings(_env_file=None)
        assert s.database_url
        assert "postgresql" in s.database_url or "sqlite" in s.database_url

    def test_settings_has_pool_config(self) -> None:
        """Settings must expose pool size and timeout."""
        s = Settings(_env_file=None)
        assert s.database_pool_size > 0
        assert s.database_pool_timeout > 0
