"""Tests for scripts/okr/__init__.py — fetch_all_issues pagination helper.

Covers:
- Single-page response (no pagination needed)
- Multi-page response (pagination across multiple pages)
- Empty first page
- Network error recovery
- Malformed JSON response
- Custom params pass-through
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr import fetch_all_issues


def _make_response(data: object) -> MagicMock:
    """Build a mock urlopen response that returns the given JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestFetchAllIssuesSinglePage:
    """fetch_all_issues when all results fit in one page."""

    @patch("scripts.okr.urllib.request.urlopen")
    def test_returns_issues_from_single_page(self, mock_urlopen: MagicMock) -> None:
        issues = [
            {"key": "NFM-100", "status": "done"},
            {"key": "NFM-200", "status": "in_progress"},
        ]
        mock_urlopen.return_value = _make_response(issues)

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert len(result) == 2
        assert result[0]["key"] == "NFM-100"
        assert result[1]["status"] == "in_progress"

    @patch("scripts.okr.urllib.request.urlopen")
    def test_returns_empty_list_when_no_issues(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _make_response([])

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert result == []

    @patch("scripts.okr.urllib.request.urlopen")
    def test_handles_wrapped_response(self, mock_urlopen: MagicMock) -> None:
        """API may return {"issues": [...]} instead of a bare array."""
        mock_urlopen.return_value = _make_response({
            "issues": [{"key": "NFM-1", "status": "done"}]
        })

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert len(result) == 1
        assert result[0]["key"] == "NFM-1"


class TestFetchAllIssuesMultiPage:
    """fetch_all_issues correctly paginates through multiple pages."""

    @patch("scripts.okr.urllib.request.urlopen")
    def test_accumulates_across_pages(self, mock_urlopen: MagicMock) -> None:
        page1 = [{"key": f"NFM-{i}", "status": "done"} for i in range(1, 4)]
        page2 = [{"key": f"NFM-{i}", "status": "in_progress"} for i in range(4, 7)]
        page3 = []  # sentinel: empty page stops pagination

        mock_urlopen.side_effect = [
            _make_response(page1),
            _make_response(page2),
            _make_response(page3),
        ]

        result = fetch_all_issues(
            "http://localhost:3000", "company-uuid", {"limit": "3"}
        )

        assert len(result) == 6
        assert result[0]["key"] == "NFM-1"
        assert result[5]["key"] == "NFM-6"

    @patch("scripts.okr.urllib.request.urlopen")
    def test_url_contains_offset_and_limit(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _make_response([])

        fetch_all_issues("http://localhost:3000", "company-uuid")

        call_url = mock_urlopen.call_args[0][0]
        assert "offset=0" in call_url
        assert "limit=1000" in call_url
        assert "/api/companies/company-uuid/issues" in call_url

    @patch("scripts.okr.urllib.request.urlopen")
    def test_second_page_has_correct_offset(self, mock_urlopen: MagicMock) -> None:
        page1 = [{"key": f"NFM-{i}", "status": "done"} for i in range(3)]
        page2 = []

        mock_urlopen.side_effect = [
            _make_response(page1),
            _make_response(page2),
        ]

        fetch_all_issues(
            "http://localhost:3000", "company-uuid", {"limit": "3"}
        )

        second_call_url = mock_urlopen.call_args_list[1][0][0]
        assert "offset=3" in second_call_url


class TestFetchAllIssuesCustomParams:
    """fetch_all_issues passes through custom query params."""

    @patch("scripts.okr.urllib.request.urlopen")
    def test_custom_params_merged_into_query(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _make_response([])

        fetch_all_issues(
            "http://localhost:3000",
            "company-uuid",
            {"status": "done"},
        )

        call_url = mock_urlopen.call_args[0][0]
        assert "status=done" in call_url


class TestFetchAllIssuesErrorHandling:
    """fetch_all_issues returns partial results on error, empty list on first-page failure."""

    @patch("scripts.okr.urllib.request.urlopen")
    def test_returns_empty_on_network_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert result == []

    @patch("scripts.okr.urllib.request.urlopen")
    def test_returns_partial_on_mid_stream_error(self, mock_urlopen: MagicMock) -> None:
        page1 = [{"key": "NFM-1", "status": "done"}]
        mock_urlopen.side_effect = [
            _make_response(page1),
            urllib.error.URLError("connection refused"),
        ]

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert len(result) == 1
        assert result[0]["key"] == "NFM-1"

    @patch("scripts.okr.urllib.request.urlopen")
    def test_returns_empty_on_malformed_json(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_all_issues("http://localhost:3000", "company-uuid")

        assert result == []
