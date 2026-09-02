"""Tests for scripts/okr/report.py — 5-KR weekly report aggregator (NFM-2041 B2)."""

# ruff: noqa: F841

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.okr.report import (
    _NO_DATA_BASELINE,
    build_kr_report,
    compute_kr3,
    compute_kr5,
    compute_lead_time,
    format_baseline,
    format_table,
)

# ---------------------------------------------------------------------------
# compute_lead_time
# ---------------------------------------------------------------------------


class TestComputeLeadTime:
    """KR-4: average lead time for done issues in a window."""

    def test_returns_mean_lead_time(self) -> None:
        issues = [
            {
                "key": "NFM-100",
                "status": "done",
                "createdAt": "2026-07-20T08:00:00Z",
                "updatedAt": "2026-07-30T08:00:00Z",
            },
            {
                "key": "NFM-101",
                "status": "done",
                "createdAt": "2026-07-21T08:00:00Z",
                "updatedAt": "2026-07-26T08:00:00Z",
            },
        ]
        result = compute_lead_time(issues, "2026-07-20", "2026-07-30")
        # (10 + 5) / 2 = 7.5
        assert result == 7.5

    def test_rounds_to_two_decimals(self) -> None:
        # 2026-07-20 06:00 → 2026-07-29 18:00 = 9 days 12h = 9.5 days
        # round(9.5, 2) = 9.5 in Python 3 (banker's rounding: 9.5→9.5)
        issues = [
            {
                "key": "NFM-100",
                "status": "done",
                "createdAt": "2026-07-20T06:00:00Z",
                "updatedAt": "2026-07-29T18:00:00Z",
            },
        ]
        result = compute_lead_time(issues, "2026-07-20", "2026-07-30")
        # date-level: 2026-07-29 - 2026-07-20 = 9.0 days
        # (time component is stripped by [:10] parsing)
        assert result == 9.0

    def test_returns_none_when_no_issues(self) -> None:
        assert compute_lead_time([], "2026-07-20", "2026-07-26") is None

    def test_filters_by_updated_at_window(self) -> None:
        issues = [
            {
                "key": "NFM-100",
                "status": "done",
                "createdAt": "2026-07-10T00:00:00Z",
                "updatedAt": "2026-07-15T00:00:00Z",
            },
            {
                "key": "NFM-101",
                "status": "done",
                "createdAt": "2026-07-20T00:00:00Z",
                "updatedAt": "2026-07-25T00:00:00Z",
            },
        ]
        # Only NFM-101 falls in window — 5 days
        result = compute_lead_time(issues, "2026-07-20", "2026-07-26")
        assert result == 5.0

    def test_skips_issues_with_missing_dates(self) -> None:
        issues = [
            {
                "key": "NFM-100",
                "status": "done",
                "createdAt": "",
                "updatedAt": "2026-07-25T00:00:00Z",
            },
            {
                "key": "NFM-101",
                "status": "done",
                "createdAt": "2026-07-20T00:00:00Z",
                "updatedAt": "",
            },
        ]
        assert compute_lead_time(issues, "2026-07-20", "2026-07-26") is None

    def test_single_issue_returns_exact_delta(self) -> None:
        # Lead time uses date-level granularity ([:10] parsing).
        # 2026-07-29 - 2026-07-20 = 9.0 days exactly at date level.
        issues = [
            {
                "key": "NFM-100",
                "status": "done",
                "createdAt": "2026-07-20T00:00:00Z",
                "updatedAt": "2026-07-29T00:00:00Z",
            },
        ]
        result = compute_lead_time(issues, "2026-07-20", "2026-07-30")
        assert result == 9.0


# ---------------------------------------------------------------------------
# compute_kr3
# ---------------------------------------------------------------------------


