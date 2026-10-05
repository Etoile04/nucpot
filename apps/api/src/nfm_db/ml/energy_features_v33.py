"""24D energy feature vocabulary for the v3.3 confirmatory run (NFM-5305).

Extends the frozen v3.0/v1.1 20D vocabulary (``ENERGY_V11_FEATURE_NAMES`` in
``energy_features_v11``, frozen under NFM-5060) with the four pairwise
additions locked in the NFM-5305 PREREG (``[PREREG-SUBMITTED]`` comment
40d2e0b5-1bd8-4eda-ae12-1736c01a9d1a; ``[PREREG-APPROVED]`` NDE comment
d6e1b268-8b4d-4856-8178-24dca1f28e8b, 2026-10-05T01:33:00.374Z):

21. ``min_pair_en_diff``            - min pairwise |Δ Allen χ|
22. ``avg_pair_en_diff``            - composition-weighted mean pairwise |Δ Allen χ|
23. ``max_pair_bulk_modulus_diff``  - max pairwise |Δ bulk modulus|
24. ``max_pair_work_function_diff`` - max pairwise |Δ work function|

All four are pure functions of composition over the in-repo element tables
and mirror the existing v1.1 pairwise helper semantics (``energy_features_v11``):
pairs are unordered, span every element present in the lookup (U included:
χ_U = 1.226, B_U = 113.0 GPa, φ_U = 3.63 eV), and compositions with fewer
than two lookup-covered elements yield 0.0. Known table gaps: Mn and Sn have
no bulk-modulus entries, so the bulk-modulus pairwise feature returns 0.0 for
U-Mn / U-Sn binaries under the <2-element rule (same fallback behavior as the
existing v1.1 pairwise features).

No post-hoc feature selection: all four additions stay in the Arm B vector
regardless of measured importance, per the locked prereg. The 20D base is
imported verbatim from the frozen v11 module — this file never edits or
recomputes it.
"""

from nfm_db.ml.energy_features_v11 import (
    _WORK_FUNCTION,
    ENERGY_V11_FEATURE_NAMES,
    _get_lookups,
    _normalize,
    _pairwise_max,
    compute_energy_features_v11,
)

V33_ADDITION_FEATURE_NAMES = [
    "min_pair_en_diff",
    "avg_pair_en_diff",
    "max_pair_bulk_modulus_diff",
    "max_pair_work_function_diff",
]

ENERGY_V33_FEATURE_NAMES = [
    *ENERGY_V11_FEATURE_NAMES,
    *V33_ADDITION_FEATURE_NAMES,
]


def _pairwise_min(
    composition: dict[str, float],
    lookup: dict[str, float],
) -> float:
    """Compute min pairwise absolute difference across all element pairs.

    Mirrors ``_pairwise_max`` in ``energy_features_v11``: pairs are unordered
    and span only elements present in ``lookup``; fewer than two covered
    elements yields 0.0.
    """
    norm = _normalize(composition)
    elements = [el for el in norm if el in lookup]
    if len(elements) < 2:
        return 0.0
    min_val: float | None = None
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            val = abs(lookup[elements[i]] - lookup[elements[j]])
            if min_val is None or val < min_val:
                min_val = val
    return 0.0 if min_val is None else min_val


def _pairwise_avg_abs_diff(
    composition: dict[str, float],
    lookup: dict[str, float],
) -> float:
    """Compute weighted average pairwise absolute difference.

    Mirrors the ``x_i * x_j`` weighting of ``_pairwise_avg_distance`` in
    ``energy_features_v11``: each pair (i, j) contributes
    ``w = x_i * x_j`` toward the mean of ``|v_i - v_j|``. Fewer than two
    covered elements yields 0.0.
    """
    norm = _normalize(composition)
    elements = [el for el in norm if el in lookup]
    if len(elements) < 2:
        return 0.0
    diff_sum = 0.0
    weight_sum = 0.0
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            diff = abs(lookup[elements[i]] - lookup[elements[j]])
            w = norm[elements[i]] * norm[elements[j]]
            diff_sum += w * diff
            weight_sum += w
    return diff_sum / weight_sum if weight_sum > 0 else 0.0


def compute_min_pair_en_diff(composition: dict[str, float]) -> float:
    """Minimum pairwise |Δ Allen χ| across element pairs (U included)."""
    allen_chi = _get_lookups()[0]
    return _pairwise_min(composition, allen_chi)


def compute_avg_pair_en_diff(composition: dict[str, float]) -> float:
    """Composition-weighted mean pairwise |Δ Allen χ| (U included)."""
    allen_chi = _get_lookups()[0]
    return _pairwise_avg_abs_diff(composition, allen_chi)


def compute_max_pair_bulk_modulus_diff(composition: dict[str, float]) -> float:
    """Maximum pairwise |Δ bulk modulus| across element pairs (U included)."""
    bulk_modulus = _get_lookups()[3]
    return _pairwise_max(composition, bulk_modulus)


def compute_max_pair_work_function_diff(composition: dict[str, float]) -> float:
    """Maximum pairwise |Δ work function| across element pairs (U included)."""
    return _pairwise_max(composition, _WORK_FUNCTION)


def compute_energy_features_v33(composition: dict[str, float]) -> dict[str, float]:
    """Compute the locked 24D v3.3 feature vector for one composition.

    Returns a new dict: the frozen 20D v1.1 vocabulary verbatim plus the four
    NFM-5305 pairwise additions, in the prereg-locked order.
    """
    base = compute_energy_features_v11(composition)
    return {
        **base,
        "min_pair_en_diff": compute_min_pair_en_diff(composition),
        "avg_pair_en_diff": compute_avg_pair_en_diff(composition),
        "max_pair_bulk_modulus_diff": compute_max_pair_bulk_modulus_diff(
            composition,
        ),
        "max_pair_work_function_diff": compute_max_pair_work_function_diff(
            composition,
        ),
    }
