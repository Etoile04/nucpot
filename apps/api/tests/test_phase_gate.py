"""Tests for the Phase Gate validation script (NFM-864, B3.7).

Validates that each gate check function returns the correct GateResult
for known inputs, without requiring a live database or CI runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# G1: CI pipeline check
# ---------------------------------------------------------------------------


class TestGateCI:
    """Gate 1: All batch CI pipelines present and valid."""

    def test_all_workflows_present(self) -> None:
        """All 3 required workflow files exist."""
        from validate_phase_gate import _check_ci_pipelines

        result = _check_ci_pipelines()
        assert result.gate_id == "G1"
        assert result.passed

    def test_ci_gate_detail_mentions_count(self) -> None:
        """Detail string contains the count of found workflows."""
        from validate_phase_gate import _check_ci_pipelines

        result = _check_ci_pipelines()
        assert "3/3" in result.detail


# ---------------------------------------------------------------------------
# G2: Extraction accuracy check
# ---------------------------------------------------------------------------


class TestGateExtractionAccuracy:
    """Gate 2: Extraction accuracy >= 60%."""

    def test_passes_when_accuracy_above_threshold(self) -> None:
        """Gate passes when all benchmarks exceed 60%."""
        from unittest.mock import MagicMock, patch

        mock_bench = MagicMock(accuracy=0.70, total=10, threshold_met=True)

        with (
            patch("eval_extraction_accuracy.run_figure_detection_benchmark", return_value=MagicMock(accuracy=0.70, total=10, threshold_met=True)),
            patch("eval_extraction_accuracy.run_plot_extraction_benchmark", return_value=MagicMock(accuracy=0.65, total=10, threshold_met=True)),
            patch("eval_extraction_accuracy.run_table_extraction_benchmark", return_value=MagicMock(accuracy=0.80, total=10, threshold_met=True)),
        ):
            from validate_phase_gate import _check_extraction_accuracy

            result = _check_extraction_accuracy()
            assert result.gate_id == "G2"
            assert result.passed

    def test_fails_when_accuracy_below_threshold(self) -> None:
        """Gate fails when weighted accuracy drops below 60%."""
        from unittest.mock import MagicMock, patch

        with (
            patch("eval_extraction_accuracy.run_figure_detection_benchmark", return_value=MagicMock(accuracy=0.30, total=10, threshold_met=False)),
            patch("eval_extraction_accuracy.run_plot_extraction_benchmark", return_value=MagicMock(accuracy=0.40, total=10, threshold_met=False)),
            patch("eval_extraction_accuracy.run_table_extraction_benchmark", return_value=MagicMock(accuracy=0.50, total=10, threshold_met=False)),
        ):
            from validate_phase_gate import _check_extraction_accuracy

            result = _check_extraction_accuracy()
            assert result.passed is False


# ---------------------------------------------------------------------------
# G3: KG coverage check
# ---------------------------------------------------------------------------


class TestGateKGCoverage:
    """Gate 3: KG entity types >= 5 and relation types >= 10."""

    def test_passes_with_real_model(self) -> None:
        """Real KG model has >= 5 entity types and >= 10 relation types."""
        from validate_phase_gate import _check_kg_coverage

        result = _check_kg_coverage()
        assert result.gate_id == "G3"
        assert result.passed
        assert "Entity types:" in result.detail
        assert "Relation types:" in result.detail


# ---------------------------------------------------------------------------
# G4: KG query modes check
# ---------------------------------------------------------------------------


class TestGateKGQueryModes:
    """Gate 4: KG query API supports >= 3 query modes."""

    def test_passes_with_real_router(self) -> None:
        """Real kg.py router has >= 3 distinct endpoints."""
        from validate_phase_gate import _check_kg_query_modes

        result = _check_kg_query_modes()
        assert result.gate_id == "G4"
        assert result.passed

    def test_fails_when_router_missing(self, tmp_path: Path) -> None:
        """Gate fails when kg.py router file is absent."""
        from validate_phase_gate import _check_kg_query_modes

        import validate_phase_gate as vg

        original = vg._REPO_ROOT
        try:
            vg._REPO_ROOT = tmp_path / "nonexistent"
            result = _check_kg_query_modes()
            assert result.passed is False
            assert "not found" in result.detail
        finally:
            vg._REPO_ROOT = original


# ---------------------------------------------------------------------------
# G5: Fusion configurable check
# ---------------------------------------------------------------------------


class TestGateFusionConfigurable:
    """Gate 5: Conflict resolution supports >= 3 strategies."""

    def test_passes_with_real_enum(self) -> None:
        """ResolutionStrategy enum has >= 3 strategies including required ones."""
        from validate_phase_gate import _check_fusion_configurable

        result = _check_fusion_configurable()
        assert result.gate_id == "G5"
        assert result.passed
        assert "Strategies:" in result.evidence


# ---------------------------------------------------------------------------
# G6: Test coverage check
# ---------------------------------------------------------------------------


class TestGateCoverage:
    """Gate 6: Unit test coverage >= 80%."""

    def test_passes_when_coverage_above_threshold(
        self,
    ) -> None:
        """Gate passes when pytest --cov reports >= 80%."""
        import subprocess as sp
        from unittest.mock import patch

        with patch.object(sp, "run", return_value=MagicMock(
            stdout="TOTAL Coverage: 85.5%",
            stderr="",
        )):
            from validate_phase_gate import _check_test_coverage

            result = _check_test_coverage()
            assert result.gate_id == "G6"
            assert result.passed
            assert "85.5%" in result.detail

    def test_fails_when_coverage_below_threshold(
        self,
    ) -> None:
        """Gate fails when coverage is below 80%."""
        import subprocess as sp
        from unittest.mock import patch

        with patch.object(sp, "run", return_value=MagicMock(
            stdout="TOTAL Coverage: 72.3%",
            stderr="",
        )):
            from validate_phase_gate import _check_test_coverage

            result = _check_test_coverage()
            assert result.passed is False

    def test_uses_proxy_on_subprocess_failure(
        self,
    ) -> None:
        """Falls back to file-count proxy when subprocess fails."""
        import subprocess as sp
        from unittest.mock import patch

        with patch.object(sp, "run", side_effect=FileNotFoundError):
            from validate_phase_gate import _check_test_coverage

            result = _check_test_coverage()
            assert "proxy" in result.detail


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


class TestPhaseGateReport:
    """PhaseGateReport dataclass and serialization."""

    def test_all_passed_property(self) -> None:
        """all_passed is True only when all gates pass."""
        from validate_phase_gate import GateResult, PhaseGateReport

        report = PhaseGateReport(
            gates=[
                GateResult(gate_id="G1", name="CI", passed=True, detail="ok"),
                GateResult(gate_id="G2", name="Accuracy", passed=True, detail="ok"),
            ],
            timestamp="2026-07-14T00:00:00+00:00",
        )
        assert report.all_passed is True

        report_fail = PhaseGateReport(
            gates=[
                GateResult(gate_id="G1", name="CI", passed=True, detail="ok"),
                GateResult(gate_id="G2", name="Accuracy", passed=False, detail="low"),
            ],
            timestamp="2026-07-14T00:00:00+00:00",
        )
        assert report_fail.all_passed is False

    def test_to_json_serialization(self) -> None:
        """to_json produces valid JSON with expected keys."""
        from validate_phase_gate import GateResult, PhaseGateReport

        report = PhaseGateReport(
            gates=[
                GateResult(gate_id="G1", name="CI", passed=True, detail="ok"),
            ],
            timestamp="2026-07-14T00:00:00+00:00",
        )
        data = report.to_json()
        parsed = json.loads(json.dumps(data))
        assert parsed["all_passed"] is True
        assert parsed["total_gates"] == 1
        assert len(parsed["gates"]) == 1
        assert parsed["gates"][0]["gate_id"] == "G1"

    def test_to_text_contains_gate_status(self) -> None:
        """to_text includes PASS/FAIL markers for each gate."""
        from validate_phase_gate import GateResult, PhaseGateReport

        report = PhaseGateReport(
            gates=[
                GateResult(gate_id="G1", name="CI", passed=True, detail="ok"),
                GateResult(gate_id="G2", name="Accuracy", passed=False, detail="low"),
            ],
            timestamp="2026-07-14T00:00:00+00:00",
        )
        text = report.to_text()
        assert "[PASS]" in text
        assert "[FAIL]" in text
        assert "1/2" in text
