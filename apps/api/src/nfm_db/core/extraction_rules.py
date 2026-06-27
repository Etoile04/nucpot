"""Extraction rules for nuclear property extraction (NFM-526).

This module implements v4 extraction rules:
- parse_value(): numeric parser for ranges, uncertainties, scientific notation, LaTeX
- is_extractable(): filter for non-extractable types
- assess_confidence(): three-level confidence assessment
- ConditionType Enum and Conditions Model
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class ConditionType(str, Enum):
    """Type of condition for measurement."""

    EXPERIMENTAL = "experimental"
    SIMULATION = "simulation"
    SERVICE = "service"
    PROCESSING = "processing"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Conditions(BaseModel):
    """Measurement conditions for extracted properties."""

    condition_type: ConditionType = Field(default=ConditionType.UNKNOWN)
    temp_C: float | str | None = Field(default=None, description="Temperature in Celsius")
    temp_K: float | str | None = Field(default=None, description="Temperature in Kelvin")
    pressure_MPa: float | str | None = Field(default=None, description="Pressure in MPa")
    stress_MPa: float | str | None = Field(default=None, description="Stress in MPa")
    strain_rate_s1: float | str | None = Field(
        default=None, alias="strain_rate_s-1", description="Strain rate in s^-1"
    )
    time_h: float | str | None = Field(default=None, description="Time in hours")
    dpa: float | str | None = Field(default=None, description="Displacement per atom (dpa)")
    fluence_n_m2: str | None = Field(default=None, description="Neutron fluence in n/m^2")
    flux_n_m2_s: str | None = Field(default=None, description="Neutron flux in n/m^2/s")
    burnup_GWd_t: float | str | None = Field(
        default=None, alias="burnup_GWd/t", description="Burnup in GWd/t"
    )
    atmosphere: str | None = Field(default=None, description="Atmosphere or medium")
    simulation_method: str | None = Field(
        default=None, description="Simulation method: DFT/MD/FEM/phase-field"
    )
    model_name: str | None = Field(default=None, description="Model name")
    processing_state: str | None = Field(
        default=None, description="Processing state: heat treatment, cold work"
    )

    class Config:
        """Pydantic config."""

        populate_by_name = True


@dataclass
class ParsedValue:
    """Parsed numeric value with metadata.

    Attributes:
        main_value: Primary numeric value
        uncertainty: Measurement uncertainty (± value)
        range: Tuple of (min, max) for range values
        raw: Original string representation
    """

    main_value: float
    uncertainty: float | None = None
    range: tuple[float, float] | None = None
    raw: str = ""


class Confidence(str, Enum):
    """Confidence level for extracted property."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# LaTeX Cleaning
# ---------------------------------------------------------------------------


def clean_latex(text: str) -> str:
    """Remove LaTeX formatting from text.

    Args:
        text: Raw text possibly containing LaTeX

    Returns:
        Cleaned text with LaTeX stripped
    """
    # Remove common LaTeX patterns (order matters!)
    replacements = [
        # Handle \circ for degrees (first, before brace patterns)
        (r"\\circ", "°"),
        # Handle \mu before general patterns
        (r"\\mu", "μ"),
        # Handle \times before exponent patterns
        (r"\\times", "×"),
        # Handle \pm
        (r"\\pm", "±"),
        # Handle double-braced subscripts with any content: $_{{X}}$ -> _X$
        (r"_\{\{([^}]+)\}\}", r"_\1"),
        # Handle subscript with special chars (no braces): $X_C$ -> XC
        (r"\$([^$]*)_([A-Za-z]+)\$", r"\1\2"),
        # Handle subscript with special chars (braced): ${X°}_{C}$ -> X°C
        (r"\$\{([^}]+)°\}_\{([A-Za-z]+)\}\$", r"\1\2"),
        # Handle subscript: ${X}_{2}$ -> X2
        (r"\$\{([A-Za-z0-9]+)\}_\{(\d+)\}\$", r"\1\2"),
        # Handle subscript with letters: ${X}_{C}$ -> XC
        (r"\$\{([A-Za-z0-9]+)\}_\{([A-Za-z]+)\}\$", r"\1\2"),
        # Handle superscript with times: ${X}×10^{-3}$ -> X×10^-3
        (r"\$\{([A-Za-z0-9]+)\}×10\^\{(-?\d+)\}\$", r"\1×10^\2"),
        # Handle plain superscript: ${X}^{-3}$ -> X^-3
        (r"\$\{([A-Za-z0-9]+)\}\^\{(-?\d+)\}\$", r"\1^\2"),
        # Handle braces: $m^{2}$ -> m^2 (after special cases)
        (r"\$\{?([A-Za-z0-9]+)\}\?\^\{?(-?\d+)\}?\$", r"\1^\2"),
        # Remove remaining double braces
        (r"\{\{([^\}]+)\}\}", r"\1"),
        # Remove remaining braces around content
        (r"\{([^\}]+)\}", r"\1"),
        # Remove dollar signs
        (r"\$", ""),
        # Handle remaining Greek letters
        (r"\\([a-z]+)", r"\1"),
        # Handle \text{foo} -> foo
        (r"\\text\{([^}]+)\}", r"\1"),
    ]

    result = text
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result)

    return result


