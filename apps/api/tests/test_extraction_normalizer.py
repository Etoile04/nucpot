"""Tests for ExtractionNormalizer service (NFM-852).

Unit conversion, value validation, and deduplication logic.
Tests follow TDD RED → GREEN → REFACTOR.

Acceptance Criteria:
- Unit normalization converts all supported units to SI
- Value validation flags out-of-range values
- Deduplication detects >=95% of duplicate extractions
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest


# ---------------------------------------------------------------------------
# Test data structures (mirrors ExtractedProperty from schemas/extraction.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedValue:
    """Minimal extracted property value for normalization testing."""

    property_name: str
    value: float
    unit: str
    material_name: str | None = None
    source_file: str | None = None
    page_number: int | None = None
    context: str | None = None


@dataclass(frozen=True)
class NormalizedValue:
    """Result of normalizing an extracted value."""

    property_name: str
    original_value: float
    original_unit: str
    normalized_value: float
    normalized_unit: str
    is_valid: bool
    validation_errors: list[str]


# ===========================================================================
# Unit Conversion Tests
# ===========================================================================


class TestUnitConversion:
    """Tests for SI unit conversion across property types."""

    # -- Temperature conversions --

    @pytest.mark.unit
    def test_celsius_to_kelvin(self) -> None:
        """0 deg C = 273.15 K"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(0.0, "deg C")
        assert math.isclose(result, 273.15, abs_tol=1e-10)

    @pytest.mark.unit
    def test_celsius_to_kelvin_negative(self) -> None:
        """-273.15 deg C = 0 K (absolute zero)"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(-273.15, "deg C")
        assert math.isclose(result, 0.0, abs_tol=1e-10)

    @pytest.mark.unit
    def test_fahrenheit_to_kelvin(self) -> None:
        """212 deg F = 373.15 K (boiling point)"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(212.0, "deg F")
        assert math.isclose(result, 373.15, abs_tol=1e-9)

    @pytest.mark.unit
    def test_kelvin_passthrough(self) -> None:
        """K -> K (no conversion needed)"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(300.0, "K")
        assert math.isclose(result, 300.0, abs_tol=1e-10)

    @pytest.mark.unit
    def test_temperature_alias_c(self) -> None:
        """'C' is recognized as Celsius."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(100.0, "C")
        assert math.isclose(result, 373.15, abs_tol=1e-9)

    @pytest.mark.unit
    def test_temperature_alias_f(self) -> None:
        """'F' is recognized as Fahrenheit."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_temperature(32.0, "F")
        assert math.isclose(result, 273.15, abs_tol=1e-9)

    # -- Pressure conversions --

    @pytest.mark.unit
    def test_mpa_to_pa(self) -> None:
        """1 MPa = 1_000_000 Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_pressure(1.0, "MPa")
        assert result == 1_000_000.0

    @pytest.mark.unit
    def test_gpa_to_pa(self) -> None:
        """1 GPa = 1_000_000_000 Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_pressure(1.0, "GPa")
        assert result == 1_000_000_000.0

    @pytest.mark.unit
    def test_bar_to_pa(self) -> None:
        """1 bar = 100_000 Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_pressure(1.0, "bar")
        assert math.isclose(result, 100_000.0, abs_tol=1e-6)

    @pytest.mark.unit
    def test_psi_to_pa(self) -> None:
        """1 psi approx 6894.757 Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_pressure(1.0, "psi")
        assert math.isclose(result, 6894.75729, rel_tol=1e-5)

    @pytest.mark.unit
    def test_pa_passthrough(self) -> None:
        """Pa -> Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_pressure(100.0, "Pa")
        assert result == 100.0

    # -- Stress conversions (same as pressure) --

    @pytest.mark.unit
    def test_ksi_to_pa(self) -> None:
        """1 ksi = 1_000 psi approx 6_894_757 Pa"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_stress(1.0, "ksi")
        assert math.isclose(result, 6_894_757.29, rel_tol=1e-5)

    @pytest.mark.unit
    def test_mpa_stress_to_pa(self) -> None:
        """Stress uses same conversion as pressure."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_stress(200.0, "MPa")
        assert result == 200_000_000.0

    # -- Thermal conductivity --

    @pytest.mark.unit
    def test_thermal_conductivity_passthrough(self) -> None:
        """W/(m-K) -> W/(m-K)"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_thermal_conductivity(15.0, "W/(m-K)")
        assert result == 15.0

    @pytest.mark.unit
    def test_thermal_conductivity_w_mk_alias(self) -> None:
        """'W/(m*K)' recognized as W/(m-K)."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_thermal_conductivity(15.0, "W/(m*K)")
        assert result == 15.0

    # -- Diffusion coefficient --

    @pytest.mark.unit
    def test_diffusion_cm2_s_to_m2_s(self) -> None:
        """1 cm^2/s = 1e-4 m^2/s"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_diffusion_coefficient(1.0, "cm2/s")
        assert math.isclose(result, 1e-4, abs_tol=1e-15)

    @pytest.mark.unit
    def test_diffusion_m2_s_passthrough(self) -> None:
        """m^2/s -> m^2/s"""
        from nfm_db.services.extraction_normalizer import UnitConverter

        result = UnitConverter.convert_diffusion_coefficient(1e-12, "m2/s")
        assert result == 1e-12

    # -- Generic convert dispatch --

    @pytest.mark.unit
    def test_generic_convert_temperature(self) -> None:
        """Generic convert dispatches to correct converter."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        value, unit = UnitConverter.convert("temperature", 100.0, "deg C")
        assert math.isclose(value, 373.15, abs_tol=1e-9)
        assert unit == "K"

    @pytest.mark.unit
    def test_generic_convert_pressure(self) -> None:
        """Generic convert dispatches pressure correctly."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        value, unit = UnitConverter.convert("pressure", 1.0, "MPa")
        assert value == 1_000_000.0
        assert unit == "Pa"

    @pytest.mark.unit
    def test_generic_convert_unknown_property(self) -> None:
        """Unknown property returns original value/unit unchanged."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        value, unit = UnitConverter.convert("unknown_prop", 42.0, "cm")
        assert value == 42.0
        assert unit == "cm"

    @pytest.mark.unit
    def test_generic_convert_unknown_unit(self) -> None:
        """Unknown unit returns original value/unit unchanged."""
        from nfm_db.services.extraction_normalizer import UnitConverter

        value, unit = UnitConverter.convert("temperature", 42.0, "furlong")
        assert value == 42.0
        assert unit == "furlong"


