"""Unit tests for v4_record_to_staging() mapping function (NFM-542).

TDD RED phase: tests define the required behavior of v4_record_to_staging()
before any implementation exists.

Coverage:
- All 13 v4 fields mapped to correct staging columns
- Conditions dict decomposed into temperature/method
- Value string parsed (ranges, scientific notation, units stripped)
- Phase normalized via PhaseMapper
- Property name normalized via STANDARD_PROPERTIES
- Null/missing fields handled gracefully (no KeyError)
- Edge cases: empty strings, missing keys, range values
"""

from __future__ import annotations

import pytest

from nfm_db.services.v4_mapper import v4_record_to_staging


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_v4_record() -> dict:
    """A complete v4 record with all 13 fields populated."""
    return {
        "source_file": "papers/Smith2024_zr_alloy.md",
        "material_name": "Zircaloy-4",
        "composition": "Zr-1.5Sn-0.2Fe-0.1Cr",
        "phase": "α",
        "element": "Zr",
        "property_category": "热传导率",
        "property": "热导率",
        "value": "16.5",
        "unit": "W/m·K",
        "conditions": {"temp_C": 300, "simulation_method": "DFT"},
        "context": "Measured at 300K in alpha phase",
        "confidence": "high",
        "reference": "Smith et al., JNM 2024",
    }


@pytest.fixture
def minimal_v4_record() -> dict:
    """Minimal v4 record with only required fields."""
    return {
        "property": "热导率",
        "value": "16.5",
        "unit": "W/m·K",
    }


# ---------------------------------------------------------------------------
# 1. All 13 fields mapped correctly
# ---------------------------------------------------------------------------


