"""Unit tests for extraction rules (NFM-526).

Tests for:
- parse_value(): numeric parser
- clean_latex(): LaTeX cleaning
- is_extractable(): extractability filter
- assess_confidence(): confidence assessment
"""

import pytest

from nfm_db.core.extraction_rules import (
    Conditions,
    ConditionType,
    Confidence,
    assess_confidence,
    clean_latex,
    is_extractable,
    parse_value,
)

# ---------------------------------------------------------------------------
# clean_latex() Tests
# ---------------------------------------------------------------------------


class TestCleanLatex:
    """Test LaTeX cleaning functionality."""

    def test_subscript(self):
        """Test subscript removal: ${ZrO}_{2}$ → ZrO2."""
        assert clean_latex("${ZrO}_{2}$") == "ZrO2"

    def test_superscript(self):
        """Test superscript removal: $7\\times10^{25}$ → 7×10^25."""
        assert clean_latex("$7\\times10^{25}$") == "7×10^25"

    def test_squared(self):
        """Test squared: $m^{2}$ → m²."""
        assert clean_latex("$m^{2}$") == "m^2"

    def test_micro_meter(self):
        """Test micrometer: $\\mu{m}$ → μm."""
        assert clean_latex("$\\mu{m}$") == "μm"

    def test_celsius(self):
        """Test Celsius: $325{{\\circ}}_{{C}}$ → 325°C."""
        assert clean_latex("$325{{\\circ}}_{{C}}$") == "325°C"

    def test_greek_letter(self):
        """Test Greek letter: $\\alpha$ → alpha."""
        assert clean_latex("$\\alpha$") == "alpha"

    def test_times_symbol(self):
        """Test times symbol: \\times → ×."""
        assert clean_latex("1.5\\times10^{-3}") == "1.5×10^-3"

    def test_combined_latex(self):
        """Test combined LaTeX patterns."""
        assert clean_latex("$1.5\\times10^{-3}$") == "1.5×10^-3"


# ---------------------------------------------------------------------------
# parse_value() Tests
# ---------------------------------------------------------------------------


class TestParseValue:
    """Test numeric value parsing."""

    def test_plain_integer(self):
        """Test plain integer: "200" → 200.0."""
        result = parse_value("200")
        assert result.main_value == 200.0
        assert result.uncertainty is None
        assert result.range is None
        assert result.raw == "200"

    def test_plain_float(self):
        """Test plain float: "7.90" → 7.90."""
        result = parse_value("7.90")
        assert result.main_value == 7.90
        assert result.raw == "7.90"

    def test_range_to(self):
        """Test range with 'to': "3 to 4 μm" → range (3.0, 4.0)."""
        result = parse_value("3 to 4 μm")
        assert result.main_value == 3.5
        assert result.range == (3.0, 4.0)
        assert result.raw == "3 to 4 μm"

    def test_range_between(self):
        """Test range with 'between': "between 10 and 20 MPa" → range (10.0, 20.0)."""
        result = parse_value("between 10 and 20 MPa")
        assert result.main_value == 15.0
        assert result.range == (10.0, 20.0)

    def test_range_ranged_from(self):
        """Test range with 'ranged from': "ranged from 5 to 15" → range (5.0, 15.0)."""
        result = parse_value("ranged from 5 to 15")
        assert result.main_value == 10.0
        assert result.range == (5.0, 15.0)

    def test_range_dash(self):
        """Test range with dash: "3-4" → range (3.0, 4.0)."""
        result = parse_value("3-4")
        assert result.main_value == 3.5
        assert result.range == (3.0, 4.0)

    def test_uncertainty_pm(self):
        """Test uncertainty: "200 ± 10 MPa" → main_value=200, uncertainty=10."""
        result = parse_value("200 ± 10 MPa")
        assert result.main_value == 200.0
        assert result.uncertainty == 10.0
        assert result.raw == "200 ± 10 MPa"

    def test_scientific_notation_e(self):
        """Test scientific notation with 'e': "1.5e-3" → 0.0015."""
        result = parse_value("1.5e-3")
        assert result.main_value == 0.0015
        assert result.raw == "1.5e-3"

    def test_scientific_notation_x(self):
        """Test scientific notation with '×': "1.5×10^-3" → 0.0015."""
        result = parse_value("1.5×10^-3")
        assert result.main_value == 0.0015
        assert result.raw == "1.5×10^-3"

    def test_latex_scientific(self):
        """Test LaTeX scientific notation: "$1.5\\times10^{-3}$" → 0.0015."""
        result = parse_value("$1.5\\times10^{-3}$")
        assert result.main_value == 0.0015
        assert result.raw == "$1.5\\times10^{-3}$"

    def test_approximate_tilde(self):
        """Test approximate with tilde: "~200 MPa" → main_value=200."""
        result = parse_value("~200 MPa")
        assert result.main_value == 200.0
        assert result.raw == "~200 MPa"

    def test_approximately_word(self):
        """Test approximate with word: "approximately 8 GWd/t" → main_value=8."""
        result = parse_value("approximately 8 GWd/t")
        assert result.main_value == 8.0
        assert result.raw == "approximately 8 GWd/t"

    def test_empty_string(self):
        """Test empty string raises error."""
        with pytest.raises(ValueError, match="Cannot parse empty string"):
            parse_value("")

    def test_unparseable(self):
        """Test unparseable string raises error."""
        with pytest.raises(ValueError, match="Cannot parse numeric value"):
            parse_value("not a number")


