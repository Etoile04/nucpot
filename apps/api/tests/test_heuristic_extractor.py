"""Tests for heuristic regex/materials extractor (NFM-2050 demo fallback).

Covers: material detection, formula collapsing, numeric value parsing,
property matching, unit compatibility, spatial association, and the
public heuristic_extract() API including deduplication and element filtering.

NFM-3517 [NFM-3424-A]: F8 scorecard coverage — adds 6 new pattern classes
(Cr-doped Ea/D0, density values, RDF peaks, Cr-O bond length) on the
Owen2023 paper fixture.
"""

from __future__ import annotations

from pathlib import Path

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

# NFM-3517: fixture path for the Owen2023 paper sample text.
OWEN2023_FIXTURE = (
    Path(__file__).parent / "fixtures" / "extraction" / "owen2023_sample.txt"
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

    def test_emits_material_name_and_composition_nfm_3919(self):
        """NFM-3919: heuristic items must carry non-empty material_name and
        composition so the mapper's dedup can reuse the existing Material
        row instead of creating a new "Unknown Material" every run.
        """
        text = "UO2 has a density of 10.97 g/cm3."
        result = heuristic_extract(text, source_reference="ref-nfm-3919")
        assert len(result) >= 1
        item = result[0]
        # Both fields must be populated and non-empty.
        assert item.get("material_name"), (
            "heuristic_extractor must set material_name so the mapper does not "
            "fall back to 'Unknown Material' (NFM-3919)."
        )
        assert item.get("composition"), (
            "heuristic_extractor must set composition so _find_material_by_formula "
            "can dedup across runs (NFM-3919)."
        )
        assert item["material_name"] == "UO2"
        assert item["composition"] == "UO2"
        # Backward compatibility: element_system remains for existing consumers.
        assert item["element_system"] == "UO2"

    def test_dft_pass_also_emits_material_name_and_composition_nfm_3919(self):
        """NFM-3919: the DFT-direct second pass must also populate the
        material_name/composition fields, not just element_system.
        """
        # Use a dimensionless screening_constant DFT result via _DFT_DIRECT_PATTERNS.
        # Pull a representative text that triggers the dimensionless family.
        text = (
            "The Thomas-Fermi screening constant of UO2 is 1.8 "
            "in atomic units."
        )
        result = heuristic_extract(text, source_reference="ref-dft-nfm-3919")
        assert len(result) >= 1, "DFT-direct pass should produce an item"
        for item in result:
            assert item.get("material_name"), (
                "DFT-direct pass must set material_name (NFM-3919)"
            )
            assert item.get("composition"), (
                "DFT-direct pass must set composition (NFM-3919)"
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


# ---------------------------------------------------------------------------
# NFM-3517 [NFM-3424-A]: F8 scorecard coverage — 6 missing pattern classes.
# Owen2023 paper text fixture (amorphous + Cr-doped UO2 diffusion study).
# Existing 2/8 PASS checkpoints (undoped Ea 0.30 eV, undoped D0 3.32e-8
# cm²/s) MUST continue to pass — see AC-A5.
# ---------------------------------------------------------------------------


def _owen2023_text() -> str:
    """Load the Owen2023 paper sample text from the test fixture."""
    return OWEN2023_FIXTURE.read_text(encoding="utf-8")


def _values_by_names(
    results: list[dict], property_names: tuple[str, ...]
) -> list[float]:
    """Return numeric values for rows whose property_name is in the set.

    Some F8 checkpoints can land under either of two related property
    names — e.g. D0 may be emitted as ``diffusion_coefficient`` or as
    ``pre_exponential_factor`` depending on the surrounding prose
    ("diffusion coefficient" vs "pre-exponential factor"). This helper
    accepts any of the names so tests assert on the physical quantity,
    not on the lexical surface form.
    """
    return [
        r["value"]
        for r in results
        if r["property_name"] in property_names
    ]


class TestF8ExistingPassCheckpoints:
    """Regression guard: AC-A5 — do NOT regress the existing 2/8 PASS.

    These two rows must still appear in the Owen2023 extraction. If
    either test fails, the additive change has unintentionally regressed
    the undoped baseline.
    """

    def test_undoped_ea_0p30_still_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        eV_values = _values_by_names(result, ("activation_energy",))
        assert any(v == pytest.approx(0.30) for v in eV_values), (
            f"undoped Ea=0.30 eV must remain; got {eV_values}"
        )

    def test_undoped_d0_3p32e_minus_8_still_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        # D0 may surface as either ``diffusion_coefficient`` or
        # ``pre_exponential_factor`` depending on the surrounding prose.
        D0_values = _values_by_names(
            result, ("diffusion_coefficient", "pre_exponential_factor")
        )
        assert any(v == pytest.approx(3.32e-8, rel=1e-3) for v in D0_values), (
            f"undoped D0=3.32e-8 cm²/s must remain; got {D0_values}"
        )


class TestF8CrDopedActivationEnergy:
    """AC-A1 class #1: Cr-doped Ea (target ~0.26±0.08 eV at 50 at% Cr).

    The activation_energy rule already exists. This test asserts the
    0.26 eV value lands somewhere in the extraction on Owen2023 text.
    """

    def test_cr_doped_ea_0p26_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        eV_values = _values_by_names(result, ("activation_energy",))
        assert any(v == pytest.approx(0.26) for v in eV_values), (
            f"Cr-doped Ea=0.26 eV must land; got {eV_values}"
        )


class TestF8CrDopedDiffusivity:
    """AC-A1 class #2: Cr-doped D0 (target ~1.27e-9 cm²/s at 50 at% Cr)."""

    def test_cr_doped_d0_1p27e_minus_9_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        D0_values = _values_by_names(
            result, ("diffusion_coefficient", "pre_exponential_factor")
        )
        assert any(v == pytest.approx(1.27e-9, rel=1e-3) for v in D0_values), (
            f"Cr-doped D0=1.27e-9 cm²/s must land; got {D0_values}"
        )


class TestF8DensityAmorphous:
    """AC-A1 class #3: Density of amorphous UO2 (target ~10.55 g/cm³)."""

    def test_amorphous_density_10p55_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        density_values = _values_by_names(result, ("density",))
        assert any(v == pytest.approx(10.55) for v in density_values), (
            f"amorphous density=10.55 g/cm³ must land; got {density_values}"
        )


class TestF8DensityCrDoped:
    """AC-A1 class #4: Density of 10 at% Cr-doped UO2 (target ~10.27 g/cm³)."""

    def test_cr_doped_density_10p27_extracted(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        density_values = _values_by_names(result, ("density",))
        assert any(v == pytest.approx(10.27) for v in density_values), (
            f"Cr-doped density=10.27 g/cm³ must land; got {density_values}"
        )


class TestRdfPeakPattern:
    """AC-A1 class #5: NEW property type — RDF peaks (target 2.28 Å, 2.83 Å).

    No existing _PROPERTY_RULES entry matches 'RDF peak' / 'radial
    distribution function'. Implementation must add a new rule mapping
    to property_name='rdf_peak' with family='length'.
    """

    def test_rdf_peak_matches_property_name(self):
        result = _match_property(
            "The radial distribution function peak at", len(
                "The radial distribution function peak at"
            ),
        )
        assert result is not None, "RDF peak must produce a property match"
        name, family = result
        assert name == "rdf_peak", f"expected rdf_peak, got {name!r}"
        assert family == "length", f"rdf_peak family must be length, got {family!r}"

    @pytest.mark.parametrize(
        "phrase",
        [
            "RDF peak",
            "radial distribution function peak",
            "g(r) peak",
        ],
    )
    def test_rdf_peak_various_phrasings(self, phrase: str):
        result = _match_property(phrase, len(phrase))
        assert result is not None
        assert result[0] == "rdf_peak"

    def test_rdf_peak_values_land_in_extraction(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        rdf_values = _values_by_names(result, ("rdf_peak",))
        assert any(v == pytest.approx(2.28) for v in rdf_values), (
            f"RDF peak 2.28 Å must land; got {rdf_values}"
        )
        assert any(v == pytest.approx(2.83) for v in rdf_values), (
            f"RDF peak 2.83 Å must land; got {rdf_values}"
        )

    def test_rdf_peak_length_family_compatible_units(self):
        # RDF peaks use angstrom — must be unit-compatible with 'length'
        # family to avoid _units_compatible rejecting them.
        assert _units_compatible("angstrom", "length") is True


class TestBondLengthPattern:
    """AC-A1 class #6: NEW property type - Cr-O bond length (2.02-2.05 Å).

    No existing _PROPERTY_RULES entry matches 'bond length' / 'bond
    distance'. Implementation must add a new rule mapping to
    property_name='bond_length' with family='length'.
    """

    def test_bond_length_matches_property_name(self):
        text = "The Cr-O bond length was"
        result = _match_property(text, len(text))
        assert result is not None, "bond length must produce a property match"
        name, family = result
        assert name == "bond_length", f"expected bond_length, got {name!r}"
        assert family == "length", f"bond_length family must be length, got {family!r}"

    @pytest.mark.parametrize(
        "phrase",
        [
            "Cr-O bond length",
            "Cr-O bond distance",
            "UO bond length",
            "bond distance",
        ],
    )
    def test_bond_length_various_phrasings(self, phrase: str):
        result = _match_property(phrase, len(phrase))
        assert result is not None
        assert result[0] == "bond_length"

    def test_bond_length_value_lands_in_extraction(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        bond_values = _values_by_names(result, ("bond_length",))
        # The fixture uses 2.04 angstrom as the headline Cr-O bond length;
        # range bounds (2.02, 2.05) also appear in the prose.
        assert any(
            v == pytest.approx(target) for v in bond_values for target in (2.02, 2.04, 2.05)
        ), (
            f"Cr-O bond length (2.02-2.05 Å range) must land; got {bond_values}"
        )

    def test_bond_length_does_not_match_unrelated_text(self):
        # Negative case: prose without "bond length" must NOT match.
        result = _match_property(
            "The temperature of the sample is", len(
                "The temperature of the sample is"
            ),
        )
        assert result is None


class TestF8ScorecardCompleteExtraction:
    """AC-A3: Re-run on Owen2023 fixture, all 8 F8 checkpoint values land.

    This is the end-to-end gate: every value in the F8 scorecard must
    appear somewhere in the extraction. Order of attributes does not
    matter; what matters is that each F8 row is emitted by
    heuristic_extract() on the Owen2023 text fixture.
    """

    EXPECTED_VALUES = {
        # (property_names_set, target_value, tolerance)
        # D0 may emit under either diffusion_coefficient or
        # pre_exponential_factor; both refer to the same physical
        # quantity (the F8 scorecard calls it "D0").
        (("activation_energy",), 0.30, 0.001),
        (("diffusion_coefficient", "pre_exponential_factor"), 3.32e-8, 1e-11),
        (("activation_energy",), 0.26, 0.001),
        (("diffusion_coefficient", "pre_exponential_factor"), 1.27e-9, 1e-12),
        (("density",), 10.55, 0.001),
        (("density",), 10.27, 0.001),
        (("rdf_peak",), 2.28, 0.001),
        (("rdf_peak",), 2.83, 0.001),
    }

    def test_all_eight_f8_values_present(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        # Collect (property_name, value) tuples that landed. Values
        # span ~12 orders of magnitude (1.27e-9 → 10.55), so we keep
        # raw floats rather than rounding (which would collapse the
        # 1e-9 / 1e-8 diffusivity values to 0.0).
        landed = [
            (r["property_name"], r["value"]) for r in result
        ]
        missing: list[tuple[tuple[str, ...], float]] = []
        for prop_names, target, tol in self.EXPECTED_VALUES:
            if not any(
                p in prop_names and abs(v - target) <= tol
                for p, v in landed
            ):
                missing.append((prop_names, target))
        assert not missing, (
            f"F8 scorecard rows missing from extraction: {missing}; "
            f"got {sorted(landed)}"
        )


# ---------------------------------------------------------------------------
# NFM-3511: DFT / theoretical calculation property patterns
# ---------------------------------------------------------------------------


class TestDftElasticConstants:
    """Elastic constants C11, C12, C44 in GPa."""

    def test_c11_gpa(self):
        text = "The elastic constant C11 of U3Si2 is 204.5 GPa"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "elastic_constant" in props
        match = next(r for r in result if r["property_name"] == "elastic_constant")
        assert match["value"] == pytest.approx(204.5)
        assert match["element_system"] == "U3Si2"

    def test_c12_gpa(self):
        text = "C12 = 78.3 GPa for UO2"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "elastic_constant" in props

    def test_c44_gpa(self):
        text = "The calculated C44 for gamma-U is 32.1 GPa"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "elastic_constant" in props

    def test_elastic_negative_no_match(self):
        """C in prose without numeric context should not fire."""
        text = "The carbon content was measured in U3C2"
        result = heuristic_extract(text, source_reference="dft_test")
        assert not any(r["property_name"] == "elastic_constant" for r in result)


class TestDftHeatOfFormation:
    """Heat of formation variant wording."""

    def test_heat_of_formation_ev(self):
        text = "The heat of formation of UO2 is -10.52 eV/atom"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "formation_energy" in props
        match = next(r for r in result if r["property_name"] == "formation_energy")
        assert match["value"] == pytest.approx(-10.52)

    def test_formation_energy_original_still_works(self):
        """Original formation_energy rule still matches."""
        text = "The formation energy of UZr2 is -5.3 eV/atom"
        result = heuristic_extract(text, source_reference="dft_test")
        assert any(r["property_name"] == "formation_energy" for r in result)


class TestDftOrderingTemperature:
    """Ordering/disordering temperature in K."""

    def test_ordering_temperature_k(self):
        text = "The ordering temperature of U-10Mo is 873 K"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "ordering_temperature" in props
        match = next(r for r in result if r["property_name"] == "ordering_temperature")
        assert match["value"] == pytest.approx(873)
        assert match["element_system"] == "U-10Mo"

    def test_disordering_temperature_k(self):
        text = "The disordering temperature was calculated as 950 K for UZr2"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "ordering_temperature" in props


class TestDftSolubilityLimit:
    """Solubility limit — tested via element_system filter.

    Note: at%/wt% have a pre-existing \\b boundary bug in
    _VALUE_UNIT_RE that prevents direct matching. These tests
    verify the property rule fires when a compatible unit lands;
    the \\b fix is tracked separately.
    """

    def test_solubility_limit_property_rule_fires(self):
        """Property name detection works for solubility limit."""
        text = "The solubility limit of Zr in U-10Mo reaches 30 at%"
        heuristic_extract(text, source_reference="dft_test")
        # May be empty due to at% \\b bug, but should not crash.
        # Verify the rule exists by checking _match_property directly.
        from nfm_db.services.heuristic_extractor import _match_property
        prop = _match_property(text, len(text) - 1)
        assert prop is not None
        assert prop[0] == "solubility_limit"


class TestDftDosAtFermiLevel:
    """DOS at Fermi level in eV."""

    def test_dos_fermi_ev(self):
        text = "The DOS at the Fermi level for UO2 is 2.35 eV"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "dos_at_fermi_level" in props
        match = next(r for r in result if r["property_name"] == "dos_at_fermi_level")
        assert match["value"] == pytest.approx(2.35)

    def test_nef_notation(self):
        text = "N(E_F) for UO2 was calculated to be 1.8 eV"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "dos_at_fermi_level" in props

    def test_density_of_states_fermi(self):
        text = "The density of states at Fermi level is 3.12 eV for U3Si2"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "dos_at_fermi_level" in props


class TestDftScreeningConstant:
    """Screening constant — unitless DFT property (direct pattern)."""

    def test_screening_constant_colon(self):
        text = "The screening constant of U-10Mo is 0.725"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "screening_constant" in props
        match = next(r for r in result if r["property_name"] == "screening_constant")
        assert match["value"] == pytest.approx(0.725)
        assert match["unit"] is None

    def test_screening_constant_equals(self):
        text = "For U-10Mo, screening constant = 0.80"
        result = heuristic_extract(text, source_reference="dft_test")
        assert any(r["property_name"] == "screening_constant" for r in result)

    def test_screening_constant_is(self):
        text = "The screening constant is 0.65 for UMo alloy"
        result = heuristic_extract(text, source_reference="dft_test")
        props = [r["property_name"] for r in result]
        assert "screening_constant" in props
        match = next(r for r in result if r["property_name"] == "screening_constant")
        assert match["value"] == pytest.approx(0.65)

    def test_screening_constant_negative(self):
        """Prose mentioning screening without a numeric value must not match."""
        text = "The screening effect was discussed in the context of UMo alloy"
        result = heuristic_extract(text, source_reference="dft_test")
        assert not any(r["property_name"] == "screening_constant" for r in result)


class TestDftRegressionOnExisting:
    """NFM-3511 AC-3: new patterns must not reduce existing recall."""

    def test_owen2023_f8_still_passes(self):
        """The full F8 scorecard extraction must still land all 8 values."""
        text = OWEN2023_FIXTURE.read_text()
        result = heuristic_extract(text, source_reference="owen2023")
        landed = [(r["property_name"], r["value"]) for r in result]
        expected = [
            (("activation_energy",), 0.30, 0.001),
            (("diffusion_coefficient", "pre_exponential_factor"), 3.32e-8, 1e-11),
            (("activation_energy",), 0.26, 0.001),
            (("diffusion_coefficient", "pre_exponential_factor"), 1.27e-9, 1e-12),
            (("density",), 10.55, 0.001),
            (("density",), 10.27, 0.001),
            (("rdf_peak",), 2.28, 0.001),
            (("rdf_peak",), 2.83, 0.001),
        ]
        for prop_names, target, tol in expected:
            assert any(
                p in prop_names and abs(v - target) <= tol
                for p, v in landed
            ), f"Regression: {prop_names}={target} missing from extraction"


# ---------------------------------------------------------------------------
# NFM-3835: heuristic_extract must emit property_category per finding so
# downstream PropertyType lookups land instead of being skipped.
# ---------------------------------------------------------------------------

#: Family → valid PropertyCategory literal (mirrors FAMILY_TO_CATEGORY in
#: heuristic_extractor.py so the test fails loudly if the mapping drifts).
EXPECTED_FAMILY_TO_CATEGORY: dict[str, str] = {
    "energy": "diffusion",
    "diffusivity": "diffusion",
    "density": "physical",
    "length": "physical",
    "pressure": "mechanical",
    "temperature": "thermal",
    "expansion": "thermal",
    "thermal_cond": "thermal",
    "specific_heat": "thermal",
    "dimensionless": "physical",
}


class TestHeuristicEmitsPropertyCategory:
    """NFM-3835 acceptance: every heuristic_extract() finding MUST carry a
    ``property_category`` field that is one of the 7 valid Pydantic
    literal categories. Without this, ``_coerce_unknown_categories`` falls
    back to "other" and the mapper records ``skipped_unknown_properties``
    for what is genuinely a known property (e.g. activation_energy,
    diffusion_coefficient, density).
    """

    def test_every_finding_has_property_category(self):
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        assert result, "fixture must produce some findings"
        for row in result:
            assert "property_category" in row, (
                f"row missing property_category: {row!r}"
            )
            assert row["property_category"] is not None, (
                f"row has None property_category: {row!r}"
            )

    @pytest.mark.parametrize(
        "prop_name,expected_category",
        [
            ("activation_energy", "diffusion"),
            ("diffusion_coefficient", "diffusion"),
            ("pre_exponential_factor", "diffusion"),
            ("formation_energy", "diffusion"),
            ("binding_energy", "diffusion"),
            ("band_gap", "diffusion"),
            ("cohesive_energy", "diffusion"),
            ("density", "physical"),
            ("lattice_constant", "physical"),
            ("grain_size", "physical"),
            ("rdf_peak", "physical"),
            ("bond_length", "physical"),
            ("elastic_constant", "mechanical"),
            ("youngs_modulus", "mechanical"),
            ("bulk_modulus", "mechanical"),
            ("shear_modulus", "mechanical"),
            ("thermal_conductivity", "thermal"),
            ("specific_heat", "thermal"),
            ("thermal_expansion_coefficient", "thermal"),
            ("melting_point", "thermal"),
            ("curie_temperature", "thermal"),
            ("ordering_temperature", "thermal"),
            ("porosity", "physical"),
            ("solubility_limit", "physical"),
            ("dos_at_fermi_level", "diffusion"),
            ("screening_constant", "physical"),
        ],
    )
    def test_property_category_by_family(self, prop_name, expected_category):
        """When a finding for this property_name lands, its category must match
        the family→category mapping defined in the issue spec."""
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        matching = [r for r in result if r["property_name"] == prop_name]
        if not matching:
            pytest.skip(
                f"fixture did not produce a {prop_name} finding; "
                "this test only constrains the category when one lands"
            )
        for row in matching:
            assert row["property_category"] == expected_category, (
                f"{prop_name} should map to category={expected_category!r}, "
                f"got {row['property_category']!r}"
            )

    def test_property_category_is_valid_literal(self):
        """property_category MUST be one of the 7 Pydantic Literal values
        accepted by ExtractedProperty (see extraction_to_db_mapper.py)."""
        from nfm_db.services.extraction_to_db_mapper import (
            _VALID_PROPERTY_CATEGORIES,
        )
        text = _owen2023_text()
        result = heuristic_extract(text, source_reference="owen2023")
        for row in result:
            assert row["property_category"] in _VALID_PROPERTY_CATEGORIES, (
                f"row property_category={row['property_category']!r} "
                f"not in {_VALID_PROPERTY_CATEGORIES}"
            )

    def test_family_to_category_mapping_complete(self):
        """Every family emitted by _PROPERTY_RULES must have a category."""
        from nfm_db.services.heuristic_extractor import (
            _PROPERTY_RULES,
            FAMILY_TO_CATEGORY,
        )
        for _pattern, _name, family in _PROPERTY_RULES:
            assert family in FAMILY_TO_CATEGORY, (
                f"family {family!r} (from property rule {_name!r}) "
                "has no FAMILY_TO_CATEGORY entry"
            )
        for family, category in FAMILY_TO_CATEGORY.items():
            assert category in {
                "mechanical", "thermal", "physical",
                "diffusion", "irradiation", "nuclear", "other",
            }, (
                f"FAMILY_TO_CATEGORY[{family!r}]={category!r} "
                "is not a valid PropertyCategory Literal"
            )


class TestPropertyMappingAliases:
    """NFM-3835 acceptance: the 6 missing English aliases must be present
    in ``property_mapping.json`` so that ``load_standard_properties()``
    returns the expected Chinese standard_name on lookup."""

    @pytest.fixture(scope="class")
    def mapping(self) -> dict[str, str]:
        from nfm_db.core.property_catalog import load_standard_properties
        return load_standard_properties()

    @pytest.mark.parametrize(
        "english_alias,expected_chinese",
        [
            ("activation energy", "扩散激活能"),
            ("diffusion coefficient", "扩散系数"),
            ("pre-exponential factor", "扩散前指数因子"),
            ("elastic constant", "弹性常数"),
            ("rdf peak", "RDF峰"),
            ("bond length", "键长"),
        ],
    )
    def test_alias_resolves_to_standard_name(
        self, mapping, english_alias, expected_chinese
    ):
        assert mapping.get(english_alias.lower()) == expected_chinese, (
            f"alias {english_alias!r} should resolve to "
            f"{expected_chinese!r}, got {mapping.get(english_alias.lower())!r}"
        )
