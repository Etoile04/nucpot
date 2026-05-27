"""A-F grading engine for verification results.

Grades based on relative error against reference values:
  A ≤ 1%, B ≤ 3%, C ≤ 5%, D ≤ 10%, F > 10%
"""

from __future__ import annotations

import logging
from typing import Any

from verify_service.config import get_settings

logger = logging.getLogger(__name__)


def _get_thresholds() -> dict[str, float]:
    settings = get_settings()
    return {
        "A": settings.GRADING_THRESHOLD_A,
        "B": settings.GRADING_THRESHOLD_B,
        "C": settings.GRADING_THRESHOLD_C,
        "D": settings.GRADING_THRESHOLD_D,
    }


def relative_error(computed: float, reference: float) -> float:
    """Compute relative error |computed - reference| / |reference|.

    Returns 0 if both are zero. Returns infinity if reference is zero
    but computed is not.
    """
    if reference == 0:
        return 0.0 if computed == 0 else float("inf")
    return abs(computed - reference) / abs(reference)


def grade_property(
    computed: float,
    reference: float,
    thresholds: dict[str, float] | None = None,
) -> tuple[str, float]:
    """Grade a single computed property against reference.

    Returns (grade, relative_error).
    """
    thresholds = thresholds or _get_thresholds()
    rel_err = relative_error(computed, reference)

    for grade, threshold in thresholds.items():
        if rel_err <= threshold:
            return grade, rel_err

    return "F", rel_err


def grade_results(
    computed: dict[str, Any],
    reference: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Grade all computed properties against reference values.

    Args:
        computed: {"lattice_constant": {"value": 3.45, "unit": "angstrom"}, ...}
        reference: {"lattice_constant": {"value": 3.47, "unit": "angstrom"}, ...}

    Returns:
        Graded results dict with grade, errors, and overall grade.
    """
    graded = {}
    grades = []

    for prop_name, comp_result in computed.items():
        if "error" in comp_result:
            graded[prop_name] = {"error": comp_result["error"], "grade": "F"}
            grades.append("F")
            continue

        comp_val = comp_result.get("value")
        ref_entry = reference.get(prop_name)
        if comp_val is None or ref_entry is None:
            graded[prop_name] = {**comp_result, "grade": None, "reference": None}
            continue

        ref_val = ref_entry.get("value") if isinstance(ref_entry, dict) else ref_entry
        if ref_val is None:
            graded[prop_name] = {**comp_result, "grade": None, "reference": None}
            continue

        grade, rel_err = grade_property(comp_val, ref_val)
        abs_err = abs(comp_val - ref_val)

        graded[prop_name] = {
            **comp_result,
            "reference": ref_val,
            "absolute_error": round(abs_err, 6),
            "relative_error": round(rel_err, 6),
            "grade": grade,
        }
        grades.append(grade)

    overall = _worst_grade(grades) if grades else None
    return {"results": graded, "overall_grade": overall}


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}


def _worst_grade(grades: list[str]) -> str:
    """Return the worst grade from a list."""
    return max(grades, key=lambda g: _GRADE_ORDER.get(g, 99))