class TestAllFieldsMapping:
    """Verify complete v4 record produces correct staging dict."""

    def test_all_13_fields_present_in_output(self, sample_v4_record: dict) -> None:
        """Output dict must contain all mapped staging columns."""
        result = v4_record_to_staging(sample_v4_record)

        expected_keys = {
            "source_file",
            "element_system",
            "composition",
            "phase",
            "element",
            "property_category",
            "property_name",
            "value",
            "unit",
            "temperature",
            "method",
            "context",
            "confidence",
            "source",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_source_file_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["source_file"] == "papers/Smith2024_zr_alloy.md"

    def test_material_name_maps_to_element_system(self, sample_v4_record: dict) -> None:
        """material_name should be copied to element_system (fallback mapping)."""
        result = v4_record_to_staging(sample_v4_record)
        assert result["element_system"] == "Zircaloy-4"

    def test_composition_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["composition"] == "Zr-1.5Sn-0.2Fe-0.1Cr"

    def test_element_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["element"] == "Zr"

    def test_property_category_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["property_category"] == "热传导率"

    def test_property_maps_to_property_name(self, sample_v4_record: dict) -> None:
        """property field should map to property_name staging column."""
        result = v4_record_to_staging(sample_v4_record)
        assert result["property_name"] == "热导率"

    def test_unit_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["unit"] == "W/m·K"

    def test_context_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["context"] == "Measured at 300K in alpha phase"

    def test_confidence_direct_copy(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["confidence"] == "high"

    def test_reference_maps_to_source(self, sample_v4_record: dict) -> None:
        """reference field should map to source staging column."""
        result = v4_record_to_staging(sample_v4_record)
        assert result["source"] == "Smith et al., JNM 2024"


# ---------------------------------------------------------------------------
# 2. Value parsing (string → float via parse_value)
# ---------------------------------------------------------------------------


class TestValueParsing:
    """Verify value string is correctly parsed to float."""

    def test_plain_number(self, minimal_v4_record: dict) -> None:
        minimal_v4_record["value"] = "16.5"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 16.5

    def test_scientific_notation(self, minimal_v4_record: dict) -> None:
        minimal_v4_record["value"] = "1.5e-3"
        result = v4_record_to_staging(minimal_v4_record)
        assert abs(result["value"] - 0.0015) < 1e-10

    def test_range_value_returns_midpoint(self, minimal_v4_record: dict) -> None:
        """Range '3 to 5' should produce midpoint 4.0."""
        minimal_v4_record["value"] = "3 to 5"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 4.0

    def test_uncertainty_strips_uncertainty(self, minimal_v4_record: dict) -> None:
        """'200 ± 10' should produce main_value 200."""
        minimal_v4_record["value"] = "200 ± 10"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 200.0

    def test_value_with_trailing_units(self, minimal_v4_record: dict) -> None:
        """Value with trailing unit text (ASCII) should be parsed to float."""
        minimal_v4_record["value"] = "16.5 MPa"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 16.5

    def test_integer_value(self, minimal_v4_record: dict) -> None:
        minimal_v4_record["value"] = "42"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 42.0

    def test_zero_value(self, minimal_v4_record: dict) -> None:
        minimal_v4_record["value"] = "0"
        result = v4_record_to_staging(minimal_v4_record)
        assert result["value"] == 0.0


# ---------------------------------------------------------------------------
# 3. Phase normalization via PhaseMapper
# ---------------------------------------------------------------------------


class TestPhaseNormalization:
    """Verify phase field is normalized via PhaseMapper."""

    def test_known_phase_alias_normalized(self) -> None:
        """Known phase alias 'α' should normalize to 'alpha'."""
        record = {
            "property": "密度",
            "value": "6.5",
            "unit": "g/cm³",
            "phase": "α",
        }
        result = v4_record_to_staging(record)
        assert result["phase"] == "alpha"

    def test_known_phase_direct(self) -> None:
        """Canonical phase 'alpha' should pass through."""
        record = {
            "property": "密度",
            "value": "6.5",
            "unit": "g/cm³",
            "phase": "alpha",
        }
        result = v4_record_to_staging(record)
        assert result["phase"] == "alpha"

    def test_unknown_phase_returns_none(self) -> None:
        """Unknown phase string should produce None."""
        record = {
            "property": "密度",
            "value": "6.5",
            "unit": "g/cm³",
            "phase": "unknown-phase-xyz",
        }
        result = v4_record_to_staging(record)
        assert result["phase"] is None

    def test_empty_phase_returns_none(self) -> None:
        record = {
            "property": "密度",
            "value": "6.5",
            "unit": "g/cm³",
            "phase": "",
        }
        result = v4_record_to_staging(record)
        assert result["phase"] is None

    def test_missing_phase_returns_none(self, minimal_v4_record: dict) -> None:
        result = v4_record_to_staging(minimal_v4_record)
        assert result["phase"] is None


# ---------------------------------------------------------------------------
# 4. Conditions dict decomposition
# ---------------------------------------------------------------------------


class TestConditionsDecomposition:
    """Verify conditions dict is decomposed into temperature/method."""

    def test_temp_c_extracted(self) -> None:
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {"temp_C": 300},
        }
        result = v4_record_to_staging(record)
        assert result["temperature"] == 300.0

    def test_temp_k_extracted(self) -> None:
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {"temp_K": 573},
        }
        result = v4_record_to_staging(record)
        assert abs(result["temperature"] - 299.85) < 0.01  # 573K - 273.15 = 299.85C

    def test_simulation_method_extracted(self) -> None:
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {"simulation_method": "DFT"},
        }
        result = v4_record_to_staging(record)
        assert result["method"] == "DFT"

    def test_both_temp_and_method(self, sample_v4_record: dict) -> None:
        result = v4_record_to_staging(sample_v4_record)
        assert result["temperature"] == 300.0
        assert result["method"] == "DFT"

    def test_empty_conditions_dict(self) -> None:
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {},
        }
        result = v4_record_to_staging(record)
        assert result["temperature"] is None
        assert result["method"] is None

    def test_missing_conditions_returns_none(self, minimal_v4_record: dict) -> None:
        result = v4_record_to_staging(minimal_v4_record)
        assert result["temperature"] is None
        assert result["method"] is None

    def test_temp_c_takes_priority_over_temp_k(self) -> None:
        """When both temp_C and temp_K are present, temp_C wins."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {"temp_C": 400, "temp_K": 573},
        }
        result = v4_record_to_staging(record)
        assert result["temperature"] == 400.0


# ---------------------------------------------------------------------------
# 5. Null/missing field handling
# ---------------------------------------------------------------------------


class TestNullFieldHandling:
    """Verify null and missing fields are handled gracefully."""

    def test_missing_optional_fields_produce_none(self) -> None:
        """Optional fields missing from input should be None in output."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
        }
        result = v4_record_to_staging(record)

        assert result["source_file"] is None
        assert result["element_system"] is None
        assert result["composition"] is None
        assert result["element"] is None
        assert result["property_category"] is None
        assert result["context"] is None
        assert result["source"] is None
        assert result["phase"] is None

    def test_none_values_produce_none(self) -> None:
        """Explicit None values should produce None in output."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "material_name": None,
            "phase": None,
            "composition": None,
            "element": None,
            "reference": None,
            "context": None,
        }
        result = v4_record_to_staging(record)
        assert result["element_system"] is None
        assert result["phase"] is None
        assert result["composition"] is None
        assert result["element"] is None
        assert result["source"] is None
        assert result["context"] is None

    def test_empty_string_treated_as_none(self) -> None:
        """Empty strings for optional fields should produce None."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "material_name": "",
            "phase": "",
            "reference": "",
        }
        result = v4_record_to_staging(record)
        assert result["element_system"] is None
        assert result["source"] is None

    def test_no_keyerror_on_minimal_record(self, minimal_v4_record: dict) -> None:
        """Minimal record with only required fields should not raise KeyError."""
        result = v4_record_to_staging(minimal_v4_record)
        assert "property_name" in result
        assert "value" in result
        assert "unit" in result

    def test_confidence_defaults_to_medium(self, minimal_v4_record: dict) -> None:
        """Missing confidence should default to 'medium'."""
        result = v4_record_to_staging(minimal_v4_record)
        assert result["confidence"] == "medium"


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Verify edge case handling."""

    def test_extra_keys_ignored(self) -> None:
        """Extra keys in input dict should be silently ignored."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "extra_field": "should be ignored",
        }
        result = v4_record_to_staging(record)
        assert "extra_field" not in result

    def test_value_parse_error_raises_valueerror(self, minimal_v4_record: dict) -> None:
        """Unparseable value string should raise ValueError."""
        minimal_v4_record["value"] = "not a number"
        with pytest.raises(ValueError, match="Cannot parse"):
            v4_record_to_staging(minimal_v4_record)

    def test_value_none_raises_valueerror(self, minimal_v4_record: dict) -> None:
        """None value should raise ValueError."""
        minimal_v4_record["value"] = None
        with pytest.raises(ValueError):
            v4_record_to_staging(minimal_v4_record)

    def test_conditions_as_non_dict_returns_none(self) -> None:
        """Non-dict conditions should produce None for temp/method."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": "300K, DFT",
        }
        result = v4_record_to_staging(record)
        assert result["temperature"] is None
        assert result["method"] is None

    def test_temp_as_string_converted_to_float(self) -> None:
        """String temperature values should be converted to float."""
        record = {
            "property": "热导率",
            "value": "16.5",
            "unit": "W/m·K",
            "conditions": {"temp_C": "300"},
        }
        result = v4_record_to_staging(record)
        assert result["temperature"] == 300.0

    def test_output_is_new_dict_not_input_mutation(self, sample_v4_record: dict) -> None:
        """Function must not mutate the input record."""
        original = dict(sample_v4_record)
        _ = v4_record_to_staging(sample_v4_record)
        assert sample_v4_record == original