# ===========================================================================
# Value Validation Tests
# ===========================================================================


class TestValueValidation:
    """Tests for range-based value validation."""

    @pytest.mark.unit
    def test_valid_temperature(self) -> None:
        """Temperature within 0-5000 K is valid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("temperature", 293.15)
        assert errors == []

    @pytest.mark.unit
    def test_invalid_temperature_too_low(self) -> None:
        """Temperature below 0 K is invalid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("temperature", -1.0)
        assert len(errors) > 0
        assert any("below minimum" in e.lower() or "range" in e.lower() for e in errors)

    @pytest.mark.unit
    def test_invalid_temperature_too_high(self) -> None:
        """Temperature above 10000 K is invalid for nuclear fuel."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("temperature", 50000.0)
        assert len(errors) > 0

    @pytest.mark.unit
    def test_valid_pressure(self) -> None:
        """Pressure in reasonable range is valid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("pressure", 101325.0)
        assert errors == []

    @pytest.mark.unit
    def test_invalid_pressure_negative(self) -> None:
        """Negative pressure is invalid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("pressure", -100.0)
        assert len(errors) > 0

    @pytest.mark.unit
    def test_valid_thermal_conductivity(self) -> None:
        """Thermal conductivity in 0.01-500 W/(m-K) is valid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("thermal_conductivity", 15.0)
        assert errors == []

    @pytest.mark.unit
    def test_invalid_thermal_conductivity_negative(self) -> None:
        """Negative thermal conductivity is physically impossible."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("thermal_conductivity", -1.0)
        assert len(errors) > 0

    @pytest.mark.unit
    def test_valid_density(self) -> None:
        """Density in 100-25000 kg/m3 is valid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("density", 10970.0)
        assert errors == []

    @pytest.mark.unit
    def test_invalid_density_zero(self) -> None:
        """Zero density is invalid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("density", 0.0)
        assert len(errors) > 0

    @pytest.mark.unit
    def test_unknown_property_validates_true(self) -> None:
        """Unknown property types pass validation (no range defined)."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("some_new_property", 999.0)
        assert errors == []

    @pytest.mark.unit
    def test_valid_diffusion_coefficient(self) -> None:
        """Diffusion coefficient in typical range is valid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("diffusion_coefficient", 1e-12)
        assert errors == []

    @pytest.mark.unit
    def test_invalid_diffusion_negative(self) -> None:
        """Negative diffusion coefficient is invalid."""
        from nfm_db.services.extraction_normalizer import ValueValidator

        errors = ValueValidator.validate("diffusion_coefficient", -1e-10)
        assert len(errors) > 0


# ===========================================================================
# Deduplication Tests
# ===========================================================================


class TestDeduplication:
    """Tests for hash-based duplicate detection."""

    @pytest.mark.unit
    def test_exact_duplicate_detected(self) -> None:
        """Identical extractions are detected as duplicates."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="thermal_conductivity",
            value=15.0,
            unit="W/(m-K)",
            material_name="UO2",
            source_file="paper1.md",
        )
        v2 = ExtractedValue(
            property_name="thermal_conductivity",
            value=15.0,
            unit="W/(m-K)",
            material_name="UO2",
            source_file="paper1.md",
        )
        assert detector.is_duplicate(v1, v2)

    @pytest.mark.unit
    def test_different_values_not_duplicate(self) -> None:
        """Different values for same property are not duplicates."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="thermal_conductivity",
            value=15.0,
            unit="W/(m-K)",
            material_name="UO2",
        )
        v2 = ExtractedValue(
            property_name="thermal_conductivity",
            value=20.0,
            unit="W/(m-K)",
            material_name="UO2",
        )
        assert not detector.is_duplicate(v1, v2)

    @pytest.mark.unit
    def test_different_properties_not_duplicate(self) -> None:
        """Different property types are not duplicates."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="density",
            value=10970.0,
            unit="kg/m3",
            material_name="UO2",
        )
        v2 = ExtractedValue(
            property_name="thermal_conductivity",
            value=10970.0,
            unit="kg/m3",
            material_name="UO2",
        )
        assert not detector.is_duplicate(v1, v2)

    @pytest.mark.unit
    def test_different_materials_not_duplicate(self) -> None:
        """Same property, different materials, not duplicate."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="density",
            value=10970.0,
            unit="kg/m3",
            material_name="UO2",
        )
        v2 = ExtractedValue(
            property_name="density",
            value=10970.0,
            unit="kg/m3",
            material_name="PuO2",
        )
        assert not detector.is_duplicate(v1, v2)

    @pytest.mark.unit
    def test_unit_normalized_duplicate_detected(self) -> None:
        """Same value in equivalent units is still a duplicate."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="thermal_conductivity",
            value=15.0,
            unit="W/(m-K)",
            material_name="UO2",
        )
        v2 = ExtractedValue(
            property_name="thermal_conductivity",
            value=15.0,
            unit="W/(m*K)",
            material_name="UO2",
        )
        assert detector.is_duplicate(v1, v2)

    @pytest.mark.unit
    def test_deduplicate_batch(self) -> None:
        """Batch deduplication removes duplicates, keeps uniques."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        values = [
            ExtractedValue("density", 10970.0, "kg/m3", "UO2"),
            ExtractedValue("density", 10970.0, "kg/m3", "UO2"),
            ExtractedValue("density", 10970.0, "kg/m3", "UO2"),
            ExtractedValue("thermal_conductivity", 15.0, "W/(m-K)", "UO2"),
            ExtractedValue("density", 11300.0, "kg/m3", "PuO2"),
        ]
        result = detector.deduplicate(values)
        assert len(result) == 3

    @pytest.mark.unit
    def test_hash_stability(self) -> None:
        """Hash is deterministic for same input."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v = ExtractedValue("density", 10970.0, "kg/m3", "UO2")
        h1 = detector.compute_hash(v)
        h2 = detector.compute_hash(v)
        assert h1 == h2

    @pytest.mark.unit
    def test_near_duplicate_different_sources(self) -> None:
        """Same property/value from different sources is not a duplicate."""
        from nfm_db.services.extraction_normalizer import DeduplicationDetector

        detector = DeduplicationDetector()
        v1 = ExtractedValue(
            property_name="density",
            value=10970.0,
            unit="kg/m3",
            material_name="UO2",
            source_file="paper1.md",
        )
        v2 = ExtractedValue(
            property_name="density",
            value=10970.0,
            unit="kg/m3",
            material_name="UO2",
            source_file="paper2.md",
        )
        assert not detector.is_duplicate(v1, v2)


