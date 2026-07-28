"""Tests for ExtractedProperty schema (NFM-1979 / AC-4).

Covers:
- `property_category` Literal enum validation (7 fixed values).
- Unknown category values are rejected at validation time.
- `value` field stays as `str` (preserves precision).
- `conditions` dict accepts the 5 standard keys.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.extraction import ExtractedProperty


# ---------------------------------------------------------------------------
# property_category Literal validation
# ---------------------------------------------------------------------------


VALID_CATEGORIES = (
    "mechanical",
    "thermal",
    "physical",
    "diffusion",
    "irradiation",
    "nuclear",
    "other",
)


@pytest.mark.unit
@pytest.mark.parametrize("category", VALID_CATEGORIES)
def test_property_category_accepts_each_literal_value(category: str) -> None:
    """Each of the 7 canonical Literal values must validate."""
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
        property_category=category,
    )
    assert prop.property_category == category


@pytest.mark.unit
@pytest.mark.parametrize("bad_category", ["Mechanical", "MECHANICAL", "腐蚀", "其他", "unknown"])
def test_property_category_rejects_unknown_value(bad_category: str) -> None:
    """Unknown category values must be rejected at validation time.

    Migration paths must explicitly bucket unknown values into ``other``.
    """
    with pytest.raises(ValidationError) as exc_info:
        ExtractedProperty(
            property="thermal_conductivity",
            value="8.5",
            unit="W/(m·K)",
            property_category=bad_category,
        )
    assert "property_category" in str(exc_info.value)


@pytest.mark.unit
def test_property_category_optional() -> None:
    """property_category may still be null (the field is optional)."""
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
    )
    assert prop.property_category is None


# ---------------------------------------------------------------------------
# value field remains a string
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_value_remains_str_to_preserve_precision() -> None:
    """`value` stays as `str` to preserve precision and ranges."""
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="3 to 4",
        unit="μm",
    )
    assert isinstance(prop.value, str)
    assert prop.value == "3 to 4"


@pytest.mark.unit
def test_value_rejects_non_string() -> None:
    """Non-string `value` (e.g., float) must be rejected."""
    with pytest.raises(ValidationError):
        ExtractedProperty(
            property="thermal_conductivity",
            value=8.5,  # type: ignore[arg-type]
            unit="W/(m·K)",
        )


# ---------------------------------------------------------------------------
# conditions standard keys
# ---------------------------------------------------------------------------


STANDARD_CONDITION_KEYS = (
    "temperature",
    "pressure",
    "neutron_flux",
    "dose",
    "strain_rate",
)


@pytest.mark.unit
@pytest.mark.parametrize("key", STANDARD_CONDITION_KEYS)
def test_conditions_accepts_each_standard_key(key: str) -> None:
    """Each of the 5 standard conditions keys round-trips through the schema."""
    conditions = {key: 1.0}
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
        conditions=conditions,
    )
    assert prop.conditions == conditions
    assert prop.conditions is not None
    assert key in prop.conditions


@pytest.mark.unit
def test_conditions_accepts_all_standard_keys_simultaneously() -> None:
    """All 5 standard keys together should round-trip without loss."""
    conditions = {
        "temperature": 600,
        "pressure": 0.1,
        "neutron_flux": 1.0e18,
        "dose": 5.0,
        "strain_rate": 1.0e-6,
    }
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
        conditions=conditions,
    )
    assert prop.conditions == conditions


@pytest.mark.unit
def test_conditions_accepts_unknown_keys_for_forward_compat() -> None:
    """Unknown conditions keys are still permitted (forward compat)."""
    conditions = {"temperature": 600, "future_key": "some-value"}
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
        conditions=conditions,
    )
    assert prop.conditions == conditions


@pytest.mark.unit
def test_conditions_is_optional() -> None:
    """conditions is optional (None by default)."""
    prop = ExtractedProperty(
        property="thermal_conductivity",
        value="8.5",
        unit="W/(m·K)",
    )
    assert prop.conditions is None