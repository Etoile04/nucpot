"""Tests for training_data.py — ML dataset builder + feature computation (NFM-1566).

TDD RED phase: these tests define the contract for training_data.py.
Each test verifies one acceptance criterion or public function.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from nfm_db.ml.feature_engineering import _FEATURE_COLUMNS, compute_all_features
from nfm_db.ml.training_data import (
    build_feature_matrix,
    build_label_vector,
    build_ml_dataset,
    create_cv_indices,
    generate_data_quality_report,
    load_experimental_data,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_sample_entries(count: int = 10) -> list[dict[str, Any]]:
    """Create synthetic experimental data entries for testing."""
    entries = []
    for i in range(count):
        mo_pct = i * 2.0  # 0, 2, 4, ... at.%
        u_pct = 100.0 - mo_pct
        entries.append({
            "id": i + 1,
            "alloy_system": "U-Mo",
            "alloy_type": "binary",
            "composition": {"U": u_pct, "Mo": mo_pct},
            "formula": f"U-{int(mo_pct)}at%Mo" if mo_pct > 0 else "U",
            "crystal_structure_initial": "alpha" if mo_pct < 5 else "gamma",
            "crystal_structure_final": "beta" if mo_pct < 5 else "gamma",
            "phase_transition": "alpha_to_beta" if mo_pct < 5 else "gamma_stable",
            "transition_temperature_C": round(668.0 - mo_pct * 8.0, 1) if mo_pct < 10 else None,
            "transition_temperature_K": round(941.15 - mo_pct * 8.0, 2) if mo_pct < 10 else None,
            "uncertainty_C": 2.0 + i * 0.5 if mo_pct < 10 else None,
            "experimental_method": "DSC",
            "source_credibility": "test",
            "source_reference": "Test data",
            "source_doi": "",
            "notes": "Synthetic test entry",
        })
    return entries


def _write_sample_json(entries: list[dict[str, Any]], tmp_path: Path) -> Path:
    """Write sample entries to a JSON file and return the path."""
    data = {
        "_meta": {
            "title": "Test Dataset",
            "version": "1.0.0",
            "generated_date": "2026-07-19",
            "generated_by": "test",
            "purpose": "ML model training",
            "total_entries": len(entries),
            "schema_notes": "Test data",
            "confidence_overall": "high",
            "domain_lenses_applied": [],
        },
        "data": entries,
    }
    json_path = tmp_path / "test_phase_transitions.json"
    json_path.write_text(json.dumps(data, indent=2))
    return json_path


@pytest.fixture
def sample_json_path(tmp_path: Path) -> Path:
    """Create a temp JSON file with synthetic experimental data."""
    entries = _make_sample_entries(10)
    return _write_sample_json(entries, tmp_path)


@pytest.fixture
def real_json_path() -> Path:
    """Path to the real experimental data JSON (55 entries)."""
    root = Path(__file__).resolve().parent.parent
    return root / "experiments" / "phase_transition_data" / "ux_phase_transitions.json"


# ---------------------------------------------------------------------------
# 1. load_experimental_data
# ---------------------------------------------------------------------------

class TestLoadExperimentalData:
    """Test loading experimental phase transition data from JSON."""

    def test_loads_all_entries(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        assert len(raw) == 10

    def test_returns_list_of_dicts(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        assert isinstance(raw, list)
        assert isinstance(raw[0], dict)

    def test_entries_have_composition(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        assert "composition" in raw[0]
        assert isinstance(raw[0]["composition"], dict)

    def test_entries_have_transition_temp(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        assert "transition_temperature_K" in raw[0]

    def test_entries_have_phase_transition(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        assert "phase_transition" in raw[0]

    def test_loads_real_data_55_entries(self, real_json_path: Path) -> None:
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        raw = load_experimental_data(real_json_path)
        assert len(raw) == 55

    def test_raises_on_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_experimental_data(Path("/nonexistent/path.json"))

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("not valid json{{{")
        with pytest.raises(ValueError, match="valid JSON"):
            load_experimental_data(bad_path)

    def test_raises_on_missing_data_key(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "no_data.json"
        bad_path.write_text(json.dumps({"_meta": {}, "wrong_key": []}))
        with pytest.raises(ValueError, match="'data'"):
            load_experimental_data(bad_path)

    def test_preserves_null_temperatures(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        null_entries = [e for e in raw if e.get("transition_temperature_K") is None]
        assert len(null_entries) > 0


# ---------------------------------------------------------------------------
# 2. build_feature_matrix
# ---------------------------------------------------------------------------

class TestBuildFeatureMatrix:
    """Test building the X feature matrix (n_samples, 8)."""

    def test_matrix_shape(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        assert X.shape == (10, 8)

    def test_feature_column_names(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        assert list(X.columns) == _FEATURE_COLUMNS

    def test_no_nan_in_features(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        assert not X.isna().any().any(), "Feature matrix contains NaN values"

    def test_feature_values_are_numeric(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        for col in X.columns:
            assert pd.api.types.is_numeric_dtype(X[col]), f"Column {col} is not numeric"

    def test_real_data_matrix_shape(self, real_json_path: Path) -> None:
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        raw = load_experimental_data(real_json_path)
        X = build_feature_matrix(raw)
        assert X.shape == (55, 8)

    def test_raises_on_empty_input(self) -> None:
        with pytest.raises(ValueError):
            build_feature_matrix([])

    def test_computation_matches_manual(self, sample_json_path: Path) -> None:
        """Verify feature computation matches manual calculation for first entry."""
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        manual = compute_all_features(raw[0]["composition"])
        for col in _FEATURE_COLUMNS:
            assert math.isclose(X.iloc[0][col], manual[col], rel_tol=1e-10)


# ---------------------------------------------------------------------------
# 3. build_label_vector
# ---------------------------------------------------------------------------

class TestBuildLabelVector:
    """Test building the y label vector."""

    def test_temperature_label_shape(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        assert len(y) == 10
        assert len(valid_mask) == 10
        assert sum(valid_mask) < 10  # Some null temperatures

    def test_temperature_labels_are_float(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        valid_y = y[valid_mask]
        assert all(isinstance(v, float) for v in valid_y)

    def test_valid_mask_excludes_nulls(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        null_count = sum(1 for e in raw if e.get("transition_temperature_K") is None)
        assert sum(valid_mask) == len(raw) - null_count

    def test_classification_label_shape(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="classification")
        assert len(y) == 10
        assert len(valid_mask) == 10

    def test_classification_labels_are_strings(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="classification")
        assert all(isinstance(v, str) for v in y)

    def test_unknown_label_type_raises(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        with pytest.raises(ValueError, match="label_type"):
            build_label_vector(raw, label_type="unknown")

    def test_classification_valid_mask_all_true(self, sample_json_path: Path) -> None:
        """Classification should have all entries valid (no null phase labels)."""
        raw = load_experimental_data(sample_json_path)
        y, valid_mask = build_label_vector(raw, label_type="classification")
        assert valid_mask.all()


# ---------------------------------------------------------------------------
# 4. generate_data_quality_report
# ---------------------------------------------------------------------------

class TestDataQualityReport:
    """Test data quality report generation."""

    def test_report_is_dict(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert isinstance(report, dict)

    def test_report_has_n_samples(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert "n_samples" in report
        assert report["n_samples"] == 10

    def test_report_has_n_features(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert "n_features" in report
        assert report["n_features"] == 8

    def test_report_has_missing_values(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert "missing_values" in report
        assert isinstance(report["missing_values"], int)

    def test_report_has_feature_stats(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert "feature_statistics" in report
        assert isinstance(report["feature_statistics"], dict)
        for col in _FEATURE_COLUMNS:
            assert col in report["feature_statistics"]

    def test_report_has_outlier_info(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert "outliers" in report

    def test_report_no_severe_anomalies(self, sample_json_path: Path) -> None:
        raw = load_experimental_data(sample_json_path)
        X = build_feature_matrix(raw)
        y, valid_mask = build_label_vector(raw, label_type="temperature")
        report = generate_data_quality_report(X, y, valid_mask)
        assert report["missing_values"] == 0, "No NaN expected in feature matrix"


# ---------------------------------------------------------------------------
# 5. create_cv_indices (LOO-CV + optional stratified split)
# ---------------------------------------------------------------------------

class TestCreateCVIndices:
    """Test cross-validation index generation."""

    def test_loo_returns_n_folds(self) -> None:
        indices = create_cv_indices(n_samples=10, cv_strategy="loo")
        assert len(indices) == 10

    def test_loo_each_fold_exactly_one_test(self) -> None:
        indices = create_cv_indices(n_samples=10, cv_strategy="loo")
        for train_idx, test_idx in indices:
            assert len(test_idx) == 1
            assert len(train_idx) == 9

    def test_loo_reproducible_with_random_state(self) -> None:
        a = create_cv_indices(n_samples=10, cv_strategy="loo", random_state=42)
        b = create_cv_indices(n_samples=10, cv_strategy="loo", random_state=42)
        assert a == b

    def test_stratified_split_returns_three_tuples(self) -> None:
        indices = create_cv_indices(
            n_samples=100, cv_strategy="stratified", random_state=42
        )
        assert len(indices) == 3  # (train, val, test)

    def test_stratified_split_disjoint(self) -> None:
        train, val, test = create_cv_indices(
            n_samples=100, cv_strategy="stratified", random_state=42
        )
        train_set = set(train)
        val_set = set(val)
        test_set = set(test)
        assert len(train_set) == len(train)
        assert len(val_set) == len(val)
        assert len(test_set) == len(test)
        assert train_set.isdisjoint(val_set)
        assert train_set.isdisjoint(test_set)
        assert val_set.isdisjoint(test_set)

    def test_stratified_split_covers_all(self) -> None:
        train, val, test = create_cv_indices(
            n_samples=100, cv_strategy="stratified", random_state=42
        )
        assert set(train) | set(val) | set(test) == set(range(100))

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="cv_strategy"):
            create_cv_indices(n_samples=10, cv_strategy="invalid")


# ---------------------------------------------------------------------------
# 6. build_ml_dataset (end-to-end integration)
# ---------------------------------------------------------------------------

class TestBuildMLDataset:
    """Test end-to-end dataset construction."""

    def test_returns_tuple_x_y_mask(self, sample_json_path: Path) -> None:
        result = build_ml_dataset(sample_json_path)
        assert len(result) == 3
        X, y, valid_mask = result
        assert isinstance(X, pd.DataFrame)
        assert isinstance(y, np.ndarray)
        assert isinstance(valid_mask, np.ndarray)

    def test_shapes_consistent(self, sample_json_path: Path) -> None:
        X, y, valid_mask = build_ml_dataset(sample_json_path)
        assert X.shape[0] == len(y)
        assert len(y) == len(valid_mask)

    def test_real_dataset_55_samples(self, real_json_path: Path) -> None:
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        X, y, valid_mask = build_ml_dataset(real_json_path)
        assert X.shape == (55, 8)
        assert len(y) == 55

    def test_real_dataset_no_nan_features(self, real_json_path: Path) -> None:
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        X, y, valid_mask = build_ml_dataset(real_json_path)
        assert not X.isna().any().any()

    def test_acceptance_55_feature_computation(self, real_json_path: Path) -> None:
        """AC: 55 experimental data feature computation complete."""
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        X, y, valid_mask = build_ml_dataset(real_json_path)
        assert X.shape[0] == 55, "All 55 samples must have features computed"

    def test_acceptance_x_matrix_shape(self, real_json_path: Path) -> None:
        """AC: X matrix shape correct (n_samples, 8)."""
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        X, y, valid_mask = build_ml_dataset(real_json_path)
        assert X.shape == (55, 8), f"Expected (55, 8), got {X.shape}"

    def test_acceptance_reproducible_cv(self, real_json_path: Path) -> None:
        """AC: Train/val/test split is reproducible (random_state fixed)."""
        if not real_json_path.exists():
            pytest.skip("Real data not available")
        X, y, valid_mask = build_ml_dataset(real_json_path)
        n_valid = int(valid_mask.sum())
        loo_1 = create_cv_indices(n_samples=n_valid, cv_strategy="loo", random_state=42)
        loo_2 = create_cv_indices(n_samples=n_valid, cv_strategy="loo", random_state=42)
        assert loo_1 == loo_2, "LOO-CV must be reproducible with fixed random_state"
