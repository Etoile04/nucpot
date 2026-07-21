"""Tests for data_pipeline.py (NFM-1547).

Validates:
  - Training set loading (from parquet or recompute)
  - sklearn-ready data preparation (X, y numpy arrays)
  - Data quality validation (NaN, infinite, outliers, class distribution)
  - Stratified train/val splitting
  - Full pipeline end-to-end
  - Quality report formatting
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import TestCase

import numpy as np
import pandas as pd
import pytest

from nfm_db.ml.data_pipeline import (
    IQR_MULTIPLIER,
    ML_FEATURE_COLUMNS,
    QualityReport,
    _detect_outliers_iqr,
    format_quality_report,
    load_training_set,
    prepare_sklearn_data,
    run_full_pipeline,
    split_train_val,
    validate_data_quality,
)
from nfm_db.ml.merge_training_set import (
    ML_FEATURE_COLUMNS as MERGE_FEATURE_COLUMNS,
    merge_all_sources,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def training_df() -> pd.DataFrame:
    """Load training DataFrame (skips if data unavailable)."""
    if not DATA_DIR.exists():
        pytest.skip("Data directory not available")
    parquet_files = sorted(DATA_DIR.glob("training_set_*.parquet"))
    if not parquet_files:
        pytest.skip("No training set parquet found")
    return pd.read_parquet(parquet_files[-1])


@pytest.fixture
def training_X_y(training_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Prepare sklearn-ready X, y from training DataFrame."""
    return prepare_sklearn_data(training_df)


# ---------------------------------------------------------------------------
# Unit Tests: IQR outlier detection
# ---------------------------------------------------------------------------


