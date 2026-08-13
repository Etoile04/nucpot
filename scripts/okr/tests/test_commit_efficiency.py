"""Tests for commit_efficiency.py — TDD RED phase.

All tests exercise the public API of the commit_efficiency module.
Functions under test:
- parse_git_log(git_output: str) -> list[dict]
- extract_issue_refs(commit_message: str) -> list[str]
- fetch_issue_statuses(issue_refs: list[str], api_url: str) -> dict[str, str]
- calculate_metrics(commits: list[dict], statuses: dict[str, str]) -> dict
- build_report(period_start: str, period_end: str, commits: list[dict], statuses: dict[str, str]) -> dict

The [no-issue] tests in ``TestNoIssueEscapeHatch`` PIN the escape-hatch
behaviour mandated by ADR-NFM-2081 §D2: a commit subject containing
``[no-issue]`` MUST be classified as unreferenced (structural waste). The
literal marker must NOT be allowed to launder the KR-2 metric.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr.commit_efficiency import (
    build_arg_parser,
    build_report,
    calculate_metrics,
    enrich_commits_with_refs,
    extract_issue_refs,
    fetch_issue_statuses,
    parse_git_log,
    run_git_log,
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

    @patch("scripts.okr.commit_efficiency.fetch_all_issues")
    def test_returns_status_map_on_success(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = [
            {"identifier": "NFM-100", "status": "done"},
            {"identifier": "NFM-200", "status": "in_progress"},
            {"identifier": "NFM-300", "status": "todo"},
        ]

        result = fetch_issue_statuses(
            ["NFM-100", "NFM-200"], "http://localhost:3000", "co-1",
        )
        assert result["NFM-100"] == "done"
        assert result["NFM-200"] == "in_progress"

    @patch("scripts.okr.commit_efficiency.fetch_all_issues")
    def test_returns_status_map_from_api_identifier(
        self, mock_fetch: MagicMock,
    ) -> None:
        """Paperclip issue references come from the ``identifier`` field."""
        mock_fetch.return_value = [
            {"identifier": "NFM-100", "key": None, "status": "done"},
            {"identifier": "NFM-200", "key": None, "status": "in_progress"},
        ]

        result = fetch_issue_statuses(
            ["NFM-100", "NFM-200"],
            "http://localhost:3000",
            "co-1",
            api_key="test-key",
        )

        assert result == {"NFM-100": "done", "NFM-200": "in_progress"}

    @patch("scripts.okr.commit_efficiency.fetch_all_issues")
    def test_propagates_paperclip_fetch_error(self, mock_fetch: MagicMock) -> None:
        """A PaperclipFetchError must propagate (NFM-2443 AC1+AC3).

        Pre-NFM-2443 this test asserted that an API failure degraded every
        ref to ``"unknown"`` — the exact silent-wrong-number behaviour the
        defect was filed for. The new contract is to raise, so the report
        can render KR-1/KR-4 as ``[no data]`` instead of ``0.000``.
        """
        from scripts.okr import PaperclipFetchError

        mock_fetch.side_effect = PaperclipFetchError("API down")
        with pytest.raises(PaperclipFetchError):
            fetch_issue_statuses(
                ["NFM-999"], "http://localhost:3000", "co-1",
                api_key="test-key",
            )

    @patch("scripts.okr.commit_efficiency.fetch_all_issues")
    def test_returns_unknown_for_missing_refs(self, mock_fetch: MagicMock) -> None:
        """Issue refs not found in the full list get status 'unknown'."""
        mock_fetch.return_value = [
            {"identifier": "NFM-100", "status": "done"},
        ]
        result = fetch_issue_statuses(
            ["NFM-100", "NFM-999"], "http://localhost:3000", "co-1",
        )
        assert result["NFM-100"] == "done"
        assert result["NFM-999"] == "unknown"

    def test_returns_empty_dict_for_empty_refs(self) -> None:
        result = fetch_issue_statuses(
            [], "http://localhost:3000", "co-1",
        )
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


# ---------------------------------------------------------------------------
# [no-issue] escape hatch (NFM-2083 / ADR-NFM-2081 §D2)
# ---------------------------------------------------------------------------
#
# These tests PIN the existing behaviour that a commit subject containing
# the literal marker ``[no-issue]`` is classified as UNREFERENCED by
# ``extract_issue_refs`` and therefore as structural waste by
# ``calculate_metrics``. The marker is an escape hatch for genuinely
# non-OKR commits (e.g. version bumps of third-party tooling) and must
# NOT launder the KR-2 structural-waste-rate metric.
#
# The acceptance criterion is "pin, do not fix": if any of these tests
# fail, that's a regression — the regex would have to change to add
# ``[no-issue]`` as a positive match, which the ADR explicitly forbids.



from scripts.okr.commit_efficiency import _ISSUE_REF_PATTERN


class TestNoIssueEscapeHatch:
    """Pin behavioural contract for the [no-issue] escape hatch."""

    @pytest.mark.unit
    def test_no_issue_marker_yields_no_refs(self) -> None:
        """Extracting from a subject carrying only ``[no-issue]`` returns []."""
        result = extract_issue_refs("chore: bump ruff to 0.14 [no-issue]")
        assert result == []

    @pytest.mark.unit
    def test_no_issue_with_prefix_brackets(self) -> None:
        """``[no-issue]`` may also appear as the sole prefix token."""
        result = extract_issue_refs("[no-issue] chore: bump ruff to 0.14")
        assert result == []

    @pytest.mark.unit
    def test_bare_nfm_reference_is_extracted(self) -> None:
        """A plain NFM-### subject is referenced (not waste)."""
        result = extract_issue_refs("feat: NFM-2082 commit reference enforcement")
        assert "NFM-2082" in result

    @pytest.mark.unit
    def test_bracketed_nfm_reference_is_extracted(self) -> None:
        """The conventional bracketed form ``[NFM-###]`` is referenced."""
        result = extract_issue_refs("[NFM-2082] feat: commit reference enforcement")
        assert "NFM-2082" in result

    @pytest.mark.unit
    def test_plain_chore_without_marker_yields_no_refs(self) -> None:
        """A subject with no marker and no ref is waste (existing behaviour)."""
        result = extract_issue_refs("chore: dep sync")
        assert result == []

    @pytest.mark.unit
    def test_revert_with_nfm_reference_is_extracted(self) -> None:
        """A ``Revert "NFM-1234 ..."`` message carries the ref in the subject."""
        result = extract_issue_refs('Revert "NFM-1234 old thing"')
        assert "NFM-1234" in result

    @pytest.mark.unit
    def test_revert_with_no_issue_marker_yields_no_refs(self) -> None:
        """A ``Revert "[no-issue] ..."`` message is still unreferenced."""
        result = extract_issue_refs('Revert "chore: bump ruff to 0.14 [no-issue]"')
        assert result == []

    @pytest.mark.unit
    def test_literal_no_issue_does_not_match_nfm_pattern(self) -> None:
        """The literal ``[no-issue]`` MUST NOT match the strict NFM-\\d+ regex.

        This is the structural guarantee from ADR-NFM-2081 §D2: the regex
        permits only the canonical ``NFM-<digits>`` form, so any literal
        marker carrying non-digit characters after ``NFM-`` is excluded.
        """
        assert _ISSUE_REF_PATTERN.findall("[no-issue]") == []
        assert _ISSUE_REF_PATTERN.findall("foo [no-issue] bar") == []
        assert _ISSUE_REF_PATTERN.search("NFM-") is None
        # ``fullmatch`` returns a Match object; .group() gives the matched string.
        match = _ISSUE_REF_PATTERN.fullmatch("NFM-2082")
        assert match is not None and match.group() == "NFM-2082"


