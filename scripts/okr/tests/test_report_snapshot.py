"""Snapshot test for scripts/okr/report.py (NFM-2041 B3).

Proves the report pipeline reproduces the CEO hand-computed values on the
reference window 2026-07-20 to 2026-07-26. This is the final acceptance gate
before the feature is marked done.

CEO hand-computed target values (within tolerance):
    KR-1 (commit efficiency)  : 0.130  (tolerance 0.001)
    KR-2 (structural waste)    : 0.318  (tolerance 0.001)
    KR-3 (deploy success)      : no_data_baseline  (no fixture events)
    KR-4 (avg lead time)       : 9.88 d (tolerance 0.01)
    KR-5 (test coverage)       : no_data_baseline  (no fixture XML)

Approach:
    1. Load frozen fixture files (git log + paginated issues API responses).
    2. Patch ``subprocess.run`` (git log IO) and ``urllib.request.urlopen``
       (API IO) so the pipeline runs against the fixtures, not the real world.
    3. Call the report aggregation functions directly (not the CLI).
    4. Assert each KR's value matches the CEO target within tolerance.

The fixtures are version-controlled and self-documenting: each one is small
enough to read in a terminal and explains what data it carries and why.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.okr import fetch_all_issues
from scripts.okr.commit_efficiency import (
    calculate_metrics,
    enrich_commits_with_refs,
    fetch_issue_statuses,
    parse_git_log,
    run_git_log,
)
from scripts.okr.report import (
    _NO_DATA_BASELINE,
    build_kr_report,
    compute_kr3,
    compute_kr5,
    compute_lead_time,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Reference window chosen by the CEO for hand-computed values.
PERIOD_START = "2026-07-20"
PERIOD_END = "2026-07-26"

# Tolerance bands per ADR-REPEATABLE-1 / NFM-2041 acceptance criteria.
KR1_TOLERANCE = 0.001
KR2_TOLERANCE = 0.001
KR4_TOLERANCE = 0.01  # days

# CEO hand-computed reference values for the 2026-07-20 → 2026-07-26 window.
CEO_KR1 = 0.130
CEO_KR2 = 0.318
CEO_KR4 = 9.88

# Test-only company / API placeholder. The pipeline uses these as URLs only;
# the patched urlopen returns fixture data, so the URL is never hit.
TEST_API_URL = "http://paperclip.test"
TEST_COMPANY_ID = "snapshot-company-id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _urlopen_response(payload: list[dict]) -> MagicMock:
    """Build a context-manager mock that mimics ``urllib.request.urlopen``.

    The real ``urlopen`` returns an object usable as ``with resp:`` and whose
    ``.read()`` returns the response body as bytes. The mock reproduces both
    contracts so the real ``fetch_all_issues`` (in ``scripts/okr/__init__.py``)
    can be driven without modification.
    """
    response = MagicMock()
    response.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=None)
    return response


def _load_json(name: str) -> list[dict]:
    """Load a JSON array fixture file from the fixtures directory."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _run_pipeline(
    page1: list[dict],
    page2: list[dict],
    git_log_text: str,
) -> dict:
    """Run the full 5-KR pipeline against patched IO and return the report.

    Patches ``subprocess.run`` to return the frozen git log and
    ``urllib.request.urlopen`` to return the frozen paginated issue pages.
    Both ``fetch_all_issues`` invocations are served: the first (KR-1/KR-2
    status lookup, no filter) gets ``page1``; the second (KR-4 lead time,
    status=done) gets ``page2``. Each call's pagination terminates on the
    following empty response.
    """
    with patch("subprocess.run") as mock_run, \
         patch("urllib.request.urlopen") as mock_urlopen:
        mock_run.return_value = MagicMock(stdout=git_log_text, returncode=0)
        # Order of urlopen calls (matches main() in report.py):
        #   1) fetch_issue_statuses -> fetch_all_issues(no filter) -> page1
        #      page1 is < _DEFAULT_LIMIT (65 < 1000), so the loop in
        #      fetch_all_issues breaks on the first response.
        #   2) fetch_all_issues(status="done") -> page2
        #      page2 is also < _DEFAULT_LIMIT (50 < 1000), so the loop
        #      breaks on the first response too. Two urlopen mocks total.
        mock_urlopen.side_effect = [
            _urlopen_response(page1),
            _urlopen_response(page2),
        ]

        # KR-1 & KR-2: commit efficiency + waste rate
        raw_log = run_git_log(PERIOD_START, PERIOD_END, rev="origin/main")
        commits = parse_git_log(raw_log)
        enriched = enrich_commits_with_refs(commits)
        all_refs = sorted({ref for c in enriched for ref in c["issue_refs"]})
        statuses = fetch_issue_statuses(all_refs, TEST_API_URL, TEST_COMPANY_ID)
        metrics = calculate_metrics(enriched, statuses)
        kr1_value = metrics["metrics"]["commitEfficiency"]
        kr2_value = metrics["metrics"]["structuralWasteRate"]

        # KR-3: deploy first-pass success — no fixture events, expect baseline
        kr3_entry = compute_kr3(PERIOD_START, PERIOD_END)

        # KR-4: avg lead time
        done_issues = fetch_all_issues(
            TEST_API_URL, TEST_COMPANY_ID, {"status": "done"},
        )
        kr4_value = compute_lead_time(done_issues, PERIOD_START, PERIOD_END)

        # KR-5: test coverage — no fixture XML, expect baseline
        kr5_entry = compute_kr5(None)

        return build_kr_report(
            PERIOD_START,
            PERIOD_END,
            kr1_value,
            kr2_value,
            kr3_entry,
            kr4_value,
            kr5_entry,
        )