# ===========================================================================
# Integration: Full ExtractionNormalizer Pipeline
# ===========================================================================


class TestExtractionNormalizerPipeline:
    """End-to-end normalization pipeline tests."""

    @pytest.mark.unit
    def test_normalize_single_value(self) -> None:
        """Normalizing a single extracted value produces correct result."""
        from nfm_db.services.extraction_normalizer import ExtractionNormalizer

        normalizer = ExtractionNormalizer()
        extracted = ExtractedValue(
            property_name="temperature",
            value=800.0,
            unit="deg C",
            material_name="UO2",
        )
        result = normalizer.normalize(extracted)
        assert math.isclose(result.normalized_value, 1073.15, abs_tol=1e-9)
        assert result.normalized_unit == "K"
        assert result.is_valid

    @pytest.mark.unit
    def test_normalize_invalid_value_flagged(self) -> None:
        """Out-of-range values are flagged as invalid."""
        from nfm_db.services.extraction_normalizer import ExtractionNormalizer

        normalizer = ExtractionNormalizer()
        extracted = ExtractedValue(
            property_name="temperature",
            value=-500.0,
            unit="deg C",
            material_name="UO2",
        )
        result = normalizer.normalize(extracted)
        assert not result.is_valid
        assert len(result.validation_errors) > 0

    @pytest.mark.unit
    def test_normalize_batch(self) -> None:
        """Batch normalization produces correct results."""
        from nfm_db.services.extraction_normalizer import ExtractionNormalizer

        normalizer = ExtractionNormalizer()
        values = [
            ExtractedValue("temperature", 100.0, "deg C", "UO2"),
            ExtractedValue("pressure", 1.0, "MPa", "UO2"),
            ExtractedValue("thermal_conductivity", 15.0, "W/(m-K)", "UO2"),
        ]
        results = normalizer.normalize_batch(values)
        assert len(results) == 3

        # Temperature: 100 deg C -> 373.15 K
        assert math.isclose(results[0].normalized_value, 373.15, abs_tol=1e-9)
        assert results[0].normalized_unit == "K"

        # Pressure: 1 MPa -> 1_000_000 Pa
        assert results[1].normalized_value == 1_000_000.0
        assert results[1].normalized_unit == "Pa"

        # Thermal conductivity: passthrough
        assert results[2].normalized_value == 15.0
        assert results[2].normalized_unit == "W/(m-K)"

    @pytest.mark.unit
    def test_normalize_and_deduplicate_batch(self) -> None:
        """Combined normalization + deduplication."""
        from nfm_db.services.extraction_normalizer import ExtractionNormalizer

        normalizer = ExtractionNormalizer()
        values = [
            ExtractedValue("density", 10970.0, "kg/m3", "UO2"),
            ExtractedValue("density", 10970.0, "kg/m3", "UO2"),
            ExtractedValue("density", 11300.0, "kg/m3", "PuO2"),
        ]
        results = normalizer.normalize_and_deduplicate(values)
        assert len(results) == 2
