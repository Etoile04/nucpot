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


NOTIFICATION_INITIALIZED: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}


def _send_notification(proc: subprocess.Popen[str], notification: dict[str, Any]) -> None:
    """Write a JSON-RPC notification (no id) to the server's stdin."""
    proc.stdin.write(json.dumps(notification) + "\n")
    proc.stdin.flush()


def _init_and_confirm(proc: subprocess.Popen[str]) -> None:
    """Send initialize request, read response, send initialized notification."""
    _send_request(proc, INITIALIZE_REQUEST)
    _read_response(proc)
    _send_notification(proc, NOTIFICATION_INITIALIZED)


def _call_tool(
    proc: subprocess.Popen[str],
    tool_name: str,
    arguments: dict[str, Any],
    request_id: int = 3,
) -> dict[str, Any]:
    """Send a tools/call request and return the response."""
    request: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    _send_request(proc, request)
    return _read_response(proc)


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


@pytest.mark.integration
class TestToolCalls:
    """End-to-end tests: invoke tools via MCP JSON-RPC tools/call.

    These tests exercise the full protocol path that Claude Code uses:
    initialize → initialized notification → tools/call.

    Only mock-data tools are tested (no PostgreSQL required).
    """

    def test_browse_ontology_returns_nodes(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """browse_ontology should return a JSON list of ontology nodes."""
        _init_and_confirm(server_proc)
        resp = _call_tool(server_proc, "browse_ontology", {})

        assert resp.get("jsonrpc") == "2.0"
        assert resp.get("id") == 3
        result = resp.get("result")
        assert result is not None, f"Expected result, got: {resp}"

        content = result.get("content", [])
        assert len(content) > 0, "Expected non-empty content array"

        text_parts = [p["text"] for p in content if p.get("type") == "text"]
        assert len(text_parts) > 0, "Expected at least one text content part"

        data = json.loads(text_parts[0])
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"
        assert len(data) > 0, "Expected non-empty ontology list"

    def test_browse_ontology_with_query_filter(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """browse_ontology with query should filter results."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "browse_ontology",
            {"query": "reactor"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert len(data) > 0, "Expected results for 'reactor' query"

    def test_query_knowledge_graph_returns_structure(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """query_knowledge_graph should return nodes and edges."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "query_knowledge_graph",
            {"query": "UO2"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert "nodes" in data, f"Expected 'nodes' key, got: {list(data.keys())}"
        assert "edges" in data, f"Expected 'edges' key, got: {list(data.keys())}"
        assert len(data["nodes"]) > 0, "Expected non-empty nodes"

    def test_search_sources_returns_results(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """search_sources should return a list of source references."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "search_sources",
            {"query": "Finkelstein"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"
        assert len(data) > 0, "Expected non-empty sources list"

    def test_query_potentials_returns_list(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """query_potentials should return a list of potential entries."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "query_potentials",
            {"material_id": "UO2"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert isinstance(data, list), f"Expected list, got {type(data).__name__}"

    def test_trigger_extraction_returns_job_id(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """trigger_extraction should return a job_id and submitted status."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "trigger_extraction",
            {"file_url": "https://example.com/test.pdf"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert "job_id" in data, f"Expected 'job_id', got: {data}"
        assert data.get("status") == "submitted", f"Expected 'submitted', got: {data.get('status')}"

    def test_get_extraction_status_returns_job(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """get_extraction_status should return job details for a known job."""
        _init_and_confirm(server_proc)

        # First trigger a job to get a known job_id
        create_resp = _call_tool(
            server_proc,
            "trigger_extraction",
            {"file_url": "https://example.com/status-test.pdf"},
        )
        create_data = json.loads(
            [p["text"] for p in create_resp["result"]["content"] if p.get("type") == "text"][0]
        )
        job_id = create_data["job_id"]

        # Now query its status
        resp = _call_tool(
            server_proc,
            "get_extraction_status",
            {"job_id": job_id},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert data.get("job_id") == job_id, f"Expected job_id {job_id}, got: {data}"

    def test_get_extraction_status_not_found_returns_error(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """get_extraction_status should return an error for nonexistent job."""
        _init_and_confirm(server_proc)
        resp = _call_tool(
            server_proc,
            "get_extraction_status",
            {"job_id": "job-nonexistent"},
        )

        text_parts = [
            p["text"] for p in resp["result"]["content"]
            if p.get("type") == "text"
        ]
        data = json.loads(text_parts[0])
        assert "error" in data, f"Expected 'error' key for nonexistent job, got: {data}"

    def test_no_unhandled_exceptions_in_tool_calls(
        self,
        server_proc: subprocess.Popen[str],
    ) -> None:
        """All tool calls should return valid JSON-RPC responses (no crashes)."""
        _init_and_confirm(server_proc)

        tool_calls = [
            ("browse_ontology", {}),
            ("browse_ontology", {"query": "material"}),
            ("query_knowledge_graph", {"query": "UO2"}),
            ("search_sources", {"query": "nuclear"}),
            ("query_potentials", {"material_id": "UO2"}),
            ("trigger_extraction", {"file_url": "https://example.com/e2e.pdf"}),
            ("get_extraction_status", {"job_id": "job-nonexistent"}),
        ]

        for idx, (tool_name, args) in enumerate(tool_calls):
            resp = _call_tool(
                server_proc,
                tool_name,
                args,
                request_id=100 + idx,
            )

            # Must be valid JSON-RPC
            assert resp.get("jsonrpc") == "2.0", (
                f"Tool {tool_name!r}: missing jsonrpc version"
            )
            assert resp.get("id") == 100 + idx, (
                f"Tool {tool_name!r}: wrong id"
            )
            assert "result" in resp, (
                f"Tool {tool_name!r}: missing 'result', got error response: {resp}"
            )
            assert resp["result"].get("content"), (
                f"Tool {tool_name!r}: empty content in result"
            )