# ---------------------------------------------------------------------------
# Numeric Parser
# ---------------------------------------------------------------------------


def parse_value(raw_string: str) -> ParsedValue:
    """Parse numeric value from literature text.

    Handles:
    - Range values: "3 to 4", "3-4" → range
    - Uncertainty: "200 ± 10" → main_value + uncertainty
    - Scientific notation: "1.5e-3", "1.5×10^-3" → float
    - LaTeX: "$1.5\\times10^{-3}$" → cleaned float
    - Approximate: "~200" → main_value (preserve raw)

    Args:
        raw_string: Raw text from literature

    Returns:
        ParsedValue with main_value, optional uncertainty/range, and raw string
    """
    if not raw_string:
        raise ValueError("Cannot parse empty string")

    # Clean LaTeX first
    text = clean_latex(raw_string.strip())

    # Strip trailing units like Å, MPa, GWd/t, etc. (including non-ASCII)
    text = re.sub(r"\s+[A-Za-z/°μ²²%ÅÅ]+\.?$", "", text)

    # Pattern 1: Range values (e.g., "3 to 4", "3-4", "ranged from 5 to 15")
    range_patterns = [
        r"(\d+\.?\d*)\s+to\s+(\d+\.?\d*)",
        r"between\s+(\d+\.?\d*)\s+and\s+(\d+\.?\d*)",
        r"ranged?\s+from\s+(\d+\.?\d*)\s+to\s+(\d+\.?\d*)",
        r"(\d+\.?\d*)\s*-\s*(\d+\.?\d*)",
    ]

    for pattern in range_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            min_val = float(match.group(1))
            max_val = float(match.group(2))
            return ParsedValue(
                main_value=(min_val + max_val) / 2,
                range=(min_val, max_val),
                raw=raw_string,
            )

    # Pattern 2: Uncertainty (e.g., "200 ± 10")
    uncertainty_pattern = r"(\d+\.?\d*)\s*[±±]\s*(\d+\.?\d*)"
    match = re.search(uncertainty_pattern, text)
    if match:
        main_val = float(match.group(1))
        uncertainty = float(match.group(2))
        return ParsedValue(
            main_value=main_val, uncertainty=uncertainty, raw=raw_string
        )

    # Pattern 3: Scientific notation (e.g., "1.5e-3", "1.5×10^-3")
    sci_patterns = [
        r"(\d+\.?\d*)[eE]([+-]?\d+)",  # 1.5e-3
        r"(\d+\.?\d*)\s*[×x]\s*10\^([+-]?\d+)",  # 1.5×10^-3
    ]

    for pattern in sci_patterns:
        match = re.search(pattern, text)
        if match:
            base = float(match.group(1))
            exponent = int(match.group(2))
            return ParsedValue(
                main_value=base * (10**exponent),
                raw=raw_string,
            )

    # Pattern 4: Approximate values (e.g., "~200", "approximately 200")
    # Strip trailing units like "MPa", "GWd/t", etc.
    text_without_units = re.sub(r"\s+[A-Za-z/°μ²%.]+$", "", text.strip())
    approx_pattern = r"^(?:~|approx\.?|approximately)?\s*(\d+\.?\d*)$"
    match = re.match(approx_pattern, text_without_units)
    if match:
        main_val = float(match.group(1))
        return ParsedValue(main_value=main_val, raw=raw_string)

    # Pattern 5: Plain number
    plain_pattern = r"^(\d+\.?\d*)$"
    match = re.match(plain_pattern, text.strip())
    if match:
        main_val = float(match.group(1))
        return ParsedValue(main_value=main_val, raw=raw_string)

    raise ValueError(f"Cannot parse numeric value from: {raw_string}")


