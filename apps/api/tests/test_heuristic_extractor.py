"""Tests for heuristic regex/materials extractor (NFM-2050 demo fallback).

Covers: material detection, formula collapsing, numeric value parsing,
property matching, unit compatibility, spatial association, and the
public heuristic_extract() API including deduplication and element filtering.
"""

from __future__ import annotations

import pytest

from nfm_db.services.heuristic_extractor import (
    _collapse_spaced_formula,
    _collect_materials,
    _is_real_compound,
    _match_property,
    _nearest_material,
    _normalize_number,
    _units_compatible,
    heuristic_extract,
)


# ---------------------------------------------------------------------------
# _is_real_compound
# ---------------------------------------------------------------------------


class TestIsRealCompound:
    """Filter rule: accept real chemical formulas, reject prose/oxidation."""

    @pytest.mark.parametrize(
        "formula",
        ["UO2", "U3Si2", "Cr2O3", "Al2O3", "U-10Mo", "Zr-4", "U235", "Si28"],
    )
    def test_accepts_valid_materials(self, formula: str):
        assert _is_real_compound(formula) is True

    @pytest.mark.parametrize(
        "formula",
        ["Cr3", "O2", "Fe2", "Ni4", "Co2"],
    )
    def test_rejects_oxidation_notation(self, formula: str):
        """Single-element + single-digit is oxidation state, not material."""
        assert _is_real_compound(formula) is False

    @pytest.mark.parametrize(
        "formula",
        ["At", "In", "On", "To", "Is", "As", "It", "An"],
    )
    def test_rejects_false_friends(self, formula: str):
        """Common English words that match element-symbol grammar."""
        assert _is_real_compound(formula) is False

    def test_rejects_empty(self):
        assert _is_real_compound("") is False

    def test_rejects_unknown_symbols(self):
        assert _is_real_compound("Xx5Yy3") is False

    def test_rejects_overlong(self):
        assert _is_real_compound("U" * 31) is False


# ---------------------------------------------------------------------------
# _collapse_spaced_formula
# ---------------------------------------------------------------------------


class TestCollapseSpacedFormula:
    """PyMuPDF renders 'UO2' as 'UO 2' — collapse those."""

    def test_collapses_spaced_digit(self):
        assert _collapse_spaced_formula("UO 2") == "UO2"

    def test_collapses_spaced_fractional(self):
        assert _collapse_spaced_formula("UO 2.4") == "UO2.4"

    def test_collapses_multi_element(self):
        # Regex collapses last symbol+digit pair: "Si 2" -> "Si2"
        result = _collapse_spaced_formula("U3 Si 2")
        assert "Si2" in result

    def test_preserves_prose(self):
        """Shouldn't touch normal English."""
        result = _collapse_spaced_formula("for U")
        assert "U" in result  # no crash, text preserved


# ---------------------------------------------------------------------------
# _collect_materials
# ---------------------------------------------------------------------------


class TestCollectMaterials:
    def test_finds_uo2(self):
        result = _collect_materials("The compound UO2 is studied.")
        assert len(result) == 1
        assert result[0][2] == "UO2"

    def test_finds_multiple(self):
        text = "UO2 and U3Si2 are both fuels."
        result = _collect_materials(text)
        formulas = [r[2] for r in result]
        assert "UO2" in formulas
        assert "U3Si2" in formulas

    def test_excludes_false_friends(self):
        result = _collect_materials("It is on the table.")
        assert len(result) == 0

    def test_returns_positions(self):
        result = _collect_materials("The lattice of UO2 is 5.47 angstrom.")
        assert len(result) >= 1
        assert result[0][0] < result[0][1]  # start < end


# ---------------------------------------------------------------------------
# _normalize_number
# ---------------------------------------------------------------------------


class TestNormalizeNumber:
    def test_simple_int(self):
        assert _normalize_number("42") == 42.0

    def test_float(self):
        assert _normalize_number("5.47") == 5.47

    def test_with_comma(self):
        assert _normalize_number("1,234.56") == 1234.56

    def test_with_space(self):
        assert _normalize_number("1 234") == 1234.0

    def test_invalid(self):
        assert _normalize_number("abc") is None


