"""Unit tests for EnergyPredictor v1.1 expanded feature pipeline (NFM-1806).

Tests cover the 22 new/expanded feature calculators in
feature_engineering.py Part 3 and the v1.1 training/inference path
in energy_predictor.py.
"""

from __future__ import annotations

import math

import pytest
import numpy as np

from nfm_db.ml.feature_engineering import (
    D_ELECTRON_COUNT,
    ENERGY_V11_FEATURE_NAMES,
    WORK_FUNCTION,
    _MIEDEMA_LOOKUP,
    batch_compute_energy_v11,
    calculate_allen_chi_mean,
    calculate_allen_chi_variance,
    calculate_atomic_volume_mean,
    calculate_bulk_modulus_mean,
    calculate_config_entropy,
    calculate_d_electron_mean,
    calculate_lattice_distortion,
    calculate_max_pair_enthalpy_abs,
 calculate_mean_pair_enthalpy_abs,
    calculate_min_pair_enthalpy,
    calculate_moequiv_squared,
    calculate_mo_equivalent,
    calculate_pair_enthalpy_range,
    calculate_pauling_chi_diff,
    calculate_pauling_chi_mean,
    calculate_size_factor,
    calculate_vec,
    calculate_work_function_mean,
    compute_energy_v11_features,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

U_MO = {"U": 0.9, "Mo": 0.1}
U_NB_ZR = {"U": 0.7, "Nb": 0.2, "Zr": 0.1}
PURE_U = {"U": 1.0}
TWO_ELEMENT = {"U": 0.5, "Zr": 0.5}


# ---------------------------------------------------------------------------
# Feature count and name consistency
# ---------------------------------------------------------------------------


class TestFeatureNames:
    """Energy v1.1 feature set should have exactly 22 named features."""

    def test_feature_count_is_22(self):
        assert len(ENERGY_V11_FEATURE_NAMES) == 22

    def test_feature_names_are_strings(self):
        for name in ENERGY_V11_FEATURE_NAMES:
            assert isinstance(name, str)
            assert name  # non-empty

    def test_feature_names_unique(self):
        assert len(set(ENERGY_V11_FEATURE_NAMES)) == len(ENERGY_V11_FEATURE_NAMES)

    def test_base_4_features_included(self):
        for name in ["mo_equivalent", "lattice_distortion", "allen_chi_diff", "vec"]:
            assert name in ENERGY_V11_FEATURE_NAMES

    def test_reused_5_features_included(self):
        for name in [
            "pauling_chi_diff", "config_entropy", "bv_ratio",
            "mixing_enthalpy", "u_density",
        ]:
            assert name in ENERGY_V11_FEATURE_NAMES

    def test_new_13_features_included(self):
        for name in [
            "pauling_chi_mean", "allen_chi_mean", "allen_chi_variance",
            "atomic_volume_mean", "bulk_modulus_mean", "d_electron_mean",
            "work_function_mean", "size_factor", "max_pair_enthalpy_abs",
            "min_pair_enthalpy", "pair_enthalpy_range",
            "mean_pair_enthalpy_abs", "moequiv_squared",
        ]:
            assert name in ENERGY_V11_FEATURE_NAMES



# ---------------------------------------------------------------------------
# New calculator tests
# ---------------------------------------------------------------------------


class TestPerElementDescriptors:
    """Per-element weighted descriptors for v1.1."""

    def test_pauling_chi_mean_binary(self):
        result = calculate_pauling_chi_mean(U_MO)
        assert isinstance(result, float)
        assert 1.0 < result < 2.0  # U=1.38, Mo=2.16

    def test_allen_chi_mean_binary(self):
        result = calculate_allen_chi_mean(U_MO)
        assert isinstance(result, float)
        assert 1.0 < result < 2.0  # U=1.226, Mo=1.885

    def test_allen_chi_variance_binary(self):
        result = calculate_allen_chi_variance(U_MO)
        assert isinstance(result, float)
        assert result >= 0.0  # variance is non-negative

    def test_allen_chi_variance_pure_element(self):
        result = calculate_allen_chi_variance(PURE_U)
        assert result == 0.0  # pure element has zero variance

    def test_allen_chi_variance_ternary(self):
        result = calculate_allen_chi_variance(U_NB_ZR)
        assert result >= 0.0

    def test_atomic_volume_mean(self):
        result = calculate_atomic_volume_mean(U_MO)
        assert isinstance(result, float)
        assert result > 0

    def test_bulk_modulus_mean(self):
        result = calculate_bulk_modulus_mean(U_MO)
        assert isinstance(result, float)
        assert result > 0

    def test_d_electron_mean_tm(self):
        result = calculate_d_electron_mean(U_MO)
        assert isinstance(result, float)
        # Mo has 5 d-electrons, U has 3 (actinide)
        assert 3.0 <= result <= 5.0

    def test_work_function_mean(self):
        result = calculate_work_function_mean(U_MO)
        assert isinstance(result, float)
        assert result > 0

    def test_unknown_element_skipped(self):
        # Element not in lookup tables should not crash
        comp = {"U": 0.8, "Mo": 0.1, "Xx": 0.1}
        for calc in [
            calculate_pauling_chi_mean, calculate_allen_chi_mean,
            calculate_atomic_volume_mean, calculate_bulk_modulus_mean,
            calculate_d_electron_mean, calculate_work_function_mean,
        ]:
            result = calc(comp)
            assert isinstance(result, (int, float))


# ---------------------------------------------------------------------------
# Pairwise interaction tests
# ---------------------------------------------------------------------------


class TestPairwiseFeatures:
    """Pairwise interaction features for v1.1."""

    def test_size_factor_u_mn(self):
        result = calculate_size_factor({"U": 0.9, "Mn": 0.1})
        assert isinstance(result, float)
        assert result > 0

    def test_size_factor_pure_u(self):
        assert calculate_size_factor(PURE_U) == 0.0

    def test_max_pair_enthalpy_abs_binary(self):
        result = calculate_max_pair_enthalpy_abs(U_MO)
        assert isinstance(result, float)
        assert result == 5.0  # |U-Mo| = 5.0

    def test_min_pair_enthalpy_binary(self):
        result = calculate_min_pair_enthalpy(U_MO)
        assert isinstance(result, float)
        assert result == -5.0  # U-Mo = -5.0 (most negative)

    def test_pair_enthalpy_range_binary(self):
        result = calculate_pair_enthalpy_range(U_MO)
        assert isinstance(result, float)
        assert result == 0.0  # binary has one pair; range = max - min = 0

    def test_mean_pair_enthalpy_abs_binary(self):
        result = calculate_mean_pair_enthalpy_abs(U_MO)
        assert isinstance(result, float)
        assert result == 5.0  # only one pair

    def test_pairwise_unknown_pair_ignored(self):
        comp = {"U": 0.8, "Mo": 0.1, "He": 0.1}
        result = calculate_max_pair_enthalpy_abs(comp)
        assert result == 5.0  # only U-Mo pair has Miedema data


    def test_pairwise_ternary(self):
        # U-Nb=-4, U-Zr=6, Nb-Zr=0 → max|Ω|=6, min=-4, range=10
        result = calculate_pair_enthalpy_range(U_NB_ZR)
        assert isinstance(result, float)
        assert result == 10.0


# ---------------------------------------------------------------------------
# Nonlinear feature tests
# ---------------------------------------------------------------------------


class TestNonlinearFeatures:
    """Nonlinear/interaction features."""

    def test_moequiv_squared(self):
        assert calculate_moequiv_squared(U_MO) == calculate_mo_equivalent(U_MO) ** 2

    def test_moequiv_squared_pure_u(self):
        assert calculate_moequiv_squared(PURE_U) == 0.0


# ---------------------------------------------------------------------------
# Full feature vector tests
# ---------------------------------------------------------------------------


class TestComputeEnergyV11Features:
    """compute_energy_v11_features integration tests."""

    def test_returns_22_features(self):
        result = compute_energy_v11_features(U_MO)
        assert len(result) == 22

    def test_keys_match_feature_names(self):
        result = compute_energy_v11_features(U_NB_ZR)
        assert list(result.keys()) == ENERGY_V11_FEATURE_NAMES

    def test_values_are_finite(self):
        result = compute_energy_v11_features(U_NB_ZR)
        for name, val in result.items():
            assert math.isfinite(val), f"{name}={val} is not finite"

    def test_pure_element_returns_features(self):
        result = compute_energy_v11_features(PURE_U)
        assert len(result) == 22
        assert result["mo_equivalent"] == 0.0
        assert result["lattice_distortion"] == 0.0
        assert result["config_entropy"] == 0.0

    def test_batch_compute(self):
        rows = batch_compute_energy_v11([U_MO, U_NB_ZR, PURE_U])
        assert rows.shape == (3, 22)
        assert list(rows.columns) == ENERGY_V11_FEATURE_NAMES

    def test_batch_compute_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            batch_compute_energy_v11([])

    def test_backward_compat_with_physical_features(self):
        """The 4 base features should produce same values as compute_all_features."""
        from nfm_db.ml.feature_engineering import compute_all_features

        base = compute_all_features(U_MO)
        v11 = compute_energy_v11_features(U_MO)
        for name in ["mo_equivalent", "lattice_distortion", "allen_chi_diff"]:
            assert abs(base[name] - v11[name]) < 1e-10, f"{name} mismatch"

    def test_vec_in_v11(self):
        """VEC should be present in v1.1 features."""
        v11 = compute_energy_v11_features(U_MO)
        assert abs(v11["vec"] - calculate_vec(U_MO)) < 1e-10