class TestComputeKr3:
    """KR-3: deploy first-pass success rate."""

    @patch("scripts.okr.report.build_kr3_report")
    @patch("scripts.okr.coverage_kr3._resolve_path")
    def test_returns_value_when_data_exists(
        self,
        mock_resolve: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_resolve.return_value = "/default/deploy.jsonl"
        mock_build.return_value = {"value": 0.85, "n": 10, "target": 0.90}
        result = compute_kr3("2026-07-20", "2026-07-26")
        assert result == {
            "key": "deploy_first_pass_success",
            "value": 0.85,
            "unit": "ratio",
        }
        mock_build.assert_called_once_with(
            path="/default/deploy.jsonl",
            since="2026-07-20",
            until="2026-07-26",
        )

    @patch("scripts.okr.report.build_kr3_report")
    @patch("scripts.okr.coverage_kr3._resolve_path")
    def test_returns_no_data_when_no_events(
        self,
        mock_resolve: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_resolve.return_value = "/default/deploy.jsonl"
        mock_build.return_value = {"value": None, "n": 0, "target": 0.90}
        result = compute_kr3("2026-07-20", "2026-07-26")
        assert result["status"] == _NO_DATA_BASELINE
        assert result["value"] is None

    @patch("scripts.okr.report.build_kr3_report")
    @patch("scripts.okr.coverage_kr3._resolve_path")
    def test_accepts_custom_deploy_path(
        self,
        mock_resolve: MagicMock,
        mock_build: MagicMock,
    ) -> None:
        mock_resolve.return_value = "/tmp/test.jsonl"
        mock_build.return_value = {"value": 0.90, "n": 5, "target": 0.90}
        result = compute_kr3(
            "2026-07-20",
            "2026-07-26",
            deploy_path="/tmp/test.jsonl",
        )
        mock_build.assert_called_once_with(
            path="/tmp/test.jsonl",
            since="2026-07-20",
            until="2026-07-26",
        )


# ---------------------------------------------------------------------------
# compute_kr5
# ---------------------------------------------------------------------------


class TestComputeKr5:
    """KR-5: test coverage."""

    def test_returns_no_data_when_no_paths(self) -> None:
        result = compute_kr5(None)
        assert result["status"] == _NO_DATA_BASELINE
        assert result["value"] is None

    def test_returns_no_data_when_empty_list(self) -> None:
        result = compute_kr5([])
        assert result["status"] == _NO_DATA_BASELINE

    @patch("scripts.okr.report.build_kr5_report")
    @patch("scripts.okr.report.Path")
    def test_returns_value_from_coverage(
        self, mock_path_cls: MagicMock, mock_build: MagicMock
    ) -> None:
        mock_build.return_value = {
            "line_rate": 0.82,
            "covered_lines": 820,
            "total_lines": 1000,
        }
        mock_path = MagicMock()
        mock_path.read_text.return_value = "<coverage/>"
        mock_path_cls.return_value = mock_path
        result = compute_kr5(["coverage.xml"])
        assert result == {
            "key": "test_coverage",
            "value": 0.82,
            "unit": "ratio",
        }
        mock_build.assert_called_once_with(["<coverage/>"])

    @patch("scripts.okr.report.Path")
    def test_skips_unreadable_files(self, mock_path_cls: MagicMock) -> None:
        mock_path = MagicMock()
        mock_path.read_text.side_effect = OSError("no such file")
        mock_path_cls.return_value = mock_path
        result = compute_kr5(["missing.xml"])
        assert result["status"] == _NO_DATA_BASELINE


# ---------------------------------------------------------------------------
# build_kr_report
# ---------------------------------------------------------------------------


class TestBuildKrReport:
    """Report assembly per ADR-REPEATABLE-1."""

    def test_full_report_schema(self) -> None:
        report = build_kr_report(
            period_start="2026-07-20",
            period_end="2026-07-26",
            kr1_value=0.130,
            kr2_value=0.318,
            kr3_entry={
                "key": "deploy_first_pass_success",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
            kr4_value=9.88,
            kr5_entry={
                "key": "test_coverage",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
        )

        assert report["period"] == {"start": "2026-07-20", "end": "2026-07-26"}
        assert "generated_at" in report
        assert "krs" in report

        krs = report["krs"]
        assert krs["KR-1"]["key"] == "commit_efficiency"
        assert krs["KR-1"]["value"] == 0.130
        assert krs["KR-1"]["unit"] == "ratio"

        assert krs["KR-2"]["key"] == "structural_waste_rate"
        assert krs["KR-2"]["value"] == 0.318

        assert krs["KR-3"]["status"] == _NO_DATA_BASELINE
        assert krs["KR-3"]["value"] is None

        assert krs["KR-4"]["key"] == "avg_lead_time"
        assert krs["KR-4"]["value"] == 9.88
        assert krs["KR-4"]["unit"] == "days"

        assert krs["KR-5"]["status"] == _NO_DATA_BASELINE

    def test_kr_ordering(self) -> None:
        report = build_kr_report(
            period_start="2026-07-20",
            period_end="2026-07-26",
            kr1_value=0.0,
            kr2_value=0.0,
            kr3_entry={
                "key": "deploy_first_pass_success",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
            kr4_value=0.0,
            kr5_entry={
                "key": "test_coverage",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
        )
        kr_keys = list(report["krs"].keys())
        assert kr_keys == ["KR-1", "KR-2", "KR-3", "KR-4", "KR-5"]

    def test_handles_none_values(self) -> None:
        report = build_kr_report(
            period_start="2026-07-20",
            period_end="2026-07-26",
            kr1_value=None,
            kr2_value=None,
            kr3_entry={
                "key": "deploy_first_pass_success",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
            kr4_value=None,
            kr5_entry={
                "key": "test_coverage",
                "value": None,
                "status": _NO_DATA_BASELINE,
            },
        )
        assert report["krs"]["KR-1"]["value"] is None
        assert report["krs"]["KR-4"]["value"] is None


# ---------------------------------------------------------------------------
# format_table
# ---------------------------------------------------------------------------


class TestFormatTable:
    """Human-readable table output."""

    def test_includes_period_header(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": None, "status": _NO_DATA_BASELINE},
            9.88,
            {"key": "test_coverage", "value": None, "status": _NO_DATA_BASELINE},
        )
        output = format_table(report)
        assert "2026-07-20" in output
        assert "2026-07-26" in output

    def test_shows_kr_labels(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": None, "status": _NO_DATA_BASELINE},
            9.88,
            {"key": "test_coverage", "value": None, "status": _NO_DATA_BASELINE},
        )
        output = format_table(report)
        assert "Commit Efficiency" in output
        assert "Structural Waste Rate" in output
        assert "Deploy" in output
        assert "Lead Time" in output

    def test_shows_no_data_for_baseline(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": None, "status": _NO_DATA_BASELINE},
            9.88,
            {"key": "test_coverage", "value": None, "status": _NO_DATA_BASELINE},
        )
        output = format_table(report)
        assert "[no data]" in output

    def test_shows_days_suffix_for_kr4(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": 0.90, "unit": "ratio"},
            9.88,
            {"key": "test_coverage", "value": 0.75, "unit": "ratio"},
        )
        output = format_table(report)
        assert "9.88 d" in output


# ---------------------------------------------------------------------------
# format_baseline
# ---------------------------------------------------------------------------


class TestFormatBaseline:
    """Lark OKR import envelope."""

    def test_wraps_in_goal_envelope(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": None, "status": _NO_DATA_BASELINE},
            9.88,
            {"key": "test_coverage", "value": None, "status": _NO_DATA_BASELINE},
        )
        envelope = format_baseline(report, "goal-123")
        assert envelope["type"] == "okr_baseline"
        assert envelope["goal_id"] == "goal-123"
        assert "key_results" in envelope
        assert envelope["period"] == {"start": "2026-07-20", "end": "2026-07-26"}

    def test_uses_default_goal_id(self) -> None:
        report = build_kr_report(
            "2026-07-20",
            "2026-07-26",
            0.13,
            0.318,
            {"key": "deploy_first_pass_success", "value": None, "status": _NO_DATA_BASELINE},
            9.88,
            {"key": "test_coverage", "value": None, "status": _NO_DATA_BASELINE},
        )
        envelope = format_baseline(report, None)
        assert envelope["goal_id"] == "company-okr"
