"""Extraction normalization layer (NFM-852).

Provides unit conversion to SI, value range validation, and
hash-based deduplication for extracted nuclear fuel property data.

Classes:
    UnitConverter         — Convert property values to canonical SI units
    ValueValidator        — Range-check values per property type
    DeduplicationDetector — Hash-based duplicate detection
    ExtractionNormalizer  — Orchestrates normalize + validate + dedup pipeline
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unit normalization aliases
# ---------------------------------------------------------------------------

_TEMPERATURE_ALIASES: Final[dict[str, str]] = {
    "k": "K",
    "c": "C",
    "f": "F",
    "deg c": "C",
    "deg f": "F",
    "celsius": "C",
    "fahrenheit": "F",
}

_PRESSURE_ALIASES: Final[dict[str, str]] = {
    "pa": "Pa",
    "mpa": "MPa",
    "gpa": "GPa",
    "bar": "bar",
    "psi": "psi",
    "kpa": "kPa",
    "atm": "atm",
}

_STRESS_ALIASES: Final[dict[str, str]] = {
    **_PRESSURE_ALIASES,
    "ksi": "ksi",
    "psig": "psi",
    "psia": "psi",
}

_THERMAL_CONDUCTIVITY_CANONICAL: Final[str] = "W/(m-K)"

_THERMAL_CONDUCTIVITY_ALIASES: Final[dict[str, str]] = {
    "w/(m-k)": _THERMAL_CONDUCTIVITY_CANONICAL,
    "w/(m*k)": _THERMAL_CONDUCTIVITY_CANONICAL,
    "w/m/k": _THERMAL_CONDUCTIVITY_CANONICAL,
    "w m-1 k-1": _THERMAL_CONDUCTIVITY_CANONICAL,
}

_DIFFUSION_CANONICAL: Final[str] = "m2/s"

_DIFFUSION_ALIASES: Final[dict[str, str]] = {
    "m2/s": _DIFFUSION_CANONICAL,
    "m^2/s": _DIFFUSION_CANONICAL,
    "cm2/s": "cm2/s",
    "cm^2/s": "cm2/s",
    "mm2/s": "mm2/s",
}

_CONVERSION_FACTORS: Final[dict[str, float]] = {
    # Pressure / Stress
    "Pa": 1.0,
    "kPa": 1_000.0,
    "MPa": 1_000_000.0,
    "GPa": 1_000_000_000.0,
    "bar": 100_000.0,
    "psi": 6894.75729,
    "atm": 101_325.0,
    "ksi": 6_894_757.29,
    # Diffusion coefficient
    "m2/s": 1.0,
    "cm2/s": 1e-4,
    "mm2/s": 1e-6,
}

_PROPERTY_SI_UNITS: Final[dict[str, str]] = {
    "temperature": "K",
    "pressure": "Pa",
    "stress": "Pa",
    "thermal_conductivity": _THERMAL_CONDUCTIVITY_CANONICAL,
    "diffusion_coefficient": _DIFFUSION_CANONICAL,
    "density": "kg/m3",
    "specific_heat": "J/(kg-K)",
    "thermal_expansion": "1/K",
    "youngs_modulus": "Pa",
    "shear_modulus": "Pa",
    "bulk_modulus": "Pa",
    "yield_strength": "Pa",
    "ultimate_tensile_strength": "Pa",
}

_VALIDATION_RANGES: Final[dict[str, tuple[float, float]]] = {
    "temperature": (0.0, 10_000.0),
    "pressure": (0.0, 500_000_000_000.0),
    "stress": (0.0, 500_000_000_000.0),
    "thermal_conductivity": (0.01, 500.0),
    "diffusion_coefficient": (1e-20, 1e-4),
    "density": (100.0, 25_000.0),
    "specific_heat": (10.0, 5_000.0),
    "thermal_expansion": (1e-8, 1e-3),
    "youngs_modulus": (1e6, 1e12),
    "shear_modulus": (1e6, 1e12),
    "bulk_modulus": (1e6, 1e12),
    "yield_strength": (1e3, 1e9),
    "ultimate_tensile_strength": (1e3, 1e9),
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedValue:
    """Result of normalizing an extracted value."""

    property_name: str
    original_value: float
    original_unit: str
    normalized_value: float
    normalized_unit: str
    is_valid: bool
    validation_errors: tuple[str, ...]


# ---------------------------------------------------------------------------
# UnitConverter
# ---------------------------------------------------------------------------


class UnitConverter:
    """Convert property values to canonical SI units.

    Supports temperature (K), pressure (Pa), stress (Pa),
    thermal conductivity (W/(m-K)), and diffusion coefficient (m2/s).
    """

    @staticmethod
    def _resolve_alias(unit: str, aliases: dict[str, str]) -> str:
        """Resolve a unit string via alias lookup (case-insensitive)."""
        key = unit.strip().lower()
        return aliases.get(key, unit.strip())

    @staticmethod
    def convert_temperature(value: float, unit: str) -> float:
        """Convert temperature to Kelvin.

        Supports: K, C, F (and aliases).
        Unknown units pass through unchanged.
        """
        resolved = UnitConverter._resolve_alias(unit, _TEMPERATURE_ALIASES)

        if resolved == "K":
            return value
        if resolved == "C":
            return value + 273.15
        if resolved == "F":
            return (value - 32.0) * 5.0 / 9.0 + 273.15

        return value

    @staticmethod
    def _is_known_temperature_unit(unit: str) -> bool:
        """Check if unit is a recognized temperature unit."""
        resolved = UnitConverter._resolve_alias(unit, _TEMPERATURE_ALIASES)
        return resolved in ("K", "C", "F")

    @staticmethod
    def convert_pressure(value: float, unit: str) -> float:
        """Convert pressure to Pascals.

        Supports: Pa, kPa, MPa, GPa, bar, psi, atm.
        """
        resolved = UnitConverter._resolve_alias(unit, _PRESSURE_ALIASES)
        factor = _CONVERSION_FACTORS.get(resolved)
        if factor is None:
            return value
        return value * factor

    @staticmethod
    def convert_stress(value: float, unit: str) -> float:
        """Convert stress to Pascals.

        Supports: Pa, kPa, MPa, GPa, bar, psi, ksi.
        """
        resolved = UnitConverter._resolve_alias(unit, _STRESS_ALIASES)
        factor = _CONVERSION_FACTORS.get(resolved)
        if factor is None:
            return value
        return value * factor

    @staticmethod
    def convert_thermal_conductivity(value: float, unit: str) -> float:
        """Convert thermal conductivity to W/(m-K).

        Already in SI; normalizes unit string aliases only.
        """
        resolved = UnitConverter._resolve_alias(
            unit, _THERMAL_CONDUCTIVITY_ALIASES
        )
        if resolved == _THERMAL_CONDUCTIVITY_CANONICAL:
            return value
        return value

    @staticmethod
    def convert_diffusion_coefficient(value: float, unit: str) -> float:
        """Convert diffusion coefficient to m2/s.

        Supports: m2/s, cm2/s, mm2/s.
        """
        resolved = UnitConverter._resolve_alias(unit, _DIFFUSION_ALIASES)
        factor = _CONVERSION_FACTORS.get(resolved)
        if factor is None:
            return value
        return value * factor

    @staticmethod
    def convert(
        property_type: str, value: float, unit: str
    ) -> tuple[float, str]:
        """Convert a value to SI for the given property type.

        Returns:
            Tuple of (converted_value, si_unit).
            Unknown property types return (value, unit) unchanged.
        """
        prop_key = property_type.strip().lower()

        if prop_key == "temperature":
            if UnitConverter._is_known_temperature_unit(unit):
                return UnitConverter.convert_temperature(value, unit), "K"
            return value, unit
        if prop_key in (
            "pressure", "youngs_modulus", "shear_modulus",
            "bulk_modulus", "yield_strength",
            "ultimate_tensile_strength",
        ):
            return UnitConverter.convert_pressure(value, unit), "Pa"
        if prop_key == "stress":
            return UnitConverter.convert_stress(value, unit), "Pa"
        if prop_key == "thermal_conductivity":
            return (
                UnitConverter.convert_thermal_conductivity(value, unit),
                _THERMAL_CONDUCTIVITY_CANONICAL,
            )
        if prop_key == "diffusion_coefficient":
            return (
                UnitConverter.convert_diffusion_coefficient(value, unit),
                _DIFFUSION_CANONICAL,
            )

        si_unit = _PROPERTY_SI_UNITS.get(prop_key, unit)
        return value, si_unit


# ---------------------------------------------------------------------------
# ValueValidator
# ---------------------------------------------------------------------------


class ValueValidator:
    """Range-based value validation for extracted property values.

    Checks whether a normalized (SI) value falls within physically
    reasonable bounds for its property type.
    """

    @staticmethod
    def validate(property_type: str, value: float) -> list[str]:
        """Validate a value against known range constraints.

        Args:
            property_type: Lowercase property type key.
            value: Value in SI units.

        Returns:
            List of validation error messages. Empty means valid.
        """
        prop_key = property_type.strip().lower()
        range_def = _VALIDATION_RANGES.get(prop_key)
        if range_def is None:
            return []

        min_val, max_val = range_def
        errors: list[str] = []

        if value < min_val:
            errors.append(
                f"{property_type} value {value} is below minimum {min_val}"
            )
        if value > max_val:
            errors.append(
                f"{property_type} value {value} exceeds maximum {max_val}"
            )

        return errors


# ---------------------------------------------------------------------------
# DeduplicationDetector
# ---------------------------------------------------------------------------


class DeduplicationDetector:
    """Hash-based duplicate detection for extracted values.

    Two extractions are duplicates if they share property name, value,
    unit (normalized), material, and source file.  Different source files
    for the same data point are NOT duplicates.
    """

    @staticmethod
    def _normalize_unit_for_hash(unit: str) -> str:
        """Normalize unit string for consistent hashing.

        Collapses equivalent representations like W/(m-K), W/(m*K), W/(m·K).
        """
        return (
            unit.strip().lower()
            .replace("·", "")
            .replace("²", "2")
            .replace("³", "3")
            .replace("*", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
            .replace(" ", "")
        )

    @staticmethod
    def compute_hash(value: Any) -> str:
        """Compute a deterministic SHA-256 hash for an extracted value."""
        normalized_unit = DeduplicationDetector._normalize_unit_for_hash(
            getattr(value, "unit", "")
        )
        hash_key = "|".join([
            getattr(value, "property_name", ""),
            f"{getattr(value, 'value', 0):.10g}",
            normalized_unit,
            (value.material_name or "").strip(),
            (value.source_file or "").strip(),
        ])
        return hashlib.sha256(hash_key.encode("utf-8")).hexdigest()

    @staticmethod
    def is_duplicate(a: Any, b: Any) -> bool:
        """Check if two extracted values are duplicates."""
        return (
            DeduplicationDetector.compute_hash(a)
            == DeduplicationDetector.compute_hash(b)
        )

    def deduplicate(self, values: list[Any]) -> list[Any]:
        """Remove duplicates from a list, preserving first occurrence order."""
        seen: set[str] = set()
        result: list[Any] = []
        for v in values:
            h = self.compute_hash(v)
            if h not in seen:
                seen.add(h)
                result.append(v)
        return result


# ---------------------------------------------------------------------------
# ExtractionNormalizer (orchestrator)
# ---------------------------------------------------------------------------


class ExtractionNormalizer:
    """Orchestrates the full extraction normalization pipeline.

    Pipeline: unit conversion -> value validation -> deduplication.
    """

    def __init__(
        self,
        converter: UnitConverter | None = None,
        validator: ValueValidator | None = None,
        detector: DeduplicationDetector | None = None,
    ) -> None:
        self.converter = converter or UnitConverter()
        self.validator = validator or ValueValidator()
        self.detector = detector or DeduplicationDetector()

    def normalize(self, value: Any) -> NormalizedValue:
        """Normalize a single extracted value.

        Converts units to SI and validates the result.
        """
        prop_name = getattr(value, "property_name", "")
        raw_value = getattr(value, "value", 0.0)
        raw_unit = getattr(value, "unit", "")

        normalized_value, si_unit = self.converter.convert(
            prop_name, raw_value, raw_unit
        )

        errors = self.validator.validate(prop_name, normalized_value)

        return NormalizedValue(
            property_name=prop_name,
            original_value=raw_value,
            original_unit=raw_unit,
            normalized_value=normalized_value,
            normalized_unit=si_unit,
            is_valid=len(errors) == 0,
            validation_errors=tuple(errors),
        )

    def normalize_batch(self, values: list[Any]) -> list[NormalizedValue]:
        """Normalize a batch of extracted values."""
        return [self.normalize(v) for v in values]

    def normalize_and_deduplicate(
        self, values: list[Any]
    ) -> list[NormalizedValue]:
        """Normalize, validate, and deduplicate in one pass."""
        normalized = self.normalize_batch(values)
        unique: list[NormalizedValue] = []
        seen: set[str] = set()
        for i, n in enumerate(normalized):
            src = values[i] if i < len(values) else None
            material = (src.material_name or "").strip() if src else ""
            source_file = (src.source_file or "").strip() if src else ""
            hash_key = (
                f"{n.property_name}|{n.normalized_value:.10g}"
                f"|{n.normalized_unit}|{material}|{source_file}"
            )
            if hash_key not in seen:
                seen.add(hash_key)
                unique.append(n)
        return unique

