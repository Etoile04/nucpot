"""Tests for fetch_all_issues pagination helper — TDD RED phase.

Exercises the public API of ``scripts.okr.fetch_all_issues`` and the
refactored ``fetch_issue_statuses`` in ``commit_efficiency.py``.

Pagination contract:
- Endpoint: GET /api/companies/{company_id}/issues with offset & limit
- Loop increments offset by limit until response returns empty list
- Accumulates ALL results into a single flat list[dict]

Test strategy:
- Use ``http.server.HTTPServer`` stubs to verify real HTTP pagination
- Use ``unittest.mock.patch`` for simpler unit tests
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from scripts.okr import fetch_all_issues


# ---------------------------------------------------------------------------
# Stub HTTP server for real pagination tests
# ---------------------------------------------------------------------------

def _make_issues(count: int, start_id: int = 0) -> list[dict[str, Any]]:
    """Generate synthetic issue objects with sequential IDs."""
    return [
        {
            "id": f"issue-{i}",
            "key": f"NFM-{1000 + i}",
            "status": "done",
            "title": f"Test issue {i}",
        }
        for i in range(start_id, start_id + count)
    ]


class _PaginatedHandler(BaseHTTPRequestHandler):
    """Stub handler that returns paginated issues based on offset/limit query params."""

    # Class-level store set before server starts
    total_issues: int = 0

    def do_GET(self) -> None:  # noqa: N802 (HTTP verb)
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        offset = int(params.get("offset", ["0"])[0])
        limit = int(params.get("limit", ["1000"])[0])

        end = min(offset + limit, _PaginatedHandler.total_issues)
        page = _make_issues(end - offset, start_id=offset)

        body = json.dumps(page).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence request logs during tests."""
        pass


def _start_server(
    total: int,
    host: str = "127.0.0.1",
    port: int = 0,
) -> HTTPServer:
    """Start a stub server returning *total* issues across pages."""
    _PaginatedHandler.total_issues = total
    server = HTTPServer((host, port), _PaginatedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# fetch_all_issues — real HTTP tests
# ---------------------------------------------------------------------------

class TestFetchAllIssuesPagination:
    """Verify fetch_all_issues exhausts all pages via real HTTP."""

    def test_fetches_all_pages_when_total_exceeds_limit(self) -> None:
        """2037 issues (>1000 limit) must all be returned across 3 pages."""
        server = _start_server(total=2037)
        port = server.server_address[1]
        try:
            result = fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={},
            )
            assert len(result) == 2037, (
                f"Expected 2037 issues, got {len(result)}"
            )
            # Verify all IDs are present (no truncation)
            ids = {issue["id"] for issue in result}
            for i in range(2037):
                assert f"issue-{i}" in ids, f"Missing issue-{i}"
        finally:
            server.shutdown()

    def test_single_page_when_under_limit(self) -> None:
        """Less than 1000 issues should be fetched in a single request."""
        server = _start_server(total=42)
        port = server.server_address[1]
        try:
            result = fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={},
            )
            assert len(result) == 42
        finally:
            server.shutdown()

    def test_empty_first_page_returns_empty_list(self) -> None:
        """Zero total issues should return an empty list immediately."""
        server = _start_server(total=0)
        port = server.server_address[1]
        try:
            result = fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={},
            )
            assert result == []
        finally:
            server.shutdown()

    def test_exactly_one_page_boundary(self) -> None:
        """Exactly 1000 issues (one full page, no next page)."""
        server = _start_server(total=1000)
        port = server.server_address[1]
        try:
            result = fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={},
            )
            assert len(result) == 1000
        finally:
            server.shutdown()

    def test_one_more_than_single_page(self) -> None:
        """1001 issues triggers a second page fetch (offset=1000)."""
        server = _start_server(total=1001)
        port = server.server_address[1]
        try:
            result = fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={},
            )
            assert len(result) == 1001
            ids = {issue["id"] for issue in result}
            assert "issue-1000" in ids, "Second page item missing"
        finally:
            server.shutdown()

    def test_passes_extra_params_in_query(self) -> None:
        """Extra params (e.g. status filter) are forwarded in the query string."""
        server = _start_server(total=5)
        port = server.server_address[1]

        captured_requests: list[str] = []

        original_do_get = _PaginatedHandler.do_GET
        def capturing_do_get(self: _PaginatedHandler) -> None:
            captured_requests.append(self.path)
            original_do_get(self)

        _PaginatedHandler.do_GET = capturing_do_get  # type: ignore[assignment]
        try:
            fetch_all_issues(
                f"http://127.0.0.1:{port}",
                company_id="test-co",
                params={"status": "done"},
            )
            # Verify query string includes the extra param
            assert any("status=done" in req for req in captured_requests), (
                f"Extra param 'status=done' not found in requests: {captured_requests}"
            )
        finally:
            _PaginatedHandler.do_GET = original_do_get  # type: ignore[assignment]
            server.shutdown()


# ---------------------------------------------------------------------------
# fetch_all_issues — mock-based unit tests
# ---------------------------------------------------------------------------

class TestFetchAllIssuesUnit:
    """Fast unit tests using mocks instead of real HTTP."""

    @patch("scripts.okr.urllib.request.urlopen")
    def test_single_page_accumulates(self, mock_urlopen: MagicMock) -> None:
        """One page of issues is accumulated into a flat list."""
        page = _make_issues(10)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(page).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_all_issues("http://localhost:3000", "co-1", {})

        assert len(result) == 10
        assert result[0]["key"] == "NFM-1000"

    @patch("scripts.okr.urllib.request.urlopen")
    def test_empty_response_returns_empty_list(self, mock_urlopen: MagicMock) -> None:
        """Empty first page means zero issues total."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_all_issues("http://localhost:3000", "co-1", {})

        assert result == []
