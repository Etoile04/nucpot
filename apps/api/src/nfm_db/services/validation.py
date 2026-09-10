"""G1-D validity_check service (NFM-4550, spec §8.4, AC-10).

Computes a per-property ``validity_check`` JSON payload
``{status: "ok"|"warn"|"fail", reason: str|null}`` for each
``property_measurements`` row at落库 time, by comparing the measured
value(s) against the property_type's physical ``valid_range_min`` /
``valid_range_max`` (the column pair is added in migration 086).

Semantics
---------

* ``ok``    — value is inside the configured range (or only one bound is
  configured and the value is on the right side of it).
* ``warn``  — no range is configured for this property type, OR no
  numeric value is available (e.g. the row carries an expression /
  text value). The mapper persists ``status="warn"`` + a non-null
  ``reason`` so the row is auditable but not auto-rejected.
* ``fail``  — value is outside the configured range. The mapper
  overrides ``review_status`` to ``"invalid"`` on this row.

The mapper integration lives in
``nfm_db.services.extraction_to_db_mapper``. The DB column is
``property_measurements.validity_check`` (JSONB), added by migration
086 alongside the ``property_types.valid_range_min`` /
``property_types.valid_range_max`` pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ValidityStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class ValidityResult:
    """Outcome of evaluating a measurement against its property_type range.

    ``reason`` is ``None`` for ``ok`` and a non-null human-readable
    string for ``warn`` and ``fail`` so the校对 page can render it
    inline (spec §8.4 "domain_expert 可改回 disputed / 确认无效").
    """

    status: ValidityStatus
    reason: str | None


def evaluate_valid_range(
    *,
    property_name: str,
    valid_range_min: float | None,
    valid_range_max: float | None,
    value_scalar: float | None,
    value_min: float | None,
    value_max: float | None,
    unit: str | None = None,
) -> ValidityResult:
    """Evaluate a measurement's value(s) against its property_type range.

    The function is pure (no DB / network) so it can be unit-tested
    without a session and reused by other write paths (PATCH-time
    re-validation per spec §8.2 step 4).

    ``property_name`` is interpolated into the ``fail``/``warn`` reason
    so the校对 page can render
    ``"density=0.05 outside physical range [0.1, 30.0] g/cm³"`` rather
    than a value-only string.

    Decision table:

    * Both bounds unset         → ``warn`` ("no range configured")
    * No numeric value          → ``warn`` ("no numeric value to check")
    * Scalar present            → check scalar against [min, max]
    * Range [vmin, vmax] present→ check intersection with [min, max]:
        fully outside → ``fail``
        partially inside / fully inside → ``ok``
    * Scalar + range present    → scalar wins (range is treated as a
      secondary signal; the scalar is the published measurement).

    Bounds are inclusive — a value exactly on the boundary is ``ok``.
    """
    # --- (1) No range configured at all → warn ---
    if valid_range_min is None and valid_range_max is None:
        return ValidityResult(
            status="warn",
            reason=f"no valid_range configured for property_type '{property_name}'",
        )

    # --- (2) No numeric value → warn (cannot decide) ---
    if value_scalar is None and value_min is None and value_max is None:
        return ValidityResult(
            status="warn",
            reason=f"no numeric value to validate for property '{property_name}'",
        )

    # --- (3) Scalar value takes precedence over min/max pair ---
    if value_scalar is not None:
        return _check_scalar_against_range(
            property_name=property_name,
            value=value_scalar,
            lo=valid_range_min,
            hi=valid_range_max,
            unit=unit,
        )

    # --- (4) Range [vmin, vmax] intersect check ---
    return _check_range_intersection(
        property_name=property_name,
        vmin=value_min if value_min is not None else value_max,
        vmax=value_max if value_max is not None else value_min,
        lo=valid_range_min,
        hi=valid_range_max,
        unit=unit,
    )


def _format_value_with_unit(value: float, unit: str | None) -> str:
    return f"{value:g}" + (f" {unit}" if unit else "")


def _format_range_window(lo: float | None, hi: float | None) -> str:
    """Render the range as ``[lo, hi]`` / ``[lo, ∞]`` / ``[-∞, hi]``."""

    lo_s = f"{lo:g}" if lo is not None else "-∞"
    hi_s = f"{hi:g}" if hi is not None else "∞"
    return f"[{lo_s}, {hi_s}]"


def _check_scalar_against_range(
    *,
    property_name: str,
    value: float,
    lo: float | None,
    hi: float | None,
    unit: str | None,
) -> ValidityResult:
    """One-sided / two-sided bound check on a scalar value."""

    if lo is not None and value < lo:
        return ValidityResult(
            status="fail",
            reason=(
                f"{property_name}={_format_value_with_unit(value, unit)} "
                f"outside physical range {_format_range_window(lo, hi)}"
                + (f" {unit}" if unit else "")
            ),
        )
    if hi is not None and value > hi:
        return ValidityResult(
            status="fail",
            reason=(
                f"{property_name}={_format_value_with_unit(value, unit)} "
                f"outside physical range {_format_range_window(lo, hi)}"
                + (f" {unit}" if unit else "")
            ),
        )
    return ValidityResult(status="ok", reason=None)


def _check_range_intersection(
    *,
    property_name: str,
    vmin: float | None,
    vmax: float | None,
    lo: float | None,
    hi: float | None,
    unit: str | None,
) -> ValidityResult:
    """Fail when the value's [vmin, vmax] is fully outside [lo, hi].

    If only one bound is given on either side, treat it as a one-sided
    check. Partial overlap ⇒ ok.
    """

    effective_lo = vmin if vmin is not None else vmax
    effective_hi = vmax if vmax is not None else vmin
    if effective_lo is None or effective_hi is None:
        return ValidityResult(status="warn", reason="no numeric value to validate")

    if lo is not None and effective_hi < lo:
        return ValidityResult(
            status="fail",
            reason=(
                f"{property_name} range [{_format_value_with_unit(effective_lo, unit)}, "
                f"{_format_value_with_unit(effective_hi, unit)}] entirely below physical "
                f"minimum {lo:g}"
                + (f" {unit}" if unit else "")
            ),
        )
    if hi is not None and effective_lo > hi:
        return ValidityResult(
            status="fail",
            reason=(
                f"{property_name} range [{_format_value_with_unit(effective_lo, unit)}, "
                f"{_format_value_with_unit(effective_hi, unit)}] entirely above physical "
                f"maximum {hi:g}"
                + (f" {unit}" if unit else "")
            ),
        )
    return ValidityResult(status="ok", reason=None)