class TestCalculateMetricsWithNoIssue:
    """``[no-issue]`` commits must be tallied as structural waste."""

    @pytest.mark.unit
    def test_no_issue_commit_counts_as_waste(self) -> None:
        """A commit whose only 'reference' is the escape hatch is waste."""
        commits = [
            {"hash": "a", "message": "chore: bump ruff to 0.14 [no-issue]", "issue_refs": []},
            {"hash": "b", "message": "feat: NFM-2082 enforcement", "issue_refs": ["NFM-2082"]},
        ]
        statuses = {"NFM-2082": "done"}
        result = calculate_metrics(commits, statuses)

        assert result["commits"]["total"] == 2
        assert result["commits"]["withoutIssueRef"] == 1
        assert result["commits"]["withIssueRef"] == 1
        # structural_waste = 1 / 2 = 0.5
        assert abs(result["metrics"]["structuralWasteRate"] - 0.5) < 1e-9

    @pytest.mark.unit
    def test_mixed_no_issue_and_referenced_commit_efficiency(self) -> None:
        """KR-2 metric must reflect only committed NFM-### work, not escapes."""
        commits = [
            {"hash": "a", "message": "chore: bump ruff [no-issue]", "issue_refs": []},
            {"hash": "b", "message": "chore: dep sync", "issue_refs": []},
            {"hash": "c", "message": "feat: NFM-100 login", "issue_refs": ["NFM-100"]},
            {"hash": "d", "message": "feat: NFM-101 logout", "issue_refs": ["NFM-101"]},
        ]
        statuses = {"NFM-100": "done", "NFM-101": "done"}
        result = calculate_metrics(commits, statuses)

        # Only the 2 no-issue / chore commits are waste; the 2 NFM-### are referenced.
        assert result["commits"]["withIssueRef"] == 2
        assert result["commits"]["withoutIssueRef"] == 2
        assert abs(result["metrics"]["structuralWasteRate"] - 0.5) < 1e-9
        # Completed issues / total commits = 2/4 = 0.5
        assert abs(result["metrics"]["commitEfficiency"] - 0.5) < 1e-9

    @pytest.mark.unit
    def test_only_no_issue_commits_yields_full_waste_rate(self) -> None:
        """If every commit in the window carries the escape hatch, waste = 1.0."""
        commits = [
            {"hash": "a", "message": "chore: bump ruff [no-issue]", "issue_refs": []},
            {"hash": "b", "message": "chore: twiddle config [no-issue]", "issue_refs": []},
        ]
        statuses: dict[str, str] = {}
        result = calculate_metrics(commits, statuses)

        assert result["commits"]["withIssueRef"] == 0
        assert result["commits"]["withoutIssueRef"] == 2
        assert result["metrics"]["structuralWasteRate"] == 1.0
        assert result["metrics"]["commitEfficiency"] == 0.0