# ---------------------------------------------------------------------------
# _match_property
# ---------------------------------------------------------------------------


class TestMatchProperty:
    def test_lattice_constant(self):
        text = "The lattice parameter a was measured."
        result = _match_property(text, len(text))
        assert result is not None
        assert result[0] == "lattice_constant"

    def test_density(self):
        text = "The density of the sample was high."
        result = _match_property(text, len(text))
        assert result is not None
        assert result[0] == "density"

    def test_no_match(self):
        text = "The color of the sky is blue."
        result = _match_property(text, len(text))
        assert result is None


# ---------------------------------------------------------------------------
# _units_compatible
# ---------------------------------------------------------------------------


class TestUnitsCompatible:
    def test_length_family_angstrom(self):
        assert _units_compatible("angstrom", "length") is True

    def test_length_family_wrong_unit(self):
        assert _units_compatible("GPa", "length") is False

    def test_unknown_family_always_compatible(self):
        assert _units_compatible("foo", "unknown_family") is True


# ---------------------------------------------------------------------------
# _nearest_material
# ---------------------------------------------------------------------------


class TestNearestMaterial:
    def test_picks_closest_before(self):
        mats = [(0, 3, "UO2"), (10, 14, "U3Si2")]
        result = _nearest_material(mats, 12)
        assert result == "U3Si2"

    def test_falls_back_forward(self):
        # Material at pos 0, idx=100, distance=100 < max_distance=400 → found
        mats = [(0, 3, "UO2")]
        result = _nearest_material(mats, 100)
        assert result == "UO2"

    def test_out_of_range(self):
        # Beyond max_distance=400 → None
        mats = [(0, 3, "UO2")]
        assert _nearest_material(mats, 500) is None

    def test_empty_materials(self):
        assert _nearest_material([], 0) is None


# ---------------------------------------------------------------------------
# heuristic_extract (public API)
# ---------------------------------------------------------------------------


class TestHeuristicExtract:
    def test_empty_content(self):
        assert heuristic_extract("", source_reference="test") == []

    def test_no_materials(self):
        result = heuristic_extract("Just plain text.", source_reference="test")
        assert result == []

    def test_extracts_density(self):
        text = "UO2 has a density of 10.97 g/cm3."
        result = heuristic_extract(text, source_reference="ref1")
        assert len(result) >= 1
        item = result[0]
        assert item["element_system"] == "UO2"
        assert item["property_name"] == "density"
        assert item["value"] == pytest.approx(10.97)
        assert item["unit"] == "g/cm3"
        assert item["method"] == "heuristic_regex"
        assert item["source"] == "ref1"

    def test_extracts_lattice_constant(self):
        text = "The lattice parameter of UO2 is 5.47 angstrom."
        result = heuristic_extract(text, source_reference="ref2")
        assert any(
            r["property_name"] == "lattice_constant" and r["element_system"] == "UO2"
            for r in result
        )

    def test_deduplication(self):
        text = "UO2 density 10.97 g/cm3. UO2 density 10.97 g/cm3."
        result = heuristic_extract(text, source_reference="ref3")
        values = [(r["element_system"], r["property_name"], r["value"]) for r in result]
        assert len(values) == len(set(values))  # no duplicates

    def test_element_filter(self):
        # Use U-10Mo (alloy form accepted by _is_real_compound) as filter target
        text = "UO2 density 10.97 g/cm3. U-10Mo density 17.2 g/cm3."
        result = heuristic_extract(
            text,
            source_reference="ref4",
            element_systems=["U-10Mo"],
        )
        assert len(result) >= 1
        assert all("U-10Mo" in r["element_system"] for r in result)

    def test_spaced_formula_extraction(self):
        """PyMuPDF 'UO 2' should be detected as UO2."""
        text = "UO 2 density 10.97 g/cm3."
        result = heuristic_extract(text, source_reference="ref5")
        assert any(r["element_system"] == "UO2" for r in result)

    def test_confidence_medium(self):
        text = "UO2 density 10.97 g/cm3."
        result = heuristic_extract(text, source_reference="ref6")
        assert all(r["confidence"] == "medium" for r in result)
