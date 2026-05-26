"""A-F grading engine for property verification.

Grades based on relative error against reference values:
  A ≤ 1%, B ≤ 3%, C ≤ 5%, D ≤ 10%, F > 10%
"""

import logging

from ..config import settings

logger = logging.getLogger(__name__)

# Grade severity for ranking
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}


def grade_property(
    computed: float,
    reference: float,
    thresholds: dict[str, float] | None = None,
) -> str:
    """Grade a computed value against a reference.

    Args:
        computed: The computed property value.
        reference: The reference (experimental/DFT) value.
        thresholds: Override default grade thresholds.
            Keys: 'A', 'B', 'C', 'D' with max relative error values.

    Returns:
        Grade letter: A, B, C, D, or F.
    """
    if thresholds is None:
        thresholds = {
            "A": settings.GRADE_A_THRESHOLD,
            "B": settings.GRADE_B_THRESHOLD,
            "C": settings.GRADE_C_THRESHOLD,
            "D": settings.GRADE_D_THRESHOLD,
        }

    # Handle zero reference edge case
    if abs(reference) < 1e-12:
        if abs(computed) < 1e-12:
            return "A"
        logger.warning("Zero reference with non-zero computed: %.6f", computed)
        return "F"

    rel_error = abs(computed - reference) / abs(reference)

    if rel_error <= thresholds["A"]:
        return "A"
    elif rel_error <= thresholds["B"]:
        return "B"
    elif rel_error <= thresholds["C"]:
        return "C"
    elif rel_error <= thresholds["D"]:
        return "D"
    else:
        return "F"


def compute_relative_error(computed: float, reference: float) -> float | None:
    """Compute relative error between computed and reference values."""
    if abs(reference) < 1e-12:
        return None
    return abs(computed - reference) / abs(reference)


def compute_overall_grade(grades: list[str]) -> str:
    """Compute overall grade as the worst individual grade.

    Args:
        grades: list of grade letters.

    Returns:
        Worst grade letter, or "N/A" if empty.
    """
    if not grades:
        return "N/A"

    worst = max(grades, key=lambda g: GRADE_ORDER.get(g, 99))
    return worst


def grade_all_properties(
    computed: dict[str, dict],
    reference_map: dict[str, float],
) -> tuple[dict[str, str], str]:
    """Grade all computed properties against reference values.

    Args:
        computed: dict of property_name -> result dict (with 'value' key).
        reference_map: dict of property_name -> reference float value.

    Returns:
        Tuple of (per-property grades dict, overall grade).
    """
    grades = {}
    for prop_name, ref_value in reference_map.items():
        if prop_name not in computed:
            logger.warning("Property %s not in computed results", prop_name)
            continue

        result = computed[prop_name]
        if "error" in result:
            grades[prop_name] = "F"
            continue

        comp_value = result.get("value")
        if comp_value is None:
            grades[prop_name] = "F"
            continue

        grades[prop_name] = grade_property(comp_value, ref_value)

    overall = compute_overall_grade(list(grades.values()))
    return grades, overall