# ---------------------------------------------------------------------------
# run_git_log — revision basis (NFM-2204 / R2)
# ---------------------------------------------------------------------------

class TestRunGitLogRevisionBasis:
    """Pin the revision basis the KR-2 metric is computed against.

    Before NFM-2204, ``run_git_log`` shelled out to ``git log --oneline
    --since=... --until=...`` with no revision argument. Two defects followed:

    1. Implicit ``HEAD`` — the number measured whichever branch happened to be
       checked out, so it moved with the workspace rather than with behaviour.
    2. Merge commits were counted, while the CI gate exempts them by
       construction (parent-count >= 2). Numerator and denominator disagreed
       about what a "commit" is.

    These tests assert on the *argv actually handed to* ``subprocess.run`` —
    not on the returned number. A test that only checks the return value
    cannot distinguish an explicit basis from an implicit one, because both
    produce a plausible integer.
    """

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
        """Replace subprocess.run with a recorder; return the capture dict."""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            captured["cmd"] = cmd
            return MagicMock(stdout="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        return captured

    @pytest.mark.unit
    def test_revision_basis_appears_in_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The caller-supplied revision must reach git as an explicit token."""
        captured = self._capture(monkeypatch)

        run_git_log("2026-07-27", "2026-08-03", rev="origin/main")

        assert "origin/main" in captured["cmd"], (
            "revision basis missing from argv — git would default to HEAD, "
            f"making the metric workspace-dependent. argv={captured['cmd']}"
        )

    @pytest.mark.unit
    def test_merge_commits_are_excluded_from_argv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--max-parents=1 must be passed so merges are dropped.

        The CI gate exempts merge commits by construction. If the metric counts
        them, the numerator and the denominator are measuring different things.
        """
        captured = self._capture(monkeypatch)

        run_git_log("2026-07-27", "2026-08-03", rev="origin/main")

        assert "--max-parents=1" in captured["cmd"], (
            "merge commits are not excluded — the metric counts commits the "
            f"gate exempts. argv={captured['cmd']}"
        )

    @pytest.mark.unit
    def test_basis_cannot_be_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """There must be no implicit-HEAD fallback.

        Omitting the basis must be a hard error at the call site, not a silent
        fall back to whatever is checked out.
        """
        self._capture(monkeypatch)

        with pytest.raises(TypeError):
            run_git_log("2026-07-27", "2026-08-03")  # type: ignore[call-arg]

    @pytest.mark.unit
    def test_explicit_rev_is_not_overridden_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-default basis must be honoured verbatim, and only it."""
        captured = self._capture(monkeypatch)

        run_git_log("2026-07-27", "2026-08-03", rev="v1.2.3")

        assert "v1.2.3" in captured["cmd"]
        assert "origin/main" not in captured["cmd"], (
            "default basis leaked into an explicitly-parameterised call: "
            f"argv={captured['cmd']}"
        )

    @pytest.mark.unit
    def test_date_window_still_reaches_git(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pinning the basis must not drop the --since/--until window."""
        captured = self._capture(monkeypatch)

        run_git_log("2026-07-27", "2026-08-03", rev="origin/main")

        assert "--since=2026-07-27" in captured["cmd"]
        assert "--until=2026-08-03" in captured["cmd"]


class TestRevCliFlag:
    """The revision basis must be settable from the command line."""

    @pytest.mark.unit
    def test_rev_flag_defaults_to_origin_main(self) -> None:
        """Default basis is the shared remote branch, not the local checkout."""
        args = build_arg_parser().parse_args(
            ["--since", "2026-07-27", "--until", "2026-08-03"]
        )
        assert args.rev == "origin/main"

    @pytest.mark.unit
    def test_rev_flag_is_overridable(self) -> None:
        """An operator can measure a different basis when they need to."""
        args = build_arg_parser().parse_args(
            ["--since", "2026-07-27", "--until", "2026-08-03", "--rev", "v1.2.3"]
        )
        assert args.rev == "v1.2.3"
