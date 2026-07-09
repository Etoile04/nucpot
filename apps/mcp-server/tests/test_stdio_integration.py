"""Integration tests for MCP server stdio transport.

Starts the server as a subprocess, sends JSON-RPC 2.0 messages over
stdin, and verifies responses on stdout.

These tests are skipped by default. Run with::

    uv run pytest -m integration --no-cov
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

import pytest

from nfm_mcp.server import EXPECTED_TOOL_NAMES

# MCP JSON-RPC protocol helpers

INITIALIZE_REQUEST: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.1.0"},
    },
}

TOOLS_LIST_REQUEST: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/list",
    "params": {},
}


def _read_response(proc: subprocess.Popen[str], timeout: float = 10.0) -> dict[str, Any]:
    """Read a single JSON-RPC response from the server's stdout."""
    line = proc.stdout.readline().strip()
    if not line:
        pytest.fail(f"Server closed stdout (rc={proc.poll()})")
    return json.loads(line)


def _send_request(proc: subprocess.Popen[str], request: dict[str, Any]) -> None:
    """Write a JSON-RPC request to the server's stdin."""
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()


def _start_server() -> subprocess.Popen[str]:
    """Launch the MCP server as a subprocess with stdio transport."""
    return subprocess.Popen(
        [sys.executable, "-c", "from nfm_mcp.server import main; main()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="src",
    )


@pytest.fixture()
def server_proc() -> subprocess.Popen[str]:
    """Yield a running server subprocess, terminated after the test."""
    proc = _start_server()
    # Give the server a moment to initialize
    time.sleep(0.5)
    if proc.poll() is not None:
        stderr = proc.stderr.read()
        pytest.fail(f"Server exited early with rc={proc.returncode}: {stderr}")
    try:
        yield proc
    finally:
        proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.integration
class TestStdioStartup:
    """Verify the MCP server starts via stdio and responds to protocol messages."""

    def test_initialize_handshake(self, server_proc: subprocess.Popen[str]) -> None:
        """Server should respond to the MCP initialize request."""
        _send_request(server_proc, INITIALIZE_REQUEST)
        resp = _read_response(server_proc)

        assert resp.get("jsonrpc") == "2.0"
        assert resp.get("id") == 1
        result = resp.get("result")
        assert result is not None, f"Expected 'result', got: {resp}"

        server_info = result.get("serverInfo", {})
        assert server_info.get("name") == "nfm_mcp", (
            f"Expected server name 'nfm_mcp', got: {server_info.get('name')}"
        )
        assert "version" in server_info
        assert result.get("protocolVersion") == "2024-11-05"

    def test_tools_list_returns_all_tools(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """Server should list all 9 registered tools."""
        # Must initialize first (MCP protocol requirement)
        _send_request(server_proc, INITIALIZE_REQUEST)
        _read_response(server_proc)

        _send_request(server_proc, TOOLS_LIST_REQUEST)
        resp = _read_response(server_proc)

        assert resp.get("jsonrpc") == "2.0"
        assert resp.get("id") == 2
        result = resp.get("result")
        assert result is not None, f"Expected 'result', got: {resp}"

        tools = result.get("tools", [])
        tool_names = {t.get("name") for t in tools}

        for expected_name in EXPECTED_TOOL_NAMES:
            assert expected_name in tool_names, (
                f"Tool {expected_name!r} not in tools/list response. "
                f"Got: {tool_names}"
            )

        assert len(tool_names) == len(EXPECTED_TOOL_NAMES), (
            f"Expected {len(EXPECTED_TOOL_NAMES)} tools, "
            f"got {len(tool_names)}: {tool_names}"
        )

    def test_tools_have_required_schema_fields(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """Each tool in the tools/list response should have name and inputSchema."""
        _send_request(server_proc, INITIALIZE_REQUEST)
        _read_response(server_proc)

        _send_request(server_proc, TOOLS_LIST_REQUEST)
        resp = _read_response(server_proc)

        tools = resp.get("result", {}).get("tools", [])
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "inputSchema" in tool, f"Tool {tool.get('name')!r} missing 'inputSchema'"
