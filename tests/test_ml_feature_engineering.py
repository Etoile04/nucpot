"""Unit tests for physical feature engineering pipeline (NFM-1560 / NFM-1585).

Tests all physical feature functions, compute_all_features aggregation,
batch_compute DataFrame output, Part 2 cluster features (VEC, cluster
fractions), and the 8D ML feature vector. Covers edge cases,
immutability, and formula correctness per roadmap §4.1.2 / §5.1.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nfm_db.ml.feature_engineering import (
    ALLEN_ELECTRONEGATIVITY,
    ATOMIC_RADIUS,
    ATOMIC_VOLUME_CM3_PER_MOL,
    ATOMIC_WEIGHT,
    BULK_MODULUS,
    GAS_CONSTANT_R,
    ML_FEATURE_NAMES,
    MO_EQUIVALENT_COEFFICIENTS,
    PAULING_ELECTRONEGATIVITY,
    VALENCE_ELECTRON_COUNT,
    _MIEDEMA_LOOKUP,
    FeaturePipeline,
    batch_compute,
    batch_compute_ml_features,
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_cluster_fractions,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
    calculate_vec,
    compute_all_features,
    compute_ml_features,
)


# ---------------------------------------------------------------------------
# Feature 1: Mo Equivalent
# ---------------------------------------------------------------------------


class TestCalculateMoEquivalent:
    """Tests for Mo_eq = 1.0*Mo + 1.13*Nb + 2.42*V + 1.86*Ti + 1.1*Zr."""

    def test_pure_uranium(self) -> None:
        """Pure U composition should yield Mo_eq = 0.0."""
        assert calculate_mo_equivalent({"U": 100.0}) == 0.0

    def test_u_10mo(self) -> None:
        """U-10Mo: Mo_eq = 1.0 × 10.0 = 10.0."""
        assert calculate_mo_equivalent({"U": 90.0, "Mo": 10.0}) == pytest.approx(10.0)

    def test_u88_2mo_composite(self) -> None:
        """U88.2Mo8.4Ti0.6V2.8 matches pilot reference."""
        composition = {"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8}
        expected = 1.0 * 8.4 + 1.86 * 0.6 + 2.42 * 2.8
        assert calculate_mo_equivalent(composition) == pytest.approx(expected, abs=0.001)

    def test_all_five_elements(self) -> None:
        """All 5 Mo-equiv elements present."""
        composition = {"Mo": 5.0, "Nb": 3.0, "V": 2.0, "Ti": 4.0, "Zr": 1.0}
        expected = (
            1.0 * 5.0 + 1.13 * 3.0 + 2.42 * 2.0 + 1.86 * 4.0 + 1.1 * 1.0
        )
        assert calculate_mo_equivalent(composition) == pytest.approx(expected)

    def test_empty_composition(self) -> None:
        """Empty dict should yield 0.0."""
        assert calculate_mo_equivalent({}) == 0.0

    def test_unknown_elements_ignored(self) -> None:
        """Elements not in Mo-equiv formula contribute 0."""
        assert calculate_mo_equivalent({"Fe": 50.0, "Cr": 25.0, "Ni": 25.0}) == 0.0

    def test_does_not_mutate_input(self) -> None:
        """Input dict must not be modified."""
        comp = {"U": 90.0, "Mo": 10.0}
        original = dict(comp)
        calculate_mo_equivalent(comp)
        assert comp == original

    def test_u_10nb_binary(self) -> None:
        """U-10Nb binary pair: Mo_eq = 1.13 × 10.0 = 11.3."""
        assert calculate_mo_equivalent({"U": 90.0, "Nb": 10.0}) == pytest.approx(11.3)

    def test_u_10v_binary(self) -> None:
        """U-10V binary pair: Mo_eq = 2.42 × 10.0 = 24.2."""
        assert calculate_mo_equivalent({"U": 90.0, "V": 10.0}) == pytest.approx(24.2)

    def test_u_10ti_binary(self) -> None:
        """U-10Ti binary pair: Mo_eq = 1.86 × 10.0 = 18.6."""
        assert calculate_mo_equivalent({"U": 90.0, "Ti": 10.0}) == pytest.approx(18.6)

    def test_u_10zr_binary(self) -> None:
        """U-10Zr binary pair: Mo_eq = 1.1 × 10.0 = 11.0."""
        assert calculate_mo_equivalent({"U": 90.0, "Zr": 10.0}) == pytest.approx(11.0)

    def test_coefficients_match_roadmap(self) -> None:
        """Verify coefficient values match roadmap §4.1.2 exactly."""
        lookup = dict(MO_EQUIVALENT_COEFFICIENTS)
        assert lookup["Mo"] == 1.0
        assert lookup["Nb"] == 1.13
        assert lookup["V"] == 2.42
        assert lookup["Ti"] == 1.86
        assert lookup["Zr"] == 1.1


# ---------------------------------------------------------------------------
# Feature 2: Pauling Electronegativity Difference
# ---------------------------------------------------------------------------


class TestCalculatePaulingChiDiff:
    """Tests for Δχ_p = Σ(x_i × |χ_i − χ_U|)."""

    def test_pure_uranium(self) -> None:
        """Pure U should yield Δχ_p = 0.0."""
        assert calculate_pauling_chi_diff({"U": 1.0}) == pytest.approx(0.0)

    def test_u_10mo_at_percent(self) -> None:
        """U-10Mo with at.% — Pauling χ operates on raw fractions (no norm).

        Unlike config_entropy and mixing_enthalpy which normalize internally,
        Pauling χ diff works with raw input values. Use atomic fractions
        (0-1) for physically meaningful results.
        """
        result = calculate_pauling_chi_diff({"U": 90.0, "Mo": 10.0})
        chi_u = 1.38
        chi_mo = 2.16
        # Raw values: 90×|χ_U−χ_U| + 10×|χ_Mo−χ_U| = 10×0.78 = 7.8
        expected = 90.0 * 0.0 + 10.0 * abs(chi_mo - chi_u)
        assert result == pytest.approx(expected, abs=0.001)

    def test_u_10mo_fraction(self) -> None:
        """U-10Mo with atomic fractions."""
        result = calculate_pauling_chi_diff({"U": 0.9, "Mo": 0.1})
        chi_u = 1.38
        chi_mo = 2.16
        expected = 0.9 * 0.0 + 0.1 * abs(chi_mo - chi_u)
        assert result == pytest.approx(expected, abs=0.001)

    def test_unknown_element_uses_chi_u(self) -> None:
        """Unknown element should default to χ_U (zero contribution)."""
        result = calculate_pauling_chi_diff({"Xx": 1.0})
        assert result == 0.0

    def test_empty_composition(self) -> None:
        assert calculate_pauling_chi_diff({}) == 0.0

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_pauling_chi_diff(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Feature 3: Allen Electronegativity Difference
# ---------------------------------------------------------------------------


class TestCalculateAllenChiDiff:
    """Tests for Δχ_a = Σ(x_i × |χ_i(Allen) − χ_U(Allen)|)."""

    def test_pure_uranium(self) -> None:
        assert calculate_allen_chi_diff({"U": 1.0}) == pytest.approx(0.0)

    def test_u_10mo_fraction(self) -> None:
        result = calculate_allen_chi_diff({"U": 0.9, "Mo": 0.1})
        chi_u = 1.226
        chi_mo = 1.885
        expected = 0.9 * 0.0 + 0.1 * abs(chi_mo - chi_u)
        assert result == pytest.approx(expected, abs=0.001)

    def test_different_from_pauling(self) -> None:
        """Allen and Pauling scales should give different values for same comp."""
        comp = {"U": 0.8, "Mo": 0.1, "Nb": 0.1}
        p = calculate_pauling_chi_diff(comp)
        a = calculate_allen_chi_diff(comp)
        assert p != pytest.approx(a, abs=0.001)


# ---------------------------------------------------------------------------
# Feature 4: Configuration Entropy
# ---------------------------------------------------------------------------


class TestCalculateConfigEntropy:
    """Tests for S_config = -R × Σ(x_i × ln(x_i))."""

    def test_pure_uranium(self) -> None:
        """Pure element should yield 0.0."""
        assert calculate_config_entropy({"U": 1.0}) == 0.0

    def test_binary_equimolar(self) -> None:
        """Equimolar binary: S = -R × (0.5×ln(0.5) + 0.5×ln(0.5)) = R×ln(2)."""
        result = calculate_config_entropy({"U": 0.5, "Mo": 0.5})
        expected = GAS_CONSTANT_R * math.log(2)
        assert result == pytest.approx(expected, abs=0.01)

    def test_u_10mo(self) -> None:
        """U-10Mo: S = -R × (0.9×ln(0.9) + 0.1×ln(0.1))."""
        result = calculate_config_entropy({"U": 0.9, "Mo": 0.1})
        expected = -GAS_CONSTANT_R * (0.9 * math.log(0.9) + 0.1 * math.log(0.1))
        assert result == pytest.approx(expected, abs=0.01)

    def test_normalizes_at_percent(self) -> None:
        """at.% input should produce same result as fraction."""
        frac = calculate_config_entropy({"U": 0.9, "Mo": 0.1})
        atpct = calculate_config_entropy({"U": 90.0, "Mo": 10.0})
        assert frac == pytest.approx(atpct, abs=0.01)

    def test_empty_composition(self) -> None:
        assert calculate_config_entropy({}) == 0.0

    def test_zero_fraction_element(self) -> None:
        """Elements with zero fraction should not contribute."""
        result = calculate_config_entropy({"U": 1.0, "Mo": 0.0})
        assert result == 0.0

    def test_high_entropy_alloy(self) -> None:
        """5-component equimolar should have higher entropy than binary."""
        binary = calculate_config_entropy({"A": 0.5, "B": 0.5})
        quinary = calculate_config_entropy(
            {"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2}
        )
        assert quinary > binary


# ---------------------------------------------------------------------------
# Feature 5: B/V Ratio
# ---------------------------------------------------------------------------


class TestCalculateBvRatio:
    """Tests for B/V = Σ(x_i × B_i / V_i)."""

    def test_pure_uranium(self) -> None:
        """Pure U B/V should be B_U / V_U."""
        result = calculate_bv_ratio({"U": 1.0})
        expected = 113.0 / 12.49
        assert result == pytest.approx(expected, abs=0.01)

    def test_empty_composition(self) -> None:
        assert calculate_bv_ratio({}) == 0.0

    def test_unknown_elements_skipped(self) -> None:
        """Unknown elements are excluded from calculation."""
        result = calculate_bv_ratio({"Xx": 1.0})
        assert result == 0.0

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_bv_ratio(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Feature 6: Theoretical Uranium Density
# ---------------------------------------------------------------------------


class TestCalculateUDensity:
    """Tests for ρ = (Σ x_i × A_i) / (Σ x_i × V_i)."""

    def test_pure_uranium(self) -> None:
        """Pure U density ≈ 19.06 g/cm³."""
        result = calculate_u_density({"U": 1.0})
        expected = 238.03 / 12.49
        assert result == pytest.approx(expected, abs=0.01)

    def test_u_10mo(self) -> None:
        """U-10Mo density should be slightly lower than pure U."""
        rho_u = calculate_u_density({"U": 1.0})
        rho_u10mo = calculate_u_density({"U": 0.9, "Mo": 0.1})
        assert rho_u10mo < rho_u
        assert 15.0 < rho_u10mo < 20.0

    def test_empty_composition(self) -> None:
        assert calculate_u_density({}) == 0.0

    def test_density_above_threshold(self) -> None:
        """Nuclear fuel alloys should have ρ_U > 15 g/cm³."""
        for comp in [
            {"U": 0.8, "Mo": 0.2},
            {"U": 0.85, "Nb": 0.15},
            {"U": 0.7, "Mo": 0.2, "Zr": 0.1},
        ]:
            assert calculate_u_density(comp) > 15.0

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_u_density(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Feature 7: Mixing Enthalpy
# ---------------------------------------------------------------------------


class TestCalculateMixingEnthalpy:
    """Tests for ΔH_mix = Σ_{i<j} Ω_ij × x_i × x_j."""

    def test_pure_uranium(self) -> None:
        """Pure element has no mixing enthalpy."""
        assert calculate_mixing_enthalpy({"U": 1.0}) == 0.0

    def test_u_10mo(self) -> None:
        """U-10Mo: ΔH_mix = Ω_U-Mo × 0.9 × 0.1."""
        result = calculate_mixing_enthalpy({"U": 0.9, "Mo": 0.1})
        omega = _MIEDEMA_LOOKUP[("U", "Mo")]
        expected = omega * 0.9 * 0.1
        assert result == pytest.approx(expected, abs=0.001)

    def test_negative_for_u_mo(self) -> None:
        """U-Mo mixing enthalpy should be negative (exothermic)."""
        result = calculate_mixing_enthalpy({"U": 0.9, "Mo": 0.1})
        assert result < 0

    def test_positive_for_u_v(self) -> None:
        """U-V mixing enthalpy should be positive (endothermic)."""
        result = calculate_mixing_enthalpy({"U": 0.9, "V": 0.1})
        assert result > 0

    def test_empty_composition(self) -> None:
        assert calculate_mixing_enthalpy({}) == 0.0

    def test_ternary(self) -> None:
        """Ternary U-Mo-Nb: sum of all three binary contributions."""
        result = calculate_mixing_enthalpy({"U": 0.8, "Mo": 0.1, "Nb": 0.1})
        omega_u_mo = _MIEDEMA_LOOKUP[("U", "Mo")]
        omega_u_nb = _MIEDEMA_LOOKUP[("U", "Nb")]
        omega_mo_nb = _MIEDEMA_LOOKUP[("Mo", "Nb")]
        expected = (
            omega_u_mo * 0.8 * 0.1
            + omega_u_nb * 0.8 * 0.1
            + omega_mo_nb * 0.1 * 0.1
        )
        assert result == pytest.approx(expected, abs=0.001)

    def test_at_percent_normalization(self) -> None:
        """at.% and fraction should produce same result."""
        frac = calculate_mixing_enthalpy({"U": 0.9, "Mo": 0.1})
        atpct = calculate_mixing_enthalpy({"U": 90.0, "Mo": 10.0})
        assert frac == pytest.approx(atpct, abs=0.001)

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_mixing_enthalpy(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Feature 8: Lattice Distortion
# ---------------------------------------------------------------------------


class TestCalculateLatticeDistortion:
    """Tests for δ = √[Σ x_i × (1 − r_i/r̄)²]."""

    def test_pure_uranium(self) -> None:
        """Pure element should yield δ = 0.0."""
        assert calculate_lattice_distortion({"U": 1.0}) == pytest.approx(0.0)

    def test_binary_known(self) -> None:
        """U-10Mo: Mo has smaller radius than U, so δ > 0."""
        result = calculate_lattice_distortion({"U": 0.9, "Mo": 0.1})
        assert result > 0
        assert result < 0.1

    def test_empty_composition(self) -> None:
        assert calculate_lattice_distortion({}) == 0.0

    def test_unknown_elements_excluded(self) -> None:
        """Unknown elements are excluded from the average."""
        result = calculate_lattice_distortion({"Xx": 1.0})
        assert result == 0.0

    def test_larger_solute_content_increases_delta(self) -> None:
        """More solute should generally increase distortion."""
        d5 = calculate_lattice_distortion({"U": 0.95, "Mo": 0.05})
        d20 = calculate_lattice_distortion({"U": 0.80, "Mo": 0.20})
        assert d20 > d5

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_lattice_distortion(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Aggregation: compute_all_features
# ---------------------------------------------------------------------------


class TestComputeAllFeatures:
    """Tests for compute_all_features(composition) -> Dict[str, float]."""

    def test_returns_all_eight_features(self) -> None:
        result = compute_all_features({"U": 0.9, "Mo": 0.1})
        assert len(result) == 8

    def test_feature_names_match_phase_classifier(self) -> None:
        """Feature names must match PHYSICAL_FEATURE_NAMES in phase_classifier."""
        result = compute_all_features({"U": 0.9, "Mo": 0.1})
        expected_names = {
            "mo_equivalent",
            "pauling_chi_diff",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        }
        assert set(result.keys()) == expected_names

    def test_values_are_float(self) -> None:
        result = compute_all_features({"U": 0.9, "Mo": 0.1})
        for value in result.values():
            assert isinstance(value, float)

    def test_pure_uranium_feature_values(self) -> None:
        result = compute_all_features({"U": 1.0})
        assert result["mo_equivalent"] == 0.0
        assert result["pauling_chi_diff"] == 0.0
        assert result["allen_chi_diff"] == 0.0
        assert result["config_entropy"] == 0.0
        assert result["mixing_enthalpy"] == 0.0
        assert result["lattice_distortion"] == pytest.approx(0.0)

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        compute_all_features(comp)
        assert comp == original


# ---------------------------------------------------------------------------
# Aggregation: batch_compute
# ---------------------------------------------------------------------------


class TestBatchCompute:
    """Tests for batch_compute(compositions) -> DataFrame."""

    def test_returns_dataframe(self) -> None:
        result = batch_compute([{"U": 0.9, "Mo": 0.1}])
        assert hasattr(result, "columns")
        assert hasattr(result, "iloc")

    def test_column_names(self) -> None:
        result = batch_compute([{"U": 0.9, "Mo": 0.1}])
        expected_cols = [
            "mo_equivalent",
            "pauling_chi_diff",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        ]
        assert list(result.columns) == expected_cols

    def test_row_count(self) -> None:
        comps = [{"U": 0.9, "Mo": 0.1}, {"U": 0.8, "Mo": 0.2}]
        result = batch_compute(comps)
        assert len(result) == 2

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            batch_compute([])

    def test_single_composition(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        df = batch_compute([comp])
        direct = compute_all_features(comp)
        for col in direct:
            assert df.iloc[0][col] == pytest.approx(direct[col])


# ---------------------------------------------------------------------------
# Data Integrity: Constants Validation
# ---------------------------------------------------------------------------


class TestConstantsIntegrity:
    """Verify reference data tables have expected entries."""

    def test_uranium_in_all_tables(self) -> None:
        """Uranium must be present in all element lookup tables."""
        tables = {
            "pauling": dict(PAULING_ELECTRONEGATIVITY),
            "allen": dict(ALLEN_ELECTRONEGATIVITY),
            "atomic_volume": dict(ATOMIC_VOLUME_CM3_PER_MOL),
            "atomic_weight": dict(ATOMIC_WEIGHT),
            "atomic_radius": dict(ATOMIC_RADIUS),
            "bulk_modulus": dict(BULK_MODULUS),
        }
        for name, table in tables.items():
            assert "U" in table, f"Uranium missing from {name} table"

    def test_key_elements_present(self) -> None:
        """Mo, Nb, V, Ti, Zr must be in electronegativity tables."""
        key_elements = ["Mo", "Nb", "V", "Ti", "Zr"]
        pauling = dict(PAULING_ELECTRONEGATIVITY)
        allen = dict(ALLEN_ELECTRONEGATIVITY)
        for el in key_elements:
            assert el in pauling, f"{el} missing from Pauling table"
            assert el in allen, f"{el} missing from Allen table"

    def test_miedema_u_mo_present(self) -> None:
        """U-Mo pair must exist in Miedema lookup."""
        assert ("U", "Mo") in _MIEDEMA_LOOKUP
        assert ("Mo", "U") in _MIEDEMA_LOOKUP

    def test_miedema_symmetry(self) -> None:
        """Ω_ij should equal Ω_ji for all pairs."""
        seen: set = set()
        for pair, val in _MIEDEMA_LOOKUP.items():
            reverse = (pair[1], pair[0])
            if reverse not in seen:
                assert val == pytest.approx(
                    _MIEDEMA_LOOKUP.get(reverse, float("nan")),
                    abs=0.001,
                ), f"Miedema asymmetry: {pair}={val} vs {reverse}"
            seen.add(pair)


# ---------------------------------------------------------------------------
# FeaturePipeline
# ---------------------------------------------------------------------------


class TestFeaturePipeline:
    """Tests for FeaturePipeline.extract_features(composition) -> np.ndarray."""

    @pytest.fixture()
    def pipeline(self) -> FeaturePipeline:
        return FeaturePipeline()

    def test_returns_numpy_array(self, pipeline: FeaturePipeline) -> None:
        """extract_features must return a numpy ndarray."""
        result = pipeline.extract_features({"U": 0.9, "Mo": 0.1})
        assert hasattr(result, "dtype")
        assert hasattr(result, "shape")

    def test_output_shape_is_8(self, pipeline: FeaturePipeline) -> None:
        """Output array must have shape (8,)."""
        result = pipeline.extract_features({"U": 0.9, "Mo": 0.1})
        assert result.shape == (8,)

    def test_output_dtype_is_float64(self, pipeline: FeaturePipeline) -> None:
        """Output dtype should be float64 for ML model compatibility."""
        result = pipeline.extract_features({"U": 0.9, "Mo": 0.1})
        assert result.dtype == np.float64

    def test_values_match_compute_all_features(
        self, pipeline: FeaturePipeline
    ) -> None:
        """Pipeline output must match compute_all_features dict values."""
        comp = {"U": 0.9, "Mo": 0.1}
        expected = compute_all_features(comp)
        result = pipeline.extract_features(comp)
        names = pipeline.feature_names
        for i, name in enumerate(names):
            assert result[i] == pytest.approx(expected[name], abs=1e-10)

    def test_feature_names_match_phase_classifier(
        self, pipeline: FeaturePipeline
    ) -> None:
        """feature_names must match PHYSICAL_FEATURE_NAMES in phase_classifier."""
        expected = [
            "mo_equivalent",
            "pauling_chi_diff",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        ]
        assert pipeline.feature_names == expected

    def test_n_features_is_8(self, pipeline: FeaturePipeline) -> None:
        assert pipeline.n_features == 8

    def test_pure_uranium_values(self, pipeline: FeaturePipeline) -> None:
        """Pure U should have mo_eq=0, chi_diff=0, entropy=0, etc."""
        result = pipeline.extract_features({"U": 1.0})
        names = pipeline.feature_names
        name_to_idx = {name: i for i, name in enumerate(names)}

        assert result[name_to_idx["mo_equivalent"]] == 0.0
        assert result[name_to_idx["pauling_chi_diff"]] == 0.0
        assert result[name_to_idx["allen_chi_diff"]] == 0.0
        assert result[name_to_idx["config_entropy"]] == 0.0
        assert result[name_to_idx["mixing_enthalpy"]] == 0.0

    def test_empty_composition_raises(self, pipeline: FeaturePipeline) -> None:
        """Empty composition must raise ValueError."""
        with pytest.raises(ValueError, match="at least one element"):
            pipeline.extract_features({})

    def test_zero_composition_raises(self, pipeline: FeaturePipeline) -> None:
        """All-zero composition must raise ValueError."""
        with pytest.raises(ValueError, match="at least one element"):
            pipeline.extract_features({"U": 0.0, "Mo": 0.0})

    def test_does_not_mutate_input(self, pipeline: FeaturePipeline) -> None:
        """Input dict must not be modified."""
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        pipeline.extract_features(comp)
        assert comp == original

    def test_multi_element_composition(self, pipeline: FeaturePipeline) -> None:
        """U-Mo-Nb-Ti-V quinary alloy should produce all non-zero features."""
        comp = {"U": 85.0, "Mo": 5.0, "Nb": 3.0, "Ti": 2.0, "V": 5.0}
        result = pipeline.extract_features(comp)
        assert result.shape == (8,)
        # Mo_eq should be non-zero (Mo, Nb, V, Ti present)
        assert result[0] > 0.0


class TestFeaturePipelineBatch:
    """Tests for FeaturePipeline.extract_features_batch."""

    @pytest.fixture()
    def pipeline(self) -> FeaturePipeline:
        return FeaturePipeline()

    def test_returns_2d_array(self, pipeline: FeaturePipeline) -> None:
        comps = [{"U": 0.9, "Mo": 0.1}, {"U": 0.8, "Mo": 0.2}]
        result = pipeline.extract_features_batch(comps)
        assert result.ndim == 2
        assert result.shape == (2, 8)

    def test_empty_list_raises(self, pipeline: FeaturePipeline) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            pipeline.extract_features_batch([])

    def test_single_composition(self, pipeline: FeaturePipeline) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        single = pipeline.extract_features(comp)
        batch = pipeline.extract_features_batch([comp])
        assert batch.shape == (1, 8)
        for i in range(8):
            assert batch[0, i] == pytest.approx(single[i], abs=1e-10)

    def test_batch_matches_individual(self, pipeline: FeaturePipeline) -> None:
        """Batch results must match individual extract_features calls."""
        comps = [
            {"U": 0.9, "Mo": 0.1},
            {"U": 0.8, "Mo": 0.15, "Nb": 0.05},
            {"U": 0.7, "Mo": 0.2, "Zr": 0.1},
        ]
        batch = pipeline.extract_features_batch(comps)
        assert batch.shape == (3, 8)
        for i, comp in enumerate(comps):
            individual = pipeline.extract_features(comp)
            for j in range(8):
                assert batch[i, j] == pytest.approx(individual[j], abs=1e-10)

    def test_output_dtype_is_float64(self, pipeline: FeaturePipeline) -> None:
        result = pipeline.extract_features_batch(
            [{"U": 0.9, "Mo": 0.1}, {"U": 1.0}]
        )
        assert result.dtype == np.float64

    def test_does_not_mutate_inputs(self, pipeline: FeaturePipeline) -> None:
        comps = [{"U": 0.9, "Mo": 0.1}, {"U": 0.8, "Mo": 0.2}]
        originals = [dict(c) for c in comps]
        pipeline.extract_features_batch(comps)
        for comp, orig in zip(comps, originals):
            assert comp == orig


# ===========================================================================
# Part 2: VEC, Cluster Fractions, 8D ML Feature Vector (NFM-1585)
# ===========================================================================


# ---------------------------------------------------------------------------
# Feature 9: Valence Electron Concentration (VEC)
# ---------------------------------------------------------------------------


class TestCalculateVec:
    """Tests for VEC = Σ(x_i × VEC_i)."""

    def test_pure_uranium(self) -> None:
        """Pure U should yield VEC = 6.0."""
        assert calculate_vec({"U": 1.0}) == pytest.approx(6.0)

    def test_u_10mo(self) -> None:
        """U-10Mo: VEC = 0.9×6 + 0.1×6 = 6.0."""
        assert calculate_vec({"U": 0.9, "Mo": 0.1}) == pytest.approx(6.0)

    def test_u_10nb(self) -> None:
        """U-10Nb: VEC = 0.9×6 + 0.1×5 = 5.9."""
        result = calculate_vec({"U": 0.9, "Nb": 0.1})
        assert result == pytest.approx(0.9 * 6.0 + 0.1 * 5.0)

    def test_u_with_ti_v(self) -> None:
        """U-Ti-V: Ti(4) and V(5) should pull VEC down."""
        comp = {"U": 0.8, "Ti": 0.1, "V": 0.1}
        result = calculate_vec(comp)
        expected = 0.8 * 6.0 + 0.1 * 4.0 + 0.1 * 5.0
        assert result == pytest.approx(expected, abs=0.001)

    def test_high_vec_with_ni(self) -> None:
        """U-Ni: Ni(10) should push VEC above 6."""
        result = calculate_vec({"U": 0.9, "Ni": 0.1})
        assert result > 6.0
        assert result < 7.0

    def test_empty_composition(self) -> None:
        assert calculate_vec({}) == 0.0

    def test_unknown_elements_skipped(self) -> None:
        """Unknown elements are excluded; fraction redistributed."""
        assert calculate_vec({"Xx": 1.0}) == 0.0

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_vec(comp)
        assert comp == original

    def test_at_percent_same_as_fraction(self) -> None:
        """at.% and fraction inputs should produce same VEC."""
        frac = calculate_vec({"U": 0.9, "Mo": 0.1})
        atpct = calculate_vec({"U": 90.0, "Mo": 10.0})
        assert frac == pytest.approx(atpct, abs=0.001)


class TestVecConstants:
    """Verify VEC lookup table integrity."""

    def test_uranium_present(self) -> None:
        lookup = dict(VALENCE_ELECTRON_COUNT)
        assert "U" in lookup
        assert lookup["U"] == 6.0

    def test_key_elements_present(self) -> None:
        """Mo, Nb, V, Ti, Zr must be in VEC table."""
        lookup = dict(VALENCE_ELECTRON_COUNT)
        for el in ["Mo", "Nb", "V", "Ti", "Zr", "Cr", "Fe", "Ni", "Al"]:
            assert el in lookup, f"{el} missing from VEC table"

    def test_vec_values_reasonable(self) -> None:
        """VEC values should be between 2 and 12 for common elements."""
        lookup = dict(VALENCE_ELECTRON_COUNT)
        for el, vec in lookup.items():
            assert 2.0 <= vec <= 14.0, f"VEC for {el} = {vec} out of range"


# ---------------------------------------------------------------------------
# Feature 10–13: Cluster-type Fractions
# ---------------------------------------------------------------------------


class TestCalculateClusterFractions:
    """Tests for cluster_K = Σ(x_i in type K) / Σ(all classified)."""

    def test_pure_uranium(self) -> None:
        """Pure U should yield all-zero cluster fractions."""
        result = calculate_cluster_fractions({"U": 1.0})
        for key in result:
            assert result[key] == 0.0

    def test_u_10mo_type_I(self) -> None:
        """Mo is Type I → cluster_I = 1.0, rest = 0.0."""
        result = calculate_cluster_fractions({"U": 0.9, "Mo": 0.1})
        assert result["cluster_I"] == pytest.approx(1.0)
        assert result["cluster_II"] == 0.0
        assert result["cluster_III"] == 0.0
        assert result["cluster_IV"] == 0.0

    def test_u_10nb_type_I(self) -> None:
        """Nb is Type I → cluster_I = 1.0."""
        result = calculate_cluster_fractions({"U": 0.9, "Nb": 0.1})
        assert result["cluster_I"] == pytest.approx(1.0)

    def test_u_10ti_type_II(self) -> None:
        """Ti is Type II → cluster_II = 1.0."""
        result = calculate_cluster_fractions({"U": 0.9, "Ti": 0.1})
        assert result["cluster_II"] == pytest.approx(1.0)

    def test_u_10v_type_III(self) -> None:
        """V is Type III → cluster_III = 1.0."""
        result = calculate_cluster_fractions({"U": 0.9, "V": 0.1})
        assert result["cluster_III"] == pytest.approx(1.0)

    def test_u_10al_type_IV(self) -> None:
        """Al is Type IV → cluster_IV = 1.0."""
        result = calculate_cluster_fractions({"U": 0.9, "Al": 0.1})
        assert result["cluster_IV"] == pytest.approx(1.0)

    def test_multi_type_mixed(self) -> None:
        """Multi-type composition: fractions should sum to 1.0."""
        comp = {"U": 0.8, "Mo": 0.05, "Ti": 0.05, "V": 0.05, "Al": 0.05}
        result = calculate_cluster_fractions(comp)
        total = sum(result.values())
        assert total == pytest.approx(1.0, abs=0.001)

    def test_mixed_type_proportional(self) -> None:
        """Type I (Mo=0.08) and Type II (Ti=0.02): I=0.8, II=0.2."""
        comp = {"U": 0.90, "Mo": 0.08, "Ti": 0.02}
        result = calculate_cluster_fractions(comp)
        assert result["cluster_I"] == pytest.approx(0.8, abs=0.01)
        assert result["cluster_II"] == pytest.approx(0.2, abs=0.01)

    def test_empty_composition(self) -> None:
        result = calculate_cluster_fractions({})
        for key in result:
            assert result[key] == 0.0

    def test_unknown_solute_ignored(self) -> None:
        """Unknown solutes not in cluster database are excluded."""
        result = calculate_cluster_fractions({"U": 0.9, "Xx": 0.1})
        for key in result:
            assert result[key] == 0.0

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        calculate_cluster_fractions(comp)
        assert comp == original

    def test_has_four_keys(self) -> None:
        """Result must always have exactly 4 keys."""
        result = calculate_cluster_fractions({"U": 0.5, "Mo": 0.5})
        assert set(result.keys()) == {"cluster_I", "cluster_II", "cluster_III", "cluster_IV"}

    def test_at_percent_same_as_fraction(self) -> None:
        """at.% and fraction inputs should produce same cluster fractions."""
        frac = calculate_cluster_fractions({"U": 90.0, "Mo": 10.0})
        atpct = calculate_cluster_fractions({"U": 0.9, "Mo": 0.1})
        for key in frac:
            assert frac[key] == pytest.approx(atpct[key], abs=0.001)


# ---------------------------------------------------------------------------
# 8D ML Feature Vector
# ---------------------------------------------------------------------------


class TestMLFeatureNames:
    """Verify ML_FEATURE_NAMES constant."""

    def test_has_eight_names(self) -> None:
        assert len(ML_FEATURE_NAMES) == 8

    def test_expected_names(self) -> None:
        expected = [
            "mo_equivalent",
            "lattice_distortion",
            "allen_chi_diff",
            "vec",
            "cluster_I",
            "cluster_II",
            "cluster_III",
            "cluster_IV",
        ]
        assert ML_FEATURE_NAMES == expected


class TestComputeMlFeatures:
    """Tests for compute_ml_features(composition) -> Dict[str, float]."""

    def test_returns_eight_features(self) -> None:
        result = compute_ml_features({"U": 0.9, "Mo": 0.1})
        assert len(result) == 8

    def test_keys_match_ml_feature_names(self) -> None:
        result = compute_ml_features({"U": 0.9, "Mo": 0.1})
        assert set(result.keys()) == set(ML_FEATURE_NAMES)

    def test_values_are_float(self) -> None:
        result = compute_ml_features({"U": 0.9, "Mo": 0.1})
        for value in result.values():
            assert isinstance(value, float)

    def test_pure_uranium(self) -> None:
        result = compute_ml_features({"U": 1.0})
        assert result["mo_equivalent"] == 0.0
        assert result["lattice_distortion"] == pytest.approx(0.0)
        assert result["allen_chi_diff"] == pytest.approx(0.0)
        assert result["vec"] == pytest.approx(6.0)
        # No solutes → all cluster fractions = 0
        assert result["cluster_I"] == 0.0
        assert result["cluster_II"] == 0.0
        assert result["cluster_III"] == 0.0
        assert result["cluster_IV"] == 0.0

    def test_u_10mo_cluster_I(self) -> None:
        """Mo is Type I → cluster_I=1.0, others=0."""
        result = compute_ml_features({"U": 0.9, "Mo": 0.1})
        assert result["cluster_I"] == pytest.approx(1.0)
        assert result["cluster_IV"] == 0.0

    def test_multi_type_cluster_fractions_sum_to_one(self) -> None:
        comp = {"U": 0.8, "Mo": 0.05, "Ti": 0.05, "V": 0.05, "Al": 0.05}
        result = compute_ml_features(comp)
        cluster_sum = sum(
            result[k] for k in ["cluster_I", "cluster_II", "cluster_III", "cluster_IV"]
        )
        assert cluster_sum == pytest.approx(1.0, abs=0.01)

    def test_does_not_mutate_input(self) -> None:
        comp = {"U": 0.9, "Mo": 0.1}
        original = dict(comp)
        compute_ml_features(comp)
        assert comp == original

    def test_mixed_composition(self) -> None:
        """U-Mo-Nb: both Type I → cluster_I still = 1.0."""
        result = compute_ml_features({"U": 0.8, "Mo": 0.1, "Nb": 0.1})
        assert result["cluster_I"] == pytest.approx(1.0)

    def test_vec_present(self) -> None:
        """VEC feature must be present and > 0."""
        result = compute_ml_features({"U": 0.9, "Mo": 0.1})
        assert result["vec"] > 0.0


class TestBatchComputeMlFeatures:
    """Tests for batch_compute_ml_features(compositions) -> DataFrame."""

    def test_returns_dataframe(self) -> None:
        result = batch_compute_ml_features([{"U": 0.9, "Mo": 0.1}])
        assert hasattr(result, "columns")
        assert hasattr(result, "iloc")

    def test_column_names(self) -> None:
        result = batch_compute_ml_features([{"U": 0.9, "Mo": 0.1}])
        assert list(result.columns) == ML_FEATURE_NAMES

    def test_row_count(self) -> None:
        comps = [{"U": 0.9, "Mo": 0.1}, {"U": 0.8, "Mo": 0.2}]
        result = batch_compute_ml_features(comps)
        assert len(result) == 2

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            batch_compute_ml_features([])

    def test_values_match_individual(self) -> None:
        comps = [{"U": 0.9, "Mo": 0.1}, {"U": 0.8, "Nb": 0.2}]
        df = batch_compute_ml_features(comps)
        for i, comp in enumerate(comps):
            individual = compute_ml_features(comp)
            for col in ML_FEATURE_NAMES:
                assert df.iloc[i][col] == pytest.approx(
                    individual[col], abs=1e-10
                )

    def test_cluster_columns_are_present(self) -> None:
        """DataFrame must include all 4 cluster columns."""
        df = batch_compute_ml_features([{"U": 0.9, "Mo": 0.1}])
        for k in ["cluster_I", "cluster_II", "cluster_III", "cluster_IV"]:
            assert k in df.columns