# ---------------------------------------------------------------------------
# is_extractable() Tests
# ---------------------------------------------------------------------------


class TestIsExtractable:
    """Test extractability filter."""

    def test_numeric_value(self):
        """Test numeric value is extractable: "200 MPa" → True."""
        assert is_extractable("200 MPa") is True

    def test_trend_increases(self):
        """Test trend is not extractable: "increases with temperature" → False."""
        assert is_extractable("increases with temperature") is False

    def test_trend_decreases(self):
        """Test trend is not extractable: "decreases with pressure" → False."""
        assert is_extractable("decreases with pressure") is False

    def test_comparison_higher(self):
        """Test comparison is not extractable: "higher than control" → False."""
        assert is_extractable("higher than control") is False

    def test_comparison_lower(self):
        """Test comparison is not extractable: "lower than reference" → False."""
        assert is_extractable("lower than reference") is False

    def test_figure_reference(self):
        """Test figure reference is not extractable: "Fig. 3" → False."""
        assert is_extractable("Fig. 3") is False

    def test_table_reference(self):
        """Test table reference is not extractable: "Table 2" → False."""
        assert is_extractable("Table 2") is False

    def test_sample_id(self):
        """Test sample ID is not extractable: "165F" → False."""
        assert is_extractable("165F") is False

    def test_reference_number(self):
        """Test reference number is not extractable: "[12]" → False."""
        assert is_extractable("[12]") is False

    def test_ref_number(self):
        """Test ref number is not extractable: "Ref. 5" → False."""
        assert is_extractable("Ref. 5") is False

    def test_second_hand_citation(self):
        """Test second-hand citation is not extractable: "reported by Smith" → False."""
        assert is_extractable("reported by Smith 400 MPa") is False

    def test_empty_string(self):
        """Test empty string is not extractable."""
        assert is_extractable("") is False


# ---------------------------------------------------------------------------
# assess_confidence() Tests
# ---------------------------------------------------------------------------


class TestAssessConfidence:
    """Test confidence assessment."""

    def test_high_confidence_all_fields(self):
        """Test high confidence with all fields plus phase/conditions."""
        record = {
            "source_file": "md_output/test/paper.md",
            "material_name": "Zr-2.5Nb",
            "property_category": "力学性能",
            "property": "屈服强度",
            "value": "400",
            "unit": "MPa",
            "reference": "Smith et al., Test Paper",
            "phase": "alpha",
        }
        assert assess_confidence(record) == Confidence.HIGH

    def test_high_confidence_with_conditions(self):
        """Test high confidence with conditions instead of phase."""
        record = {
            "source_file": "md_output/test/paper.md",
            "material_name": "Zr-2.5Nb",
            "property_category": "力学性能",
            "property": "屈服强度",
            "value": "400",
            "unit": "MPa",
            "reference": "Smith et al., Test Paper",
            "conditions": {"temp_C": 25, "pressure_MPa": 100},
        }
        assert assess_confidence(record) == Confidence.HIGH

    def test_medium_confidence_missing_phase_conditions(self):
        """Test medium confidence without phase/conditions."""
        record = {
            "source_file": "md_output/test/paper.md",
            "material_name": "Zr-2.5Nb",
            "property_category": "力学性能",
            "property": "屈服强度",
            "value": "400",
            "unit": "MPa",
            "reference": "Smith et al., Test Paper",
        }
        assert assess_confidence(record) == Confidence.MEDIUM

    def test_low_confidence_minimum_fields(self):
        """Test low confidence with only property + value + unit."""
        record = {
            "property": "屈服强度",
            "value": "400",
            "unit": "MPa",
            "material_name": "Unknown alloy",
        }
        assert assess_confidence(record) == Confidence.LOW

    def test_no_material_object_raises_error(self):
        """Test missing minimum fields raises error."""
        record = {
            "property": "屈服强度",
            "value": "400",
        }
        with pytest.raises(ValueError, match="lacks minimum required fields"):
            assess_confidence(record)


# ---------------------------------------------------------------------------
# Conditions Model Tests
# ---------------------------------------------------------------------------


class TestConditionsModel:
    """Test Conditions Pydantic model."""

    def test_default_conditions(self):
        """Test default conditions with unknown type."""
        conditions = Conditions()
        assert conditions.condition_type == ConditionType.UNKNOWN

    def test_experimental_conditions(self):
        """Test experimental conditions."""
        conditions = Conditions(
            condition_type=ConditionType.EXPERIMENTAL,
            temp_C=25,
            pressure_MPa=100,
            atmosphere="air",
        )
        assert conditions.condition_type == ConditionType.EXPERIMENTAL
        assert conditions.temp_C == 25
        assert conditions.pressure_MPa == 100

    def test_simulation_conditions(self):
        """Test simulation conditions."""
        conditions = Conditions(
            condition_type=ConditionType.SIMULATION,
            simulation_method="DFT",
            model_name="VASP",
        )
        assert conditions.condition_type == ConditionType.SIMULATION
        assert conditions.simulation_method == "DFT"

    def test_field_alias(self):
        """Test field alias for strain_rate."""
        conditions = Conditions(strain_rate_s1="0.001")
        assert conditions.strain_rate_s1 == "0.001"

    def test_burnup_alias(self):
        """Test field alias for burnup."""
        conditions = Conditions(burnup_GWd_t="45")
        assert conditions.burnup_GWd_t == "45"
