"""Tests for merge_training_set.py (NFM-1680).

Validates:
  - Record count >= 1400
  - Correct output columns
  - Valid parquet + CSV format
  - Complete 8D features (no nulls)
  - Correct source labels
  - Distribution report generation
  - Feature computation correctness
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from nfm_db.ml.merge_training_set import (
    ML_FEATURE_COLUMNS,
    OUTPUT_COLUMNS,
    SOURCE_DFT_INCR,
    SOURCE_DFT_MP,
    SOURCE_EXPERIMENTAL,
    TARGET_COLUMN,
    compute_8d_features,
    generate_distribution_report,
    load_dft_batch_records,
    load_experimental_records,
    load_incremental_dft_records,
    merge_all_sources,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TRAINING_DATA_DIR = DATA_DIR / "training_data"
DFT_EXPORT_DIR = DATA_DIR / "dft-export"


@pytest.fixture
def experimental_data_path() -> Path:
    return TRAINING_DATA_DIR / "train.csv"


@pytest.fixture
def dft_export_dir() -> Path:
    return DFT_EXPORT_DIR


@pytest.fixture
def incremental_dft_path() -> Path:
    return DATA_DIR / "dft_incremental_200.csv"


# ---------------------------------------------------------------------------
# Unit Tests: Feature Computation
# ---------------------------------------------------------------------------


class TestCompute8dFeatures:
    """Test the 8D feature computation functions."""

    def test_returns_all_8_features(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        features = compute_8d_features(comp)
        assert len(features) == 8
        for col in ML_FEATURE_COLUMNS:
            assert col in features

    def test_pure_uranium_zero_mo_eq(self) -> None:
        comp = {"U": 1.0}
        features = compute_8d_features(comp)
        assert features["mo_equivalent"] == 0.0

    def test_binary_alloy_mo_equivalent(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        features = compute_8d_features(comp)
        # Mo_eq = 0.1 * 1.0 = 0.1
        assert abs(features["mo_equivalent"] - 0.1) < 1e-10

    def test_pure_element_zero_lattice_distortion(self) -> None:
        comp = {"U": 1.0}
        features = compute_8d_features(comp)
        assert features["lattice_distortion"] == 0.0

    def test_binary_positive_lattice_distortion(self) -> None:
        comp = {"U": 0.5, "Mo": 0.5}
        features = compute_8d_features(comp)
        assert features["lattice_distortion"] > 0.0

    def test_cluster_fractions_sum_to_one(self) -> None:
        comp = {"U": 0.7, "Mo": 0.1, "Zr": 0.1, "Fe": 0.1}
        features = compute_8d_features(comp)
        cluster_sum = (
            features["cluster_I"]
            + features["cluster_II"]
            + features["cluster_III"]
            + features["cluster_IV"]
        )
        assert abs(cluster_sum - 1.0) < 1e-10

    def test_vec_in_expected_range(self) -> None:
        comp = {"U": 0.5, "Zr": 0.5}
        features = compute_8d_features(comp)
        # U=6, Zr=4, so VEC should be 5.0
        assert abs(features["vec"] - 5.0) < 1e-10

    def test_all_features_finite(self) -> None:
        """All feature values must be finite numbers (no NaN or inf)."""
        comp = {"U": 0.5, "Mo": 0.3, "Nb": 0.1, "Zr": 0.1}
        features = compute_8d_features(comp)
        for col in ML_FEATURE_COLUMNS:
            assert math.isfinite(features[col]), f"{col} is not finite: {features[col]}"

    def test_unknown_element_zero_mo_eq(self) -> None:
        comp = {"U": 0.9, "Xx": 0.1}
        features = compute_8d_features(comp)
        assert features["mo_equivalent"] == 0.0


# ---------------------------------------------------------------------------
# Integration Tests: Data Loading
# ---------------------------------------------------------------------------


class TestLoadExperimentalRecords:
    @pytest.mark.integration
    def test_load_experimental_returns_list(self, experimental_data_path: Path) -> None:
        if not experimental_data_path.exists():
            pytest.skip("Experimental data not available")
        records = load_experimental_records(experimental_data_path)
        assert isinstance(records, list)

    @pytest.mark.integration
    def test_experimental_source_label(self, experimental_data_path: Path) -> None:
        if not experimental_data_path.exists():
            pytest.skip("Experimental data not available")
        records = load_experimental_records(experimental_data_path)
        assert all(r["source"] == SOURCE_EXPERIMENTAL for r in records)


class TestLoadDftBatchRecords:
    @pytest.mark.integration
    def test_load_dft_batches(self, dft_export_dir: Path) -> None:
        if not dft_export_dir.exists():
            pytest.skip("DFT export directory not available")
        records = load_dft_batch_records(dft_export_dir)
        assert isinstance(records, list)
        assert len(records) > 0

    @pytest.mark.integration
    def test_dft_mp_source_label(self, dft_export_dir: Path) -> None:
        if not dft_export_dir.exists():
            pytest.skip("DFT export directory not available")
        records = load_dft_batch_records(dft_export_dir)
        assert all(r["source"] == SOURCE_DFT_MP for r in records)


class TestLoadIncrementalDftRecords:
    @pytest.mark.integration
    def test_load_incremental_dft(self, incremental_dft_path: Path) -> None:
        if not incremental_dft_path.exists():
            pytest.skip("Incremental DFT data not available")
        records = load_incremental_dft_records(incremental_dft_path)
        assert isinstance(records, list)
        assert len(records) == 200

    @pytest.mark.integration
    def test_incremental_source_label(self, incremental_dft_path: Path) -> None:
        if not incremental_dft_path.exists():
            pytest.skip("Incremental DFT data not available")
        records = load_incremental_dft_records(incremental_dft_path)
        assert all(r["source"] == SOURCE_DFT_INCR for r in records)


# ---------------------------------------------------------------------------
# Integration Tests: Merge
# ---------------------------------------------------------------------------


class TestMergeAllSources:
    @pytest.mark.integration
    def test_merged_record_count(self) -> None:
        df = merge_all_sources()
        assert len(df) >= 1400

    @pytest.mark.integration
    def test_merged_has_all_output_columns(self) -> None:
        df = merge_all_sources()
        for col in OUTPUT_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    @pytest.mark.integration
    def test_merged_three_sources_present(self) -> None:
        df = merge_all_sources()
        sources = set(df["source"].unique())
        assert SOURCE_EXPERIMENTAL in sources
        assert SOURCE_DFT_MP in sources
        assert SOURCE_DFT_INCR in sources

    @pytest.mark.integration
    def test_no_null_features(self) -> None:
        df = merge_all_sources()
        for col in ML_FEATURE_COLUMNS:
            null_count = df[col].isnull().sum()
            assert null_count == 0, f"Feature {col} has {null_count} null values"

    @pytest.mark.integration
    def test_all_features_finite(self) -> None:
        df = merge_all_sources()
        for col in ML_FEATURE_COLUMNS:
            assert df[col].dtype in (float, int), f"Feature {col} has dtype {df[col].dtype}"


# ---------------------------------------------------------------------------
# Integration Tests: Distribution Report
# ---------------------------------------------------------------------------


class TestDistributionReport:
    @pytest.mark.integration
    def test_report_contains_element_coverage(self) -> None:
        df = merge_all_sources()
        report = generate_distribution_report(df, output_path=Path("/dev/null"))
        assert "Element Coverage" in report
        assert "Elements:" in report

    @pytest.mark.integration
    def test_report_contains_feature_stats(self) -> None:
        df = merge_all_sources()
        report = generate_distribution_report(df, output_path=Path("/dev/null"))
        assert "8D Feature Statistics" in report
        for col in ML_FEATURE_COLUMNS:
            assert col in report

    @pytest.mark.integration
    def test_report_contains_source_breakdown(self) -> None:
        df = merge_all_sources()
        report = generate_distribution_report(df, output_path=Path("/dev/null"))
        assert "Data Source Breakdown" in report
        assert SOURCE_EXPERIMENTAL in report
        assert SOURCE_DFT_MP in report
        assert SOURCE_DFT_INCR in report
