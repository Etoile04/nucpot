"""Unit tests for physical feature engineering pipeline (NFM-1560).

Tests all 8 physical feature functions, compute_all_features aggregation,
and batch_compute DataFrame output. Covers edge cases, immutability, and
formula correctness per roadmap §4.1.2.
"""

from __future__ import annotations

import math

import pytest

from nfm_db.ml.feature_engineering import (
    ALLEN_ELECTRONEGATIVITY,
    ATOMIC_RADIUS,
    ATOMIC_VOLUME_CM3_PER_MOL,
    ATOMIC_WEIGHT,
    BULK_MODULUS,
    GAS_CONSTANT_R,
    MO_EQUIVALENT_COEFFICIENTS,
    PAULING_ELECTRONEGATIVITY,
    _MIEDEMA_LOOKUP,
    batch_compute,
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
    compute_all_features,
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