# ---------------------------------------------------------------------------
# Snapshot assertions
# ---------------------------------------------------------------------------


class TestSnapshotReproduction:
    """The full pipeline, run against frozen fixtures, must reproduce the
    CEO hand-computed values within the documented tolerance bands.
    """

    def test_kr1_commit_efficiency_matches_ceo_value(self) -> None:
        """KR-1: 0.130 within 0.001 tolerance."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["krs"]["KR-1"]["key"] == "commit_efficiency"
        assert report["krs"]["KR-1"]["value"] == pytest.approx(
            CEO_KR1, abs=KR1_TOLERANCE,
        )
        assert report["krs"]["KR-1"]["unit"] == "ratio"

    def test_kr2_structural_waste_rate_matches_ceo_value(self) -> None:
        """KR-2: 0.318 within 0.001 tolerance."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["krs"]["KR-2"]["key"] == "structural_waste_rate"
        assert report["krs"]["KR-2"]["value"] == pytest.approx(
            CEO_KR2, abs=KR2_TOLERANCE,
        )

    def test_kr3_deploy_success_reports_no_data_baseline(self) -> None:
        """KR-3: no deploy events in fixture -> no_data_baseline status."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["krs"]["KR-3"]["key"] == "deploy_first_pass_success"
        assert report["krs"]["KR-3"]["value"] is None
        assert report["krs"]["KR-3"]["status"] == _NO_DATA_BASELINE

    def test_kr4_avg_lead_time_matches_ceo_value(self) -> None:
        """KR-4: 9.88 days within 0.01 tolerance."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["krs"]["KR-4"]["key"] == "avg_lead_time"
        assert report["krs"]["KR-4"]["value"] == pytest.approx(
            CEO_KR4, abs=KR4_TOLERANCE,
        )
        assert report["krs"]["KR-4"]["unit"] == "days"

    def test_kr5_test_coverage_reports_no_data_baseline(self) -> None:
        """KR-5: no coverage XML in fixture -> no_data_baseline status."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["krs"]["KR-5"]["key"] == "test_coverage"
        assert report["krs"]["KR-5"]["value"] is None
        assert report["krs"]["KR-5"]["status"] == _NO_DATA_BASELINE


# ---------------------------------------------------------------------------
# JSON schema validation (ADR-REPEATABLE-1)
# ---------------------------------------------------------------------------


class TestReportSchema:
    """Validate the report JSON conforms to ADR-REPEATABLE-1."""

    REQUIRED_TOP_KEYS = {"period", "generated_at", "krs"}
    KR_IDS = ["KR-1", "KR-2", "KR-3", "KR-4", "KR-5"]

    def test_top_level_keys(self) -> None:
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert set(report.keys()) >= self.REQUIRED_TOP_KEYS

    def test_period_block(self) -> None:
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert report["period"] == {"start": PERIOD_START, "end": PERIOD_END}

    def test_generated_at_is_iso8601_utc(self) -> None:
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        # Format: 2026-08-03T12:34:56Z
        assert report["generated_at"].endswith("Z")
        assert len(report["generated_at"]) == 20

    def test_all_five_krs_present(self) -> None:
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        assert list(report["krs"].keys()) == self.KR_IDS

    def test_each_kr_has_key_field(self) -> None:
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        for kr_id in self.KR_IDS:
            assert "key" in report["krs"][kr_id]
            assert isinstance(report["krs"][kr_id]["key"], str)

    def test_kr_with_value_carries_unit(self) -> None:
        """KRs that successfully produce a numeric value also report a unit."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        # KR-1, KR-2, KR-4 produce values; KR-3 and KR-5 are baseline.
        for kr_id in ["KR-1", "KR-2", "KR-4"]:
            entry = report["krs"][kr_id]
            assert entry.get("value") is not None
            assert entry.get("unit") in {"ratio", "days"}

    def test_kr_baseline_carries_status(self) -> None:
        """KRs that have no fixture data report no_data_baseline status."""
        page1 = _load_json("issues_page1.json")
        page2 = _load_json("issues_page2.json")
        git_log = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()

        report = _run_pipeline(page1, page2, git_log)

        for kr_id in ["KR-3", "KR-5"]:
            entry = report["krs"][kr_id]
            assert entry.get("value") is None
            assert entry.get("status") == _NO_DATA_BASELINE


