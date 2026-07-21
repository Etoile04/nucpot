"""Tests for compute_incremental_features module (NFM-1679).

Covers: composition parsing, material naming, anomaly detection,
NULL rate computation, file-mode feature computation, and report
formatting.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from nfm_db.ml.compute_incremental_features import (
    AnomalyRecord,
    ComputeResult,
    NullRateReport,
    _compute_null_rates,
    _detect_anomalies,
    _make_formula,
    _make_material_name,
    _parse_composition,
    compute_features_from_csv,
    format_anomaly_report,
    load_incremental_csv,
)


# ---------------------------------------------------------------------------
# _parse_composition
# ---------------------------------------------------------------------------


class TestParseComposition:
    """Tests for _parse_composition helper."""

    def test_valid_json_at_percent(self) -> None:
        result = _parse_composition('{"U": 10, "Zr": 90.0}')
        assert result == {"U": 10.0, "Zr": 90.0}

    def test_valid_json_fraction(self) -> None:
        result = _parse_composition('{"Mo": 0.05, "U": 0.95}')
        assert result == {"Mo": 0.05, "U": 0.95}

    def test_empty_string(self) -> None:
        assert _parse_composition("") is None

    def test_whitespace_only(self) -> None:
        assert _parse_composition("   ") is None

    def test_invalid_json(self) -> None:
        assert _parse_composition("not-json") is None

    def test_empty_dict(self) -> None:
        assert _parse_composition("{}") is None

    def test_non_dict_json(self) -> None:
        assert _parse_composition("[1,2,3]") is None

    def test_filters_zero_values(self) -> None:
        result = _parse_composition('{"U": 0, "Mo": 10}')
        assert result == {"Mo": 10.0}

    def test_string_keys_coerced(self) -> None:
        result = _parse_composition('{"U": 90, "Mo": 10}')
        assert result is not None
        assert all(isinstance(k, str) for k in result)


# ---------------------------------------------------------------------------
# _make_material_name
# ---------------------------------------------------------------------------


class TestMakeMaterialName:
    """Tests for _make_material_name helper."""

    def test_binary_at_percent(self) -> None:
        name = _make_material_name({"U": 90.0, "Mo": 10.0})
        assert name == "U-90Mo-10"

    def test_binary_fraction(self) -> None:
        name = _make_material_name({"U": 0.9, "Mo": 0.1})
        assert name == "U-90Mo-10"

    def test_ternary(self) -> None:
        name = _make_material_name({"Mo": 5.0, "U": 90.0, "Zr": 5.0})
        assert name == "U-90Mo-5Zr-5"

    def test_fractional_percent(self) -> None:
        name = _make_material_name({"U": 88.5, "Mo": 11.5})
        assert "U-88.5" in name
        assert "Mo-11.5" in name


# ---------------------------------------------------------------------------
# _make_formula
# ---------------------------------------------------------------------------


class TestMakeFormula:
    """Tests for _make_formula helper."""

    def test_binary(self) -> None:
        formula = _make_formula({"U": 0.9, "Mo": 0.1})
        assert formula == "U0.90Mo0.10"

    def test_at_percent_normalizes(self) -> None:
        formula = _make_formula({"U": 90.0, "Mo": 10.0})
        assert formula == "U0.90Mo0.10"


# ---------------------------------------------------------------------------
# _detect_anomalies
# ---------------------------------------------------------------------------


class TestDetectAnomalies:
    """Tests for anomaly detection logic."""

    def test_no_anomalies_uniform(self) -> None:
        features = {
            "Mat-A": {"f1": 1.0, "f2": 2.0},
            "Mat-B": {"f1": 1.1, "f2": 2.1},
        }
        anomalies = _detect_anomalies(features, ["f1", "f2"])
        assert len(anomalies) == 0

    def test_detects_outlier(self) -> None:
        # 20 tight values + 1 extreme outlier → z > 3σ
        base = {f"Mat-{i:02d}": {"f1": 1.0, "f2": 2.0} for i in range(20)}
        base["Mat-Outlier"] = {"f1": 100.0, "f2": 2.0}
        anomalies = _detect_anomalies(base, ["f1"])
        outlier = [a for a in anomalies if a.material_name == "Mat-Outlier"]
        assert len(outlier) == 1
        assert outlier[0].feature_name == "f1"

    def test_empty_input(self) -> None:
        anomalies = _detect_anomalies({}, ["f1"])
        assert anomalies == []

    def test_single_record_no_anomaly(self) -> None:
        features = {"Mat-A": {"f1": 1.0}}
        anomalies = _detect_anomalies(features, ["f1"])
        assert anomalies == []

    def test_null_values_skipped(self) -> None:
        features = {
            "Mat-A": {"f1": 1.0, "f2": None},
            "Mat-B": {"f1": 1.1, "f2": None},
        }
        anomalies = _detect_anomalies(features, ["f1", "f2"])
        assert len(anomalies) == 0

    def test_zero_std_no_anomaly(self) -> None:
        features = {
            "Mat-A": {"f1": 5.0},
            "Mat-B": {"f1": 5.0},
        }
        anomalies = _detect_anomalies(features, ["f1"])
        assert len(anomalies) == 0


# ---------------------------------------------------------------------------
# _compute_null_rates
# ---------------------------------------------------------------------------


class TestComputeNullRates:
    """Tests for NULL rate computation."""

    def test_no_nulls(self) -> None:
        features = {
            "Mat-A": {"f1": 1.0, "f2": 2.0},
            "Mat-B": {"f1": 1.1, "f2": 2.1},
        }
        reports = _compute_null_rates(features, ["f1", "f2"])
        assert all(r.null_rate == 0.0 for r in reports)

    def test_partial_nulls(self) -> None:
        features = {
            "Mat-A": {"f1": 1.0, "f2": None},
            "Mat-B": {"f1": 1.1, "f2": 2.1},
        }
        reports = _compute_null_rates(features, ["f1", "f2"])
        f1_report = next(r for r in reports if r.feature_name == "f1")
        f2_report = next(r for r in reports if r.feature_name == "f2")
        assert f1_report.null_rate == 0.0
        assert f2_report.null_rate == 0.5

    def test_empty_input(self) -> None:
        reports = _compute_null_rates({}, ["f1"])
        assert reports == []

    def test_all_null(self) -> None:
        features = {
            "Mat-A": {"f1": None},
            "Mat-B": {"f1": None},
        }
        reports = _compute_null_rates(features, ["f1"])
        assert reports[0].null_rate == 1.0


# ---------------------------------------------------------------------------
# format_anomaly_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    """Tests for report formatting."""

    def test_basic_report(self) -> None:
        result = ComputeResult(
            total_records=200,
            computed_count=200,
            skipped_count=0,
            null_reports=(
                NullRateReport("f1", 200, 0, 0.0),
                NullRateReport("f2", 200, 0, 0.0),
            ),
        )
        report = format_anomaly_report(result)
        assert "200" in report
        assert "None detected" in report

    def test_with_anomalies(self) -> None:
        result = ComputeResult(
            total_records=10,
            computed_count=10,
            anomalies=(
                AnomalyRecord("Mat-X", "f1", 100.0, 5.0, 1.0, 95.0),
            ),
        )
        report = format_anomaly_report(result)
        assert "Mat-X" in report
        assert "f1" in report

    def test_with_warnings(self) -> None:
        result = ComputeResult(
            warnings=("Feature 'f1' has NULL rate 5.00%",),
        )
        report = format_anomaly_report(result)
        assert "⚠" in report


# ---------------------------------------------------------------------------
# load_incremental_csv & compute_features_from_csv (integration)
# ---------------------------------------------------------------------------


class TestLoadAndCompute:
    """Integration tests with synthetic CSV data."""

    @pytest.fixture()
    def sample_csv(self, tmp_path: Path) -> Path:
        csv_file = tmp_path / "test_dft.csv"
        with csv_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "element_system", "composition", "phase", "functional",
                "formation_energy",
            ])
            writer.writerow([
                'U-Zr', '{"U": 10, "Zr": 90.0}', "BCC", "PBE", -1.46,
            ])
            writer.writerow([
                'Mo-U', '{"Mo": 5, "U": 95.0}', "BCC", "PBE", -2.10,
            ])
            writer.writerow([
                'bad-row', 'invalid-json', "BCC", "PBE", -0.5,
            ])
        return csv_file

    def test_load_csv(self, sample_csv: Path) -> None:
        records = load_incremental_csv(sample_csv)
        assert len(records) == 2
        assert records[0]["composition_parsed"] == {"U": 10.0, "Zr": 90.0}

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_incremental_csv(tmp_path / "nonexistent.csv")

    def test_compute_from_csv(self, sample_csv: Path) -> None:
        result = compute_features_from_csv(sample_csv)
        assert result.total_records == 2
        assert result.computed_count == 2
        assert result.skipped_count == 0
        assert len(result.null_reports) == 8
        assert all(nr.null_rate == 0.0 for nr in result.null_reports)

    def test_empty_csv(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("composition,phase\n", encoding="utf-8")
        result = compute_features_from_csv(csv_file)
        assert result.total_records == 0
        assert result.computed_count == 0


# ---------------------------------------------------------------------------
# Acceptance criteria validation
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Verify acceptance criteria: NULL rate < 1% on real data."""

    def test_real_data_null_rate_under_1pct(self) -> None:
        """AC: 特征NULL率 < 1%"""
        csv_path = Path(__file__).resolve().parents[1] / "data" / "dft_incremental_200.csv"
        if not csv_path.exists():
            pytest.skip("data/dft_incremental_200.csv not available")

        result = compute_features_from_csv(csv_path)
        assert result.computed_count == 200, (
            f"Expected 200 records, got {result.computed_count}"
        )
        for nr in result.null_reports:
            assert nr.null_rate < 0.01, (
                f"{nr.feature_name}: NULL rate {nr.null_rate:.2%} >= 1%"
            )

    def test_anomaly_report_generated(self) -> None:
        """AC: 异常值检测报告生成"""
        csv_path = Path(__file__).resolve().parents[1] / "data" / "dft_incremental_200.csv"
        if not csv_path.exists():
            pytest.skip("data/dft_incremental_200.csv not available")

        result = compute_features_from_csv(csv_path)
        report = format_anomaly_report(result)
        assert "INCREMENTAL FEATURE COMPUTATION REPORT" in report
        assert "NULL RATE BY FEATURE" in report

    def test_all_8_features_computed(self) -> None:
        """AC: 8维物理特征全部计算完成"""
        csv_path = Path(__file__).resolve().parents[1] / "data" / "dft_incremental_200.csv"
        if not csv_path.exists():
            pytest.skip("data/dft_incremental_200.csv not available")

        result = compute_features_from_csv(csv_path)
        assert result.computed_count == 200
        assert len(result.null_reports) == 8
        expected_features = {
            "mo_equivalent", "lattice_distortion", "allen_chi_diff",
            "vec", "cluster_I", "cluster_II", "cluster_III", "cluster_IV",
        }
        actual_features = {nr.feature_name for nr in result.null_reports}
        assert actual_features == expected_features