# ---------------------------------------------------------------------------
# Extractability Filter
# ---------------------------------------------------------------------------


def is_extractable(text: str) -> bool:
    """Check if text describes an extractable numeric property.

    Filters out:
    - Trends: "increases with temperature"
    - Qualitative comparisons: "higher than control"
    - Figure/table numbers: "Fig. 3", "Table 2"
    - Sample IDs: "165F"
    - Reference numbers: "[12]", "Ref. 5"

    Args:
        text: Text to check

    Returns:
        True if text describes an extractable property
    """
    if not text:
        return False

    text_lower = text.lower()

    # Non-extractable patterns
    non_extractable_patterns = [
        r"\b(increases?|decreases?|rises?|falls?|drops?|grows?)\s+with\b",  # Trends
        r"\b(higher|lower|greater|less|smaller|larger)\s+than\b",  # Comparisons
        r"\b(fig\.?|figure)\s+\d+",  # Figure references
        r"\b(table)\s+\d+",  # Table references
        r"\[\d+\]",  # Reference numbers [12]
        r"\[ref\.?|ref\.?\s+\d+",  # Alternative reference patterns
        r"^\d+[a-z]?f?$",  # Sample IDs like 165F
        r"\b(reported|mentioned|described)\s+by\b",  # Second-hand citations
    ]

    for pattern in non_extractable_patterns:
        if re.search(pattern, text_lower):
            return False

    # Check if text contains a numeric value
    numeric_pattern = r"\d+\.?\d*"
    has_numeric = re.search(numeric_pattern, text)

    return has_numeric is not None


# ---------------------------------------------------------------------------
# Confidence Assessment
# ---------------------------------------------------------------------------


def assess_confidence(record: dict[str, Any]) -> Confidence:
    """Assess confidence level for an extracted property record.

    Rules:
    - high: source_file + material_name + property_category + property + value + unit + reference complete, with phase/conditions
    - medium: material_name + property + value + unit + reference complete, but missing phase/conditions
    - low: only property + value + unit, material can be identified but insufficient context

    Args:
        record: Extracted property record as dict

    Returns:
        Confidence level (high/medium/low)
    """
    required_fields = ["source_file", "material_name", "property_category", "property", "value", "unit", "reference"]

    # Check high confidence: all required fields + phase/conditions
    has_all_required = all(record.get(field) for field in required_fields)
    has_phase_or_conditions = record.get("phase") or record.get("conditions")

    if has_all_required and has_phase_or_conditions:
        return Confidence.HIGH

    # Check medium confidence: material_name + property + value + unit + reference
    medium_fields = ["material_name", "property", "value", "unit", "reference"]
    has_medium = all(record.get(field) for field in medium_fields)

    if has_medium:
        return Confidence.MEDIUM

    # Check low confidence: at least property + value + unit
    low_fields = ["property", "value", "unit"]
    has_low = all(record.get(field) for field in low_fields)

    if has_low:
        return Confidence.LOW

    # No material object = not extractable
    raise ValueError("Record lacks minimum required fields (property, value, unit)")