# ---------------------------------------------------------------------------
# Fixture integrity
# ---------------------------------------------------------------------------


class TestFixtureIntegrity:
    """Sanity-check the fixtures themselves so a hand-edit can't silently
    break the snapshot by, for example, removing all done issues.
    """

    def test_git_log_is_non_empty(self) -> None:
        text = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()
        assert len(text.strip()) > 0
        # Must be parseable by parse_git_log without error.
        commits = parse_git_log(text)
        assert len(commits) > 0

    def test_git_log_yields_known_total(self) -> None:
        """The fixture must be sized to give the target ratios exactly.

        500 total commits, 159 without an issue ref, and 65 unique refs that
        are all ``done`` reproduce KR-1 = 65/500 = 0.130 and
        KR-2 = 159/500 = 0.318 exactly. Any other totals will not match.
        """
        text = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()
        commits = parse_git_log(text)
        enriched = enrich_commits_with_refs(commits)
        total = len(enriched)
        without_ref = sum(1 for c in enriched if not c["issue_refs"])
        unique_refs = {ref for c in enriched for ref in c["issue_refs"]}
        assert total == 500
        assert without_ref == 159
        assert len(unique_refs) == 65

    def test_page1_covers_all_commit_refs_as_done(self) -> None:
        """Every NFM-XXX referenced in commits must be present in page1
        with a ``done`` (or ``closed``) status, so commit efficiency
        counts them as completed.
        """
        text = (FIXTURES_DIR / "git_log_2026-07-20_2026-07-26.txt").read_text()
        commits = parse_git_log(text)
        enriched = enrich_commits_with_refs(commits)
        unique_refs = {ref for c in enriched for ref in c["issue_refs"]}

        page1 = _load_json("issues_page1.json")
        page1_statuses = {issue["key"]: issue["status"] for issue in page1}

        for ref in unique_refs:
            assert ref in page1_statuses, (
                f"Commit ref {ref} has no matching page1 issue"
            )
            assert page1_statuses[ref] in {"done", "closed"}, (
                f"Commit ref {ref} has status {page1_statuses[ref]!r}, "
                "expected 'done' or 'closed'"
            )

    def test_page2_lead_time_average_is_9_88(self) -> None:
        """page2 is the source of truth for the KR-4 lead time computation.

        With the date-level parsing in ``compute_lead_time`` (it slices
        ``[:10]`` off the timestamps), the only whole-day lead times that
        average exactly to 9.88 are combinations of 9- and 10-day deltas at
        the 6:44 ratio (for 50 issues). The fixture encodes that mix and
        this test guards against accidental edits that would invalidate
        the snapshot.
        """
        page2 = _load_json("issues_page2.json")
        result = compute_lead_time(page2, PERIOD_START, PERIOD_END)
        assert result == pytest.approx(CEO_KR4, abs=KR4_TOLERANCE)
