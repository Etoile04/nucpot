"""Unit tests for Mo equivalent calculation (NFM-1523 pilot)."""

import pytest

from nfm_db.ml.feature_engineering import calculate_mo_equivalent


class TestCalculateMoEquivalent:
    """Tests for the Mo_eq formula: Mo_eq = 1.0*Mo + 1.13*Nb + 2.42*V + 1.86*Ti + 1.1*Zr."""

    def test_pure_uranium(self) -> None:
        """Pure U composition should yield Mo_eq = 0.0."""
        result = calculate_mo_equivalent({"U": 100.0})
        assert result == 0.0

    def test_u_10mo(self) -> None:
        """U-10Mo: Mo_eq = 1.0 × 10.0 = 10.0."""
        result = calculate_mo_equivalent({"U": 90.0, "Mo": 10.0})
        assert result == pytest.approx(10.0)

    def test_u88_2mo_composite(self) -> None:
        """U88.2Mo8.4Ti0.6V2.8: Mo_eq = 1.0×8.4 + 1.86×0.6 + 2.42×2.8 = 16.292."""
        composition = {"U": 88.2, "Mo": 8.4, "Ti": 0.6, "V": 2.8}
        result = calculate_mo_equivalent(composition)
        assert result == pytest.approx(16.292)

    def test_all_five_elements(self) -> None:
        """Composition with all 5 Mo-equiv elements present."""
        composition = {"Mo": 5.0, "Nb": 3.0, "V": 2.0, "Ti": 4.0, "Zr": 1.0}
        expected = 1.0 * 5.0 + 1.13 * 3.0 + 2.42 * 2.0 + 1.86 * 4.0 + 1.1 * 1.0
        result = calculate_mo_equivalent(composition)
        assert result == pytest.approx(expected)

    def test_empty_composition(self) -> None:
        """Empty dict should yield 0.0."""
        result = calculate_mo_equivalent({})
        assert result == 0.0

    def test_unknown_elements_ignored(self) -> None:
        """Elements not in the Mo-equiv formula should contribute 0."""
        result = calculate_mo_equivalent({"Fe": 50.0, "Cr": 25.0, "Ni": 25.0})
        assert result == 0.0
