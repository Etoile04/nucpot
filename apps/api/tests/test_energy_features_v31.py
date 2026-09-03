"""Unit tests for EnergyPredictor v3.1 aggregates-only features (NFM-3988).

Locks the PREREG §3-§4 contract: exactly 12 features, no pairwise/variance
stratum, no ``vec`` leakage. Mirrors the structure of test_energy_features_v11.py.
"""

from __future__ import annotations

import math

import pytest

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_features_v11,
)
from nfm_db.ml.energy_features_v31 import (
    ENERGY_V31_FEATURE_NAMES,
    V31_DROPPED_FEATURE_NAMES,
    compute_energy_features_v31,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def binary_comp() -> dict[str, float]:
    return {"U": 0.5, "Mo": 0.5}


@pytest.fixture
def ternary_comp() -> dict[str, float]:
    return {"U": 0.6, "Mo": 0.3, "Ti": 0.1}


@pytest.fixture
def single_element() -> dict[str, float]:
    return {"U": 1.0}


# ---------------------------------------------------------------------------
# Locked 12D contract — AC-A3
# ---------------------------------------------------------------------------


class TestFeatureNameRegistry:
    def test_total_count_is_12(self):
        assert len(ENERGY_V31_FEATURE_NAMES) == 12, (
            f"v3.1 PREREG §4 locks the stratum at 12D; got {len(ENERGY_V31_FEATURE_NAMES)}"
        )

    def test_no_duplicates(self):
        assert len(set(ENERGY_V31_FEATURE_NAMES)) == len(ENERGY_V31_FEATURE_NAMES)

    def test_no_dropped_feature_in_kept_list(self):
        leak = set(ENERGY_V31_FEATURE_NAMES) & V31_DROPPED_FEATURE_NAMES
        assert not leak, f"dropped feature leaked into v3.1 list: {leak}"

    def test_dropped_set_size(self):
        assert len(V31_DROPPED_FEATURE_NAMES) == 8

    def test_v31_is_subset_of_v11_kept_features(self):
        # The 7 Miedema aggregates + 5 element-level aggregates must all be
        # drawn from the v1.1 20D set (they exist in v1.1; we just subset).
        v11_set = set(ENERGY_V11_FEATURE_NAMES)
        for name in ENERGY_V31_FEATURE_NAMES:
            assert name in v11_set, (
                f"{name} is in v3.1 list but not in v1.1 20D sources"
            )


class TestFeatureOrderingLocked:
    """Literal order matters — the joblib artifact was trained in this order."""

    def test_first_seven_are_miedema_aggregates(self):
        expected = [
            "mo_equivalent",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        ]
        assert ENERGY_V31_FEATURE_NAMES[:7] == expected

    def test_last_five_are_element_level_aggregates(self):
        expected = [
            "avg_allen_chi",
            "avg_atomic_volume",
            "avg_d_electron",
            "avg_work_function",
            "avg_bulk_modulus",
        ]
        assert ENERGY_V31_FEATURE_NAMES[7:] == expected


# ---------------------------------------------------------------------------
# compute_energy_features_v31 — output shape and contract
# ---------------------------------------------------------------------------


class TestComputeFeatures:
    def test_empty_composition_returns_zeros(self):
        feat = compute_energy_features_v31({})
        assert len(feat) == 12
        assert all(v == 0.0 for v in feat.values())

    def test_keys_match_locked_list(self, binary_comp):
        feat = compute_energy_features_v31(binary_comp)
        assert set(feat.keys()) == set(ENERGY_V31_FEATURE_NAMES)

    def test_order_matches_locked_list(self, binary_comp):
        feat = compute_energy_features_v31(binary_comp)
        assert list(feat.keys()) == ENERGY_V31_FEATURE_NAMES

    def test_no_dropped_feature_in_output(self, binary_comp, ternary_comp):
        for comp in (binary_comp, ternary_comp, {"U": 1.0}):
            feat = compute_energy_features_v31(comp)
            leak = set(feat.keys()) & V31_DROPPED_FEATURE_NAMES
            assert not leak, f"dropped feature in output for {comp}: {leak}"

    def test_no_nans_or_infs(self, binary_comp, ternary_comp):
        for comp in (binary_comp, ternary_comp):
            feat = compute_energy_features_v31(comp)
            for k, v in feat.items():
                assert isinstance(v, float), f"{k} not float: {type(v)}"
                assert math.isfinite(v), f"{k} not finite: {v}"

    def test_single_element_does_not_raise(self, single_element):
        feat = compute_energy_features_v31(single_element)
        assert len(feat) == 12

    def test_v31_subset_of_v11_kept_features(self, binary_comp):
        """The 12 v3.1 features must agree with v1.1's same 12 keys."""
        v31 = compute_energy_features_v31(binary_comp)
        v11 = compute_energy_features_v11(binary_comp)
        for name in ENERGY_V31_FEATURE_NAMES:
            assert v31[name] == pytest.approx(v11[name], rel=1e-9), (
                f"{name}: v3.1={v31[name]} != v1.1={v11[name]}"
            )

    def test_values_pass_through_compute_ml_features(self, binary_comp):
        """7 Miedema aggregates must come from compute_ml_features verbatim."""
        from nfm_db.ml.feature_engineering import compute_ml_features
        base = compute_ml_features(binary_comp)
        v31 = compute_energy_features_v31(binary_comp)
        for name in (
            "mo_equivalent",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        ):
            assert v31[name] == pytest.approx(float(base[name] or 0.0), rel=1e-9)
