"""Aggregates-only feature engineering for EnergyPredictor v3.1 (NFM-3988 / NFM-3958).

Per the NFM-3958 PREREG §3-§4 (locked) and the NFM-3955 RD-3 root cause, v3.1 strips
the 8 pairwise/variance stratum from v3.0's 20D feature set. The 12D aggregates-only
stratum is forced to share information across element systems via the U-solvent
overlap structure rather than via solute-set fingerprints.

Drop (8) — pairwise / variance / vec:
  - vec, hr_valence_diff, dg_en_radius_distance, max_pair_en_diff,
    en_variance, volume_variance, d_electron_variance, bulk_modulus_variance

Keep (12) — composition-weighted scalar-per-element aggregates:
  - 7 Miedema-style aggregates from compute_ml_features (v1.0 baseline):
      mo_equivalent, allen_chi_diff, config_entropy, bv_ratio,
      u_density, mixing_enthalpy, lattice_distortion
  - 5 element-level aggregates from energy_features_v11._V11_CALCULATORS:
      avg_allen_chi, avg_atomic_volume, avg_d_electron,
      avg_work_function, avg_bulk_modulus

References:
  - NFM-3955 RD-3 anomaly review (grouped R² collapse root cause)
  - NFM-3958 PREREG §3-§4 (locked 12D list and CV protocol)
  - NFM-1756 GroupKFold protocol for PhaseClassifier v2.0
"""

from __future__ import annotations

import logging
from typing import Any

from nfm_db.ml.energy_features_v11 import (
    calculate_avg_allen_chi,
    calculate_avg_atomic_volume,
    calculate_avg_bulk_modulus,
    calculate_avg_d_electron,
    calculate_avg_work_function,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locked 12D feature name list — NFM-3958 PREREG §4 (no deviation permitted).
# AC-A3: literal and locked to this order.
# ---------------------------------------------------------------------------

ENERGY_V31_FEATURE_NAMES: list[str] = [
    # 7 Miedema-style aggregates from compute_ml_features (v1.0 baseline)
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
    # 5 element-level aggregates from energy_features_v11._V11_CALCULATORS
    "avg_allen_chi",
    "avg_atomic_volume",
    "avg_d_electron",
    "avg_work_function",
    "avg_bulk_modulus",
]

# Dropped 8D stratum (PREREG §3 — for documentation & sanity check in tests).
# NOT exposed in ENERGY_V31_FEATURE_NAMES; kept here as a literal set so
# tests can assert that none of these keys leak into the v3.1 feature dict.
V31_DROPPED_FEATURE_NAMES: frozenset[str] = frozenset({
    "vec",
    "hr_valence_diff",
    "dg_en_radius_distance",
    "max_pair_en_diff",
    "en_variance",
    "volume_variance",
    "d_electron_variance",
    "bulk_modulus_variance",
})

assert len(ENERGY_V31_FEATURE_NAMES) == 12, (
    f"v3.1 feature list is locked at 12D; got {len(ENERGY_V31_FEATURE_NAMES)}"
)
assert not (set(ENERGY_V31_FEATURE_NAMES) & V31_DROPPED_FEATURE_NAMES), (
    "v3.1 feature list contains a dropped feature: "
    f"{set(ENERGY_V31_FEATURE_NAMES) & V31_DROPPED_FEATURE_NAMES}"
)


def _safe_avg(composition: dict[str, float], calc) -> float:
    """Compute an aggregate feature, returning 0.0 on lookup failure.

    The v1.1 calculators return 0.0 when no element in the composition has
    lookup data; we keep that contract for v3.1 to avoid NaNs propagating
    into XGBoost.
    """
    try:
        val = calc(composition)
        if val is None:
            return 0.0
        return float(val)
    except Exception:
        logger.exception("Feature calculator %s raised", calc.__name__)
        return 0.0


def compute_energy_features_v31(composition: dict[str, float]) -> dict[str, float]:
    """Compute the locked 12D aggregates-only feature vector for v3.1.

    Combines the 7 Miedema-style aggregates from ``compute_ml_features`` with
    the 5 element-level aggregates from ``energy_features_v11``. The
    pairwise/variance stratum (8 features) is deliberately omitted per the
    NFM-3958 PREREG.

    Args:
        composition: Element name to atomic percent or atomic fraction mapping.

    Returns:
        Dictionary with exactly 12 keys matching ``ENERGY_V31_FEATURE_NAMES``,
        in the locked order. No keys from ``V31_DROPPED_FEATURE_NAMES`` are
        present.
    """
    from nfm_db.ml.feature_engineering import compute_ml_features

    base = compute_ml_features(composition)
    result: dict[str, float] = {}

    # 7 Miedema aggregates — taken verbatim from the v1.0 baseline dict.
    for name in (
        "mo_equivalent",
        "allen_chi_diff",
        "config_entropy",
        "bv_ratio",
        "u_density",
        "mixing_enthalpy",
        "lattice_distortion",
    ):
        result[name] = float(base.get(name, 0.0) or 0.0)

    # 5 element-level aggregates — computed independently to avoid coupling
    # to the v1.1 dict mutation order.
    result["avg_allen_chi"] = _safe_avg(composition, calculate_avg_allen_chi)
    result["avg_atomic_volume"] = _safe_avg(composition, calculate_avg_atomic_volume)
    result["avg_d_electron"] = _safe_avg(composition, calculate_avg_d_electron)
    result["avg_work_function"] = _safe_avg(composition, calculate_avg_work_function)
    result["avg_bulk_modulus"] = _safe_avg(composition, calculate_avg_bulk_modulus)

    # Hard guarantee: no dropped-feature key leaks through.
    for dropped in V31_DROPPED_FEATURE_NAMES:
        if dropped in result:
            result.pop(dropped, None)

    # Final ordering and length contract.
    return {name: result.get(name, 0.0) for name in ENERGY_V31_FEATURE_NAMES}


__all__ = [
    "ENERGY_V31_FEATURE_NAMES",
    "V31_DROPPED_FEATURE_NAMES",
    "compute_energy_features_v31",
]
