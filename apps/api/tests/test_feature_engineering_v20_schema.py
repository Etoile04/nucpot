"""Unit tests for feature_engineering v2.0 8D locked schema (NFM-1757 / NFM-1829).

Verifies that `compute_ml_features()` and `batch_compute_ml_features()`
produce the locked 8D pure physical feature vector that matches
`PHASE_CLASSIFIER_V2_FEATURE_NAMES` in `train_v20.py`. Cluster-fraction
features (cluster_I-IV) — the data-leakage source from NFM-1753 — must
NOT appear in the output.

References:
    - NFM-1753: RD-3 audit identifying cluster-type one-hot as leakage
    - NFM-1757: PhaseClassifier v2.0 retraining (8D locked schema)
    - NFM-1829: feature pipeline refactor (this test)
"""

from __future__ import annotations

import math

import pytest

from nfm_db.ml.feature_engineering import (
    ML_FEATURE_NAMES,
    batch_compute_ml_features,
    compute_ml_features,
)

# Canonical v2.0 locked schema — copied verbatim from PHASE_CLASSIFIER_V2_FEATURE_NAMES
# in train_v20.py (NFM-1757). Order matters: train/serve parity contract.
LOCKED_V2_SCHEMA: tuple[str, ...] = (
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
    "vec",
)

# Cluster-fraction feature names that must NEVER appear in the v2.0 schema.
LEAKY_CLUSTER_FEATURES: tuple[str, ...] = (
    "cluster_I",
    "cluster_II",
    "cluster_III",
    "cluster_IV",
)


class TestMLFeatureNamesSchema:
    """ML_FEATURE_NAMES must equal the locked 8D v2.0 schema."""

    def test_is_list_of_exactly_8(self) -> None:
        assert isinstance(ML_FEATURE_NAMES, list)
        assert len(ML_FEATURE_NAMES) == 8, (
            f"ML_FEATURE_NAMES must have 8 features (v2.0 locked), "
            f"got {len(ML_FEATURE_NAMES)}: {ML_FEATURE_NAMES}"
        )

    def test_exact_order_matches_locked_schema(self) -> None:
        """Order matters: train/serve parity contract per NFM-1757."""
        assert tuple(ML_FEATURE_NAMES) == LOCKED_V2_SCHEMA, (
            f"ML_FEATURE_NAMES drift from locked schema.\n"
            f"  Expected: {LOCKED_V2_SCHEMA}\n"
            f"  Got:      {tuple(ML_FEATURE_NAMES)}"
        )

    def test_no_cluster_fractions(self) -> None:
        for leaky in LEAKY_CLUSTER_FEATURES:
            assert leaky not in ML_FEATURE_NAMES, (
                f"{leaky} must NOT be in ML_FEATURE_NAMES — "
                f"cluster fractions are data-leakage sources per NFM-1753"
            )


class TestComputeMlFeatures:
    """compute_ml_features() must return the locked 8D schema."""

    @pytest.fixture
    def sample_composition(self) -> dict[str, float]:
        """Canonical U-Mo-Ti test composition (atomic fractions summing to 1.0)."""
        return {"U": 0.7, "Mo": 0.2, "Ti": 0.1}

    def test_returns_exactly_8_keys(self, sample_composition: dict[str, float]) -> None:
        result = compute_ml_features(sample_composition)
        assert isinstance(result, dict)
        assert len(result) == 8, f"Expected 8 features, got {len(result)}: {list(result.keys())}"

    def test_keys_match_locked_schema(self, sample_composition: dict[str, float]) -> None:
        result = compute_ml_features(sample_composition)
        assert set(result.keys()) == set(LOCKED_V2_SCHEMA)
        assert tuple(result.keys()) == LOCKED_V2_SCHEMA  # order check too

    def test_no_cluster_fractions_in_output(self, sample_composition: dict[str, float]) -> None:
        result = compute_ml_features(sample_composition)
        for leaky in LEAKY_CLUSTER_FEATURES:
            assert leaky not in result, f"{leaky} in compute_ml_features output — leakage!"

    def test_all_values_are_finite(self, sample_composition: dict[str, float]) -> None:
        result = compute_ml_features(sample_composition)
        for name, value in result.items():
            assert isinstance(value, float), f"{name} not float: {type(value)}"
            assert math.isfinite(value), f"{name} not finite: {value}"

    def test_pure_element_returns_zero_features(self) -> None:
        """Pure U composition: most features should be 0 or near-0."""
        result = compute_ml_features({"U": 1.0})
        assert len(result) == 8
        # mo_equivalent, mixing_enthalpy, lattice_distortion, config_entropy → 0
        assert result["mo_equivalent"] == 0.0
        assert result["mixing_enthalpy"] == 0.0
        assert result["lattice_distortion"] == 0.0
        assert result["config_entropy"] == 0.0

    @pytest.mark.parametrize(
        "composition",
        [
            {"U": 0.9, "Mo": 0.1},
            {"U": 0.7, "Mo": 0.2, "Ti": 0.1},
            {"U": 0.5, "Mo": 0.25, "Nb": 0.15, "Ti": 0.10},
            {"U": 90.0, "Mo": 8.0, "Ti": 2.0},  # at.% form
            {"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8},
        ],
    )
    def test_multiple_compositions(self, composition: dict[str, float]) -> None:
        result = compute_ml_features(composition)
        assert set(result.keys()) == set(LOCKED_V2_SCHEMA)
        assert all(math.isfinite(v) for v in result.values())


class TestBatchComputeMlFeatures:
    """batch_compute_ml_features() must produce a DataFrame with the locked schema."""

    def test_dataframe_columns_match_locked_schema(self) -> None:
        compositions = [
            {"U": 0.9, "Mo": 0.1},
            {"U": 0.7, "Mo": 0.2, "Ti": 0.1},
            {"U": 0.5, "Mo": 0.25, "Nb": 0.15, "Ti": 0.10},
        ]
        df = batch_compute_ml_features(compositions)

        assert list(df.columns) == list(LOCKED_V2_SCHEMA)
        assert len(df) == 3
        assert df.shape == (3, 8)

        # No cluster fractions
        for leaky in LEAKY_CLUSTER_FEATURES:
            assert leaky not in df.columns, f"{leaky} in DataFrame columns — leakage!"

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="compositions list must not be empty"):
            batch_compute_ml_features([])

    def test_all_values_finite(self) -> None:
        compositions = [
            {"U": 0.9, "Mo": 0.1},
            {"U": 1.0},
            {"U": 0.5, "Zr": 0.5},
        ]
        df = batch_compute_ml_features(compositions)
        # All numeric values should be finite
        assert not df.isna().any().any(), f"NaN in DataFrame:\n{df}"


class TestSchemaConsistency:
    """Cross-module consistency checks."""

    def test_compute_ml_features_keys_order_matches_ml_feature_names(self) -> None:
        """The output of compute_ml_features must follow ML_FEATURE_NAMES order
        (Python 3.7+ dict insertion order is guaranteed, so this is a smoke test)."""
        result = compute_ml_features({"U": 0.7, "Mo": 0.2, "Ti": 0.1})
        assert tuple(result.keys()) == tuple(ML_FEATURE_NAMES)

    def test_batch_dataframe_columns_match_ml_feature_names(self) -> None:
        df = batch_compute_ml_features([{"U": 0.9, "Mo": 0.1}])
        assert list(df.columns) == ML_FEATURE_NAMES
