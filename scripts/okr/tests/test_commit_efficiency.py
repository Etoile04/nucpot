"""Tests for commit_efficiency.py — TDD RED phase.

All tests exercise the public API of the commit_efficiency module.
Functions under test:
- parse_git_log(git_output: str) -> list[dict]
- extract_issue_refs(commit_message: str) -> list[str]
- fetch_issue_statuses(issue_refs: list[str], api_url: str) -> dict[str, str]
- calculate_metrics(commits: list[dict], statuses: dict[str, str]) -> dict
- build_report(period_start: str, period_end: str, commits: list[dict], statuses: dict[str, str]) -> dict
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr.commit_efficiency import (
    build_report,
    calculate_metrics,
    enrich_commits_with_refs,
    extract_issue_refs,
    fetch_issue_statuses,
    parse_git_log,
)


# ---------------------------------------------------------------------------
# parse_git_log
# ---------------------------------------------------------------------------

class TestParseGitLog:
    """Parse raw git log --oneline output into structured commit records."""

    def test_parses_single_commit(self) -> None:
        raw = "abc1234 feat: add login page"
        result = parse_git_log(raw)
        assert result == [
            {"hash": "abc1234", "message": "feat: add login page"},
        ]

    def test_parses_multiple_commits(self) -> None:
        raw = "a1b2c3d feat: first\ne4f5g6h fix: second\ni7j8k9l docs: third"
        result = parse_git_log(raw)
        assert len(result) == 3
        assert result[0]["hash"] == "a1b2c3d"
        assert result[2]["message"] == "docs: third"

    def test_returns_empty_list_for_empty_input(self) -> None:
        result = parse_git_log("")
        assert result == []

    def test_strips_whitespace_from_lines(self) -> None:
        raw = "  abc1234 feat: spaced  \n  def5678 fix: trailing  "
        result = parse_git_log(raw)
        assert result[0]["hash"] == "abc1234"
        assert result[1]["hash"] == "def5678"

    def test_skips_blank_lines(self) -> None:
        raw = "abc1234 feat: valid\n\n\ndef5678 fix: also valid"
        result = parse_git_log(raw)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# extract_issue_refs
# ---------------------------------------------------------------------------

class TestExtractIssueRefs:
    """Extract NFM-XXX references from a commit message."""

    def test_extracts_bare_nfm_reference(self) -> None:
        result = extract_issue_refs("feat: implement NFM-100 login")
        assert "NFM-100" in result

    def test_extracts_bracketed_reference(self) -> None:
        result = extract_issue_refs("[NFM-200] fix typo")
        assert "NFM-200" in result

    def test_extracts_multiple_references(self) -> None:
        result = extract_issue_refs("feat: NFM-300 and NFM-301 together")
        assert "NFM-300" in result
        assert "NFM-301" in result

    def test_returns_empty_for_no_reference(self) -> None:
        result = extract_issue_refs("chore: update readme")
        assert result == []

    def test_deduplicates_references(self) -> None:
        result = extract_issue_refs("NFM-400 NFM-400 NFM-401")
        assert result == ["NFM-400", "NFM-401"]

    def test_exacts_nfm_prefix_only(self) -> None:
        result = extract_issue_refs("feat: JIRA-500 ticket")
        assert result == []


# ---------------------------------------------------------------------------
# fetch_issue_statuses
# ---------------------------------------------------------------------------

class TestFetchIssueStatuses:
    """Query Paperclip API for issue statuses, with caching and error handling."""

    @patch("scripts.okr.commit_efficiency.urllib.request.urlopen")
    def test_returns_status_map_on_success(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "issues": [
                {"key": "NFM-100", "status": "done"},
                {"key": "NFM-200", "status": "in_progress"},
            ]
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = fetch_issue_statuses(["NFM-100", "NFM-200"], "http://localhost:3000")
        assert result["NFM-100"] == "done"
        assert result["NFM-200"] == "in_progress"

    @patch("scripts.okr.commit_efficiency.urllib.request.urlopen")
    def test_returns_unknown_on_api_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = Exception("API down")
        result = fetch_issue_statuses(["NFM-999"], "http://localhost:3000")
        assert result["NFM-999"] == "unknown"

    def test_returns_empty_dict_for_empty_refs(self) -> None:
        result = fetch_issue_statuses([], "http://localhost:3000")
        assert result == {}


# ---------------------------------------------------------------------------
# calculate_metrics
# ---------------------------------------------------------------------------

class TestCalculateMetrics:
    """Compute commit efficiency and structural waste rate from commit/issue data."""

    def test_basic_metrics_calculation(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-100 feat", "issue_refs": ["NFM-100"]},
            {"hash": "b", "message": "chore: cleanup", "issue_refs": []},
            {"hash": "c", "message": "NFM-101 fix", "issue_refs": ["NFM-101"]},
        ]
        statuses = {"NFM-100": "done", "NFM-101": "in_progress"}
        result = calculate_metrics(commits, statuses)

        assert result["commits"]["total"] == 3
        assert result["commits"]["withIssueRef"] == 2
        assert result["commits"]["withoutIssueRef"] == 1
        assert result["issues"]["referenced"] == 2
        assert result["issues"]["completed"] == 1
        assert result["issues"]["inProgress"] == 1
        assert result["issues"]["other"] == 0

    def test_commit_efficiency_formula(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1 done", "issue_refs": ["NFM-1"]},
            {"hash": "b", "message": "NFM-2 done", "issue_refs": ["NFM-2"]},
            {"hash": "c", "message": "NFM-3 done", "issue_refs": ["NFM-3"]},
            {"hash": "d", "message": "NFM-4 wip", "issue_refs": ["NFM-4"]},
            {"hash": "e", "message": "NFM-5 wip", "issue_refs": ["NFM-5"]},
            {"hash": "f", "message": "chore", "issue_refs": []},
            {"hash": "g", "message": "chore2", "issue_refs": []},
            {"hash": "h", "message": "chore3", "issue_refs": []},
        ]
        statuses = {
            "NFM-1": "done", "NFM-2": "done", "NFM-3": "done",
            "NFM-4": "in_progress", "NFM-5": "in_progress",
        }
        result = calculate_metrics(commits, statuses)
        # commit_efficiency = completed / total = 3 / 8
        assert abs(result["metrics"]["commitEfficiency"] - 0.375) < 1e-6

    def test_structural_waste_rate_formula(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1 feat", "issue_refs": ["NFM-1"]},
            {"hash": "b", "message": "NFM-2 feat", "issue_refs": ["NFM-2"]},
            {"hash": "c", "message": "no ref", "issue_refs": []},
        ]
        statuses = {"NFM-1": "done", "NFM-2": "done"}
        result = calculate_metrics(commits, statuses)
        # structural_waste = without_ref / total = 1 / 3
        assert abs(result["metrics"]["structuralWasteRate"] - (1 / 3)) < 1e-6

    def test_zero_commits_returns_zero_metrics(self) -> None:
        result = calculate_metrics([], {})
        assert result["metrics"]["commitEfficiency"] == 0.0
        assert result["metrics"]["structuralWasteRate"] == 0.0
        assert result["commits"]["total"] == 0

    def test_all_commits_have_refs(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1", "issue_refs": ["NFM-1"]},
            {"hash": "b", "message": "NFM-2", "issue_refs": ["NFM-2"]},
        ]
        statuses = {"NFM-1": "done", "NFM-2": "in_progress"}
        result = calculate_metrics(commits, statuses)
        assert result["commits"]["withoutIssueRef"] == 0
        assert result["metrics"]["structuralWasteRate"] == 0.0

    def test_counts_other_status_correctly(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1", "issue_refs": ["NFM-1"]},
        ]
        statuses = {"NFM-1": "open"}
        result = calculate_metrics(commits, statuses)
        assert result["issues"]["completed"] == 0
        assert result["issues"]["inProgress"] == 0
        assert result["issues"]["other"] == 1


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport:
    """Assemble the final JSON-serializable report."""

    def test_report_contains_period(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1", "issue_refs": ["NFM-1"]},
        ]
        statuses = {"NFM-1": "done"}
        report = build_report("2026-06-23", "2026-06-29", commits, statuses)

        assert report["period"]["start"] == "2026-06-23"
        assert report["period"]["end"] == "2026-06-29"

    def test_report_is_json_serializable(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1", "issue_refs": ["NFM-1"]},
            {"hash": "b", "message": "chore", "issue_refs": []},
        ]
        statuses = {"NFM-1": "done"}
        report = build_report("2026-06-23", "2026-06-29", commits, statuses)

        serialized = json.dumps(report)
        parsed = json.loads(serialized)
        assert parsed["commits"]["total"] == 2

    def test_report_matches_expected_schema(self) -> None:
        commits = [
            {"hash": "a", "message": "NFM-1 feat", "issue_refs": ["NFM-1"]},
            {"hash": "b", "message": "chore", "issue_refs": []},
        ]
        statuses = {"NFM-1": "done"}
        report = build_report("2026-06-23", "2026-06-29", commits, statuses)

        assert "period" in report
        assert "commits" in report
        assert "issues" in report
        assert "metrics" in report
        assert "commitEfficiency" in report["metrics"]
        assert "structuralWasteRate" in report["metrics"]


# ---------------------------------------------------------------------------
# enrich_commits_with_refs
# ---------------------------------------------------------------------------

class TestEnrichCommitsWithRefs:
    """Add issue_refs field to each commit dict by parsing its message."""

    def test_adds_refs_to_commits(self) -> None:
        commits = [
            {"hash": "a", "message": "feat: NFM-42 login"},
            {"hash": "b", "message": "chore: cleanup"},
        ]
        result = enrich_commits_with_refs(commits)
        assert result[0]["issue_refs"] == ["NFM-42"]
        assert result[1]["issue_refs"] == []

    def test_does_not_mutate_input(self) -> None:
        commits = [{"hash": "a", "message": "NFM-1"}]
        original = list(commits)
        enrich_commits_with_refs(commits)
        assert commits == original
        assert "issue_refs" not in commits[0]
