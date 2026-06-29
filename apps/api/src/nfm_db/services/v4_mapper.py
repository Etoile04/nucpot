"""v4→Staging mapping function (NFM-542).

Maps the 13 v4 extraction fields from ExtractedProperty to the
RefGapFillStaging column schema. Uses Phase 2A normalization modules:
- PhaseMapper for phase field normalization
- STANDARD_PROPERTIES for property name normalization
- parse_value for numeric value parsing
"""

from __future__ import annotations

from typing import Any

from nfm_db.core.extraction_rules import parse_value
from nfm_db.core.phase_rules import PhaseMapper
from nfm_db.core.property_catalog import STANDARD_PROPERTIES

# ---------------------------------------------------------------------------
# Lazy-loaded singleton (expensive JSON config read happens once)
# ---------------------------------------------------------------------------

_phase_mapper: PhaseMapper | None = None


def _get_phase_mapper() -> PhaseMapper:
    """Return a lazily-initialized PhaseMapper from the default config."""
    global _phase_mapper
    if _phase_mapper is None:
        from pathlib import Path

        config_path = (
            Path(__file__).resolve().parent.parent / "config" / "phase_mapping.json"
        )
        _phase_mapper = PhaseMapper.from_config(config_path)
    return _phase_mapper


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def v4_record_to_staging(record: dict[str, Any]) -> dict[str, Any]:
    """Map a v4 extraction record to a staging record dict.

    Handles all 13 v4 field mappings:
    - source_file → source_file (direct copy)
    - material_name → element_system (fallback mapping)
    - composition → composition (direct copy)
    - phase → phase (PhaseMapper.normalize)
    - element → element (direct copy)
    - property_category → property_category (direct copy)
    - property → property_name (STANDARD_PROPERTIES normalization)
    - value → value (parse_value string → float)
    - unit → unit (direct copy)
    - conditions → temperature, method (dict decomposition)
    - context → context (direct copy)
    - confidence → confidence (direct copy, default 'medium')
    - reference → source (direct copy)

    Args:
        record: A v4 extraction record dict with up to 13 fields.

    Returns:
        A new dict with staging column names as keys.

    Raises:
        ValueError: If value cannot be parsed to a float.
    """
    # --- Value parsing (can raise ValueError early) ---
    raw_value = record.get("value")
    if raw_value is None:
        raise ValueError("Cannot parse numeric value from: None")
    parsed = parse_value(str(raw_value))
    numeric_value: float = parsed.main_value

    # --- Phase normalization ---
    raw_phase = record.get("phase")
    normalized_phase: str | None = None
    if raw_phase:
        mapper = _get_phase_mapper()
        normalized_phase = mapper.infer_phase(
            raw_phase=str(raw_phase),
            material=record.get("material_name"),
            context=record.get("context"),
        )

    # --- Property name normalization ---
    raw_property = record.get("property", "")
    property_name = _normalize_property_name(str(raw_property))

    # --- Conditions decomposition ---
    temperature, method = _decompose_conditions(record.get("conditions"))

    # --- Confidence ---
    confidence = record.get("confidence")
    if not confidence:
        confidence = "medium"

    # --- Direct-copy fields ---
    material_name = _coalesce_empty(record.get("material_name"))
    composition = _coalesce_empty(record.get("composition"))
    element = _coalesce_empty(record.get("element"))
    property_category = _coalesce_empty(record.get("property_category"))
    context = _coalesce_empty(record.get("context"))
    source_file = _coalesce_empty(record.get("source_file"))
    reference = _coalesce_empty(record.get("reference"))
    unit = _coalesce_empty(record.get("unit"))

    return {
        "source_file": source_file,
        "element_system": material_name,
        "composition": composition,
        "phase": normalized_phase,
        "element": element,
        "property_category": property_category,
        "property_name": property_name,
        "value": numeric_value,
        "unit": unit,
        "temperature": temperature,
        "method": method,
        "context": context,
        "confidence": confidence,
        "source": reference,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_property_name(raw_property: str) -> str:
    """Normalize a property name using STANDARD_PROPERTIES catalog.

    Args:
        raw_property: The raw property name from extraction.

    Returns:
        Normalized property name, or the original if no mapping found.
    """
    if not raw_property:
        return raw_property
    return STANDARD_PROPERTIES.get(raw_property.lower(), raw_property)


def _decompose_conditions(
    conditions: Any,
) -> tuple[float | None, str | None]:
    """Extract temperature and method from a conditions dict.

    Priority:
    - temp_C (direct Celsius) takes priority over temp_K
    - temp_K is converted to Celsius (K - 273.15)
    - simulation_method maps to method

    Args:
        conditions: A dict with condition fields, or non-dict (returns None tuple).

    Returns:
        Tuple of (temperature_celsius, method).
    """
    if not isinstance(conditions, dict):
        return None, None

    temperature: float | None = None

    # temp_C takes priority
    temp_c = conditions.get("temp_C")
    if temp_c is not None:
        try:
            temperature = float(temp_c)
        except (TypeError, ValueError):
            pass
    else:
        # Fall back to temp_K → Celsius
        temp_k = conditions.get("temp_K")
        if temp_k is not None:
            try:
                temperature = float(temp_k) - 273.15
            except (TypeError, ValueError):
                pass

    method: str | None = conditions.get("simulation_method")
    if method is not None:
        method = str(method)

    return temperature, method


def _coalesce_empty(value: Any) -> Any:
    """Return None for empty strings, None, or missing values."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value