class TestDetectOutliersIQR:
    """Test the IQR outlier detection helper."""

    def test_no_outliers_uniform_data(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _detect_outliers_iqr(s)
        assert result["count"] == 0

    def test_detects_upper_outlier(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
        result = _detect_outliers_iqr(s)
        assert result["count"] > 0
        assert result["upper_bound"] is not None

    def test_detects_lower_outlier(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, -100.0])
        result = _detect_outliers_iqr(s)
        assert result["count"] > 0
        assert result["lower_bound"] is not None

    def test_returns_bounds(self) -> None:
        s = pd.Series(range(1, 21))  # 1..20
        result = _detect_outliers_iqr(s, multiplier=1.5)
        assert result["lower_bound"] is not None
        assert result["upper_bound"] is not None
        assert result["lower_bound"] < result["upper_bound"]

    def test_short_series_no_outlier(self) -> None:
        s = pd.Series([1.0, 2.0])
        result = _detect_outliers_iqr(s)
        assert result["count"] == 0
        assert result["lower_bound"] is None


# ---------------------------------------------------------------------------
# Unit Tests: prepare_sklearn_data
# ---------------------------------------------------------------------------


class TestPrepareSklearnData:
    """Test sklearn data preparation with synthetic DataFrames."""

    def _make_df(self, n: int = 100) -> pd.DataFrame:
        """Create a synthetic training DataFrame for testing."""
        np.random.seed(42)
        rows = []
        for _ in range(n):
            features = {
                "mo_equivalent": np.random.uniform(0, 2),
                "lattice_distortion": np.random.uniform(0, 0.3),
                "allen_chi_diff": np.random.uniform(0, 0.8),
                "vec": np.random.uniform(3, 10),
                "cluster_I": np.random.uniform(0, 1),
                "cluster_II": np.random.uniform(0, 1),
                "cluster_III": np.random.uniform(0, 1),
                "cluster_IV": np.random.uniform(0, 1),
                "label": np.random.choice(["H", "M"]),
            }
            rows.append(features)
        return pd.DataFrame(rows)

    def test_returns_numpy_arrays(self) -> None:
        df = self._make_df(50)
        X, y = prepare_sklearn_data(df)
        assert isinstance(X, np.ndarray)
        assert isinstance(y, np.ndarray)

    def test_x_shape_matches_features(self) -> None:
        df = self._make_df(50)
        X, y = prepare_sklearn_data(df)
        assert X.shape[1] == len(ML_FEATURE_COLUMNS)
        assert X.shape[0] == len(df)

    def test_y_shape_matches_x(self) -> None:
        df = self._make_df(50)
        X, y = prepare_sklearn_data(df)
        assert X.shape[0] == y.shape[0]

    def test_y_binary_encoding(self) -> None:
        df = self._make_df(50)
        X, y = prepare_sklearn_data(df)
        unique_labels = set(y.tolist())
        assert unique_labels.issubset({0, 1})

    def test_h_maps_to_zero(self) -> None:
        df = pd.DataFrame({
            **{col: [0.0] * 3 for col in ML_FEATURE_COLUMNS},
            "label": ["H", "H", "M"],
        })
        _, y = prepare_sklearn_data(df)
        assert list(y) == [0, 0, 1]

    def test_unmapped_label_filtered(self) -> None:
        df = pd.DataFrame({
            **{col: [0.0] * 2 for col in ML_FEATURE_COLUMNS},
            "label": ["X", "H"],
        })
        X, y = prepare_sklearn_data(df)
        # Unmapped label "X" is filtered out, only "H" remains
        assert len(X) == 1
        assert len(y) == 1
        assert y[0] == 0

    def test_custom_binary_map(self) -> None:
        df = pd.DataFrame({
            **{col: [0.0] * 2 for col in ML_FEATURE_COLUMNS},
            "label": ["H", "M"],
        })
        _, y = prepare_sklearn_data(df, binary_map={"H": 1, "M": 0})
        assert list(y) == [1, 0]


# ---------------------------------------------------------------------------
# Integration Tests: load_training_set
# ---------------------------------------------------------------------------


class TestLoadTrainingSet:
    @pytest.mark.integration
    def test_load_from_parquet(self, training_df: pd.DataFrame) -> None:
        assert len(training_df) >= 1400
        for col in ML_FEATURE_COLUMNS:
            assert col in training_df.columns

    @pytest.mark.integration
    def test_no_nan_in_features(self, training_df: pd.DataFrame) -> None:
        for col in ML_FEATURE_COLUMNS:
            assert training_df[col].isnull().sum() == 0

    @pytest.mark.integration
    def test_load_returns_dataframe(self) -> None:
        df = load_training_set()
        assert isinstance(df, pd.DataFrame)

    @pytest.mark.integration
    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_training_set(parquet_path=Path("/nonexistent/file.parquet"))


# ---------------------------------------------------------------------------
# Integration Tests: validate_data_quality
# ---------------------------------------------------------------------------


class TestValidateDataQuality:
    @pytest.mark.integration
    def test_clean_data_passes(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert report.passed

    @pytest.mark.integration
    def test_total_samples(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert report.total_samples == len(training_df)

    @pytest.mark.integration
    def test_no_nan(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert report.nan_check["passed"]
        assert report.nan_check["total_nan_values"] == 0

    @pytest.mark.integration
    def test_no_inf(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert report.inf_check["passed"]

    @pytest.mark.integration
    def test_class_distribution_present(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert len(report.class_distribution) > 0

    @pytest.mark.integration
    def test_class_ratios_sum_to_one(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        total_ratio = sum(
            info["ratio"] for info in report.class_distribution.values()
        )
        assert abs(total_ratio - 1.0) < 0.01

    @pytest.mark.integration
    def test_feature_stats_present(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        for col in ML_FEATURE_COLUMNS:
            assert col in report.feature_statistics
            stats = report.feature_statistics[col]
            assert "min" in stats
            assert "max" in stats
            assert "mean" in stats
            assert "std" in stats

    @pytest.mark.integration
    def test_outlier_check_runs(self, training_df: pd.DataFrame) -> None:
        report = validate_data_quality(training_df)
        assert "total_outliers" in report.outlier_check

    def test_detects_nan_values(self) -> None:
        df = pd.DataFrame({
            "mo_equivalent": [0.1, np.nan, 0.3],
            "lattice_distortion": [0.01, 0.02, 0.03],
            "allen_chi_diff": [0.1, 0.2, 0.3],
            "vec": [5.0, 5.1, 5.2],
            "cluster_I": [0.5, 0.6, 0.7],
            "cluster_II": [0.3, 0.2, 0.1],
            "cluster_III": [0.1, 0.1, 0.1],
            "cluster_IV": [0.1, 0.1, 0.1],
            "label": ["H", "M", "H"],
        })
        report = validate_data_quality(df)
        assert not report.passed
        assert not report.nan_check["passed"]
        assert report.nan_check["total_nan_values"] == 1
        assert "mo_equivalent" in report.nan_check["nan_columns"]

    def test_detects_inf_values(self) -> None:
        df = pd.DataFrame({
            "mo_equivalent": [0.1, np.inf, 0.3],
            "lattice_distortion": [0.01, 0.02, 0.03],
            "allen_chi_diff": [0.1, 0.2, 0.3],
            "vec": [5.0, 5.1, 5.2],
            "cluster_I": [0.5, 0.6, 0.7],
            "cluster_II": [0.3, 0.2, 0.1],
            "cluster_III": [0.1, 0.1, 0.1],
            "cluster_IV": [0.1, 0.1, 0.1],
            "label": ["H", "M", "H"],
        })
        report = validate_data_quality(df)
        assert not report.passed
        assert not report.inf_check["passed"]

    def test_missing_column_warning(self) -> None:
        # Use a subset of features to trigger missing column warning
        df = pd.DataFrame({
            "mo_equivalent": [0.1, 0.2],
            "label": ["H", "M"],
        })
        report = validate_data_quality(df)
        assert not report.passed
        assert any("not found" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Tests: split_train_val
# ---------------------------------------------------------------------------


class TestSplitTrainVal:
    def test_returns_expected_keys(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 50 + [1] * 50)
        result = split_train_val(X, y, val_ratio=0.2, seed=42)
        expected_keys = {"X_train", "X_val", "y_train", "y_val", "train_size", "val_size"}
        assert set(result.keys()) == expected_keys

    def test_split_sizes(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 50 + [1] * 50)
        result = split_train_val(X, y, val_ratio=0.2, seed=42)
        assert result["train_size"] + result["val_size"] == 100
        assert result["val_size"] == 20

    def test_no_overlap(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 50 + [1] * 50)
        result = split_train_val(X, y, val_ratio=0.2, seed=42)
        assert result["train_size"] + result["val_size"] == 100

    def test_stratification(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 80 + [1] * 20)  # 80/20 imbalance
        result = split_train_val(X, y, val_ratio=0.2, seed=42)
        # Both splits should have some of each class
        train_unique = set(result["y_train"].tolist())
        val_unique = set(result["y_val"].tolist())
        assert 0 in train_unique and 1 in train_unique
        assert 0 in val_unique and 1 in val_unique

    def test_reproducibility(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 50 + [1] * 50)
        r1 = split_train_val(X, y, seed=42)
        r2 = split_train_val(X, y, seed=42)
        np.testing.assert_array_equal(r1["X_train"], r2["X_train"])
        np.testing.assert_array_equal(r1["y_train"], r2["y_train"])

    def test_different_seed_different_split(self) -> None:
        X = np.random.randn(100, 8)
        y = np.array([0] * 50 + [1] * 50)
        r1 = split_train_val(X, y, seed=42)
        r2 = split_train_val(X, y, seed=123)
        # Different seeds should generally produce different splits
        assert not np.array_equal(r1["y_train"], r2["y_train"])


# ---------------------------------------------------------------------------
# Tests: format_quality_report
# ---------------------------------------------------------------------------


class TestFormatQualityReport:
    def test_contains_sections(self) -> None:
        report = QualityReport(
            passed=True,
            total_samples=100,
            feature_columns=tuple(ML_FEATURE_COLUMNS),
            nan_check={"passed": True, "total_nan_values": 0, "nan_columns": {}},
            inf_check={"passed": True, "total_inf_values": 0, "inf_columns": {}},
            outlier_check={"total_outliers": 0, "outlier_columns": {}},
            class_distribution={"H": {"count": 60, "ratio": 0.6}},
            feature_statistics={},
            warnings=(),
        )
        text = format_quality_report(report)
        assert "Data Quality Report" in text
        assert "PASSED" in text
        assert "Missing Values" in text
        assert "Infinite Values" in text
        assert "Outliers" in text
        assert "Class Distribution" in text

    def test_failed_shows_failed(self) -> None:
        report = QualityReport(
            passed=False,
            total_samples=100,
            feature_columns=tuple(ML_FEATURE_COLUMNS),
            nan_check={"passed": False, "total_nan_values": 5, "nan_columns": {}},
            inf_check={"passed": True, "total_inf_values": 0, "inf_columns": {}},
            outlier_check={"total_outliers": 0, "outlier_columns": {}},
            class_distribution={},
            feature_statistics={},
            warnings=("Some warning",),
        )
        text = format_quality_report(report)
        assert "FAILED" in text
        assert "Warnings" in text
