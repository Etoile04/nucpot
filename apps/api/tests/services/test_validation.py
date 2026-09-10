"""Tests for the G1-D validity_check service (NFM-4550, spec §8.4, AC-10).

Covers ``evaluate_valid_range`` from
``nfm_db.services.validation``. The function reads the per-property
``valid_range_min`` / ``valid_range_max`` columns on ``property_types``
and the value(s) on a freshly inserted ``property_measurements`` row
and emits a ``ValidityResult`` of {status, reason}.

Status semantics:
    "ok"    — value is within the configured physical range
    "warn"  — no range is configured for this property (so we can't
              decide); the caller persists status="warn" + reason
              explaining that the range is unset.
    "fail"  — value is outside the configured range (physically
              invalid). The mapper overrides review_status="invalid"
              on the row when status="fail".

AC-10 cases (spec §10):
    * bond length 0.3 Angstrom against lattice_constant
      (valid_range ~0.5 to 20 Angstrom) → status="fail"
    * density 0.05 against density (valid_range ~0.1 to 30 g/cm^3)
      → status="fail"
"""

from __future__ import annotations

import pytest

from nfm_db.services.validation import ValidityResult, evaluate_valid_range

# ---------------------------------------------------------------------------
# AC-10 — physical invalid values fail
# ---------------------------------------------------------------------------


def test_bond_length_0p3_angstrom_fails_against_lattice_range() -> None:
    """AC-10: 键长 0.3Å is below any atomic radius → fail."""

    result = evaluate_valid_range(
        property_name="lattice_constant",
        valid_range_min=0.5,  # Angstrom — sub-atomic is non-physical
        valid_range_max=20.0,
        value_scalar=0.3,
        value_min=None,
        value_max=None,
        unit="Å",
    )
    assert result == ValidityResult(
        status="fail",
        reason="lattice_constant=0.3 Å outside physical range [0.5, 20] Å",
    )


def test_density_0p05_fails_against_density_range() -> None:
    """AC-10: density 0.05 g/cm³ is below any solid → fail."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,  # g/cm³ — lighter than any solid
        valid_range_max=30.0,
        value_scalar=0.05,
        value_min=None,
        value_max=None,
        unit="g/cm³",
    )
    assert result == ValidityResult(
        status="fail",
        reason="density=0.05 g/cm³ outside physical range [0.1, 30] g/cm³",
    )


# ---------------------------------------------------------------------------
# Boundary / nominal cases
# ---------------------------------------------------------------------------


def test_scalar_within_range_is_ok() -> None:
    """A canonical UO2 density (10.97 g/cm³) is in range → ok."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=10.97,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok"
    assert result.reason is None


def test_scalar_at_lower_bound_is_ok() -> None:
    """The lower bound itself is inclusive."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=0.1,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok"
    assert result.reason is None


def test_scalar_at_upper_bound_is_ok() -> None:
    """The upper bound itself is inclusive."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=30.0,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok"
    assert result.reason is None


def test_scalar_above_upper_bound_fails() -> None:
    """Density 50 g/cm³ is heavier than any solid → fail."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=50.0,
        value_min=None,
        value_max=None,
    )
    assert result.status == "fail"
    assert result.reason is not None
    assert "50" in result.reason
    assert "outside" in result.reason


# ---------------------------------------------------------------------------
# No range configured → warn (cannot decide)
# ---------------------------------------------------------------------------


def test_no_range_configured_returns_warn() -> None:
    """When neither min nor max is set, we can't decide → warn."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=None,
        valid_range_max=None,
        value_scalar=10.97,
        value_min=None,
        value_max=None,
    )
    assert result.status == "warn"
    assert result.reason is not None
    assert "range" in result.reason.lower()


def test_only_min_configured_is_one_sided_bound() -> None:
    """A missing upper bound should not trigger a fail."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.5,
        valid_range_max=None,
        value_scalar=10_000.0,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok"


def test_only_max_configured_is_one_sided_bound() -> None:
    """A missing lower bound should not trigger a fail."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=None,
        valid_range_max=30.0,
        value_scalar=0.0001,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok"


def test_no_range_no_value_returns_warn() -> None:
    """Range unset AND value missing — degenerate but should not crash."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=None,
        valid_range_max=None,
        value_scalar=None,
        value_min=None,
        value_max=None,
    )
    assert result.status == "warn"


# ---------------------------------------------------------------------------
# Range values (min/max pair on a single row)
# ---------------------------------------------------------------------------


def test_range_value_intersects_window_is_ok() -> None:
    """If the [min, max] of the value falls partly inside the range, ok."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=None,
        value_min=10.0,
        value_max=20.0,
    )
    assert result.status == "ok"


def test_range_value_fully_outside_fails() -> None:
    """If both value bounds are above the physical max, fail."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=None,
        value_min=40.0,
        value_max=50.0,
    )
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_value_returns_warn() -> None:
    """No value yet (e.g. expression or text) → warn even if range is set."""

    result = evaluate_valid_range(
        property_name="density",
        valid_range_min=0.1,
        valid_range_max=30.0,
        value_scalar=None,
        value_min=None,
        value_max=None,
    )
    assert result.status == "warn"


@pytest.mark.parametrize(
    ("name", "min_v", "max_v", "value"),
    [
        ("density", 0.1, 30.0, 10.97),
        ("lattice_constant", 0.5, 20.0, 5.47),
        ("bulk_modulus", 1.0, 1000.0, 200.0),
    ],
)
def test_nominal_properties_pass(name: str, min_v: float, max_v: float, value: float) -> None:
    """Sanity: nominal values for physical properties stay ok."""

    result = evaluate_valid_range(
        property_name=name,
        valid_range_min=min_v,
        valid_range_max=max_v,
        value_scalar=value,
        value_min=None,
        value_max=None,
    )
    assert result.status == "ok", (
        f"{name} value {value} unexpectedly failed against [{min_v}, {max_v}]: "
        f"{result.reason}"
    )


@pytest.mark.parametrize(
    ("name", "min_v", "max_v", "value", "expected_fragment"),
    [
        ("density", 0.1, 30.0, 0.05, "density=0.05"),
        ("lattice_constant", 0.5, 20.0, 0.3, "lattice_constant=0.3"),
        ("bulk_modulus", 1.0, 1000.0, 0.5, "bulk_modulus=0.5"),
    ],
)
def test_invalid_values_fail(
    name: str, min_v: float, max_v: float, value: float, expected_fragment: str
) -> None:
    """AC-10 parametrized: unphysical values get fail + reason."""

    result = evaluate_valid_range(
        property_name=name,
        valid_range_min=min_v,
        valid_range_max=max_v,
        value_scalar=value,
        value_min=None,
        value_max=None,
    )
    assert result.status == "fail"
    assert result.reason is not None
    assert expected_fragment in result.reason
