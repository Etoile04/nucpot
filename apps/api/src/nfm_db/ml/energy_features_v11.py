"""Expanded feature engineering for EnergyPredictor v1.1 (NFM-1802).

Adds element-resolved electronic structure descriptors and pairwise interaction
terms on top of the v1.0 8D Miedema-style baseline (R²=0.8293).

v1.1 additions (12 new features → 20D total):
  - Element-level: avg_allen_chi, avg_atomic_volume, avg_d_electron,
    avg_work_function, avg_bulk_modulus
  - Pairwise interaction: hr_valence_diff, dg_en_radius_distance,
    max_pair_en_diff, en_variance, volume_variance, d_electron_variance,
    bulk_modulus_variance

All features are pure functions of ``composition: dict[str, float]`` matching
the v1.0 interface in ``feature_engineering.py``.

References:
    - Hume-Rothery rules for solid solution formation
    - Darken-Gurry plots (EN vs atomic radius)
    - L.C. Allen, J. Am. Chem. Soc. 111, 9003 (1989)
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# D-electron count (number of d electrons in neutral atom)
# Source: standard electron configurations
# ---------------------------------------------------------------------------

D_ELECTRON_COUNT: frozenset[tuple[str, int]] = frozenset({
    ("Sc", 1), ("Ti", 2), ("V", 3), ("Cr", 5), ("Mn", 5),
    ("Fe", 6), ("Co", 7), ("Ni", 8), ("Cu", 10), ("Zn", 10),
    ("Y", 1), ("Zr", 2), ("Nb", 4), ("Mo", 5), ("Tc", 6),
    ("Ru", 7), ("Rh", 8), ("Pd", 10), ("Ag", 10), ("Cd", 10),
    ("Hf", 2), ("Ta", 3), ("W", 4), ("Re", 5), ("Os", 6),
    ("Ir", 7), ("Pt", 9), ("Au", 10),
    # Actinides (5f/6d electrons)
    ("Th", 0), ("Pa", 1), ("U", 2), ("Np", 3), ("Pu", 4), ("Am", 5),
    # Non-transition metals (0 d-electrons)
    ("H", 0), ("Li", 0), ("Be", 0), ("B", 0), ("C", 0),
    ("N", 0), ("O", 0), ("F", 0), ("Na", 0), ("Mg", 0),
    ("Al", 0), ("Si", 0), ("P", 0), ("S", 0), ("Cl", 0),
    ("K", 0), ("Ca", 0), ("Ga", 0), ("Ge", 0), ("As", 0),
    ("Se", 0), ("Br", 0), ("Rb", 0), ("Sr", 0),
    ("In", 0), ("Sn", 0), ("Sb", 0), ("Te", 0), ("I", 0),
    ("Cs", 0), ("Ba", 0),
    ("La", 0), ("Ce", 1), ("Pr", 2), ("Nd", 3),
    ("Sm", 5), ("Eu", 6), ("Gd", 7), ("Tb", 8),
    ("Dy", 9), ("Ho", 10), ("Er", 11), ("Tm", 12), ("Yb", 13), ("Lu", 14),
    ("Pb", 0), ("Bi", 0),
})

# ---------------------------------------------------------------------------
# Work function (eV) — polycrystalline values
# Source: Michaelson, J. Appl. Phys. 48, 4729 (1977);
#          CRC Handbook of Chemistry and Physics
# ---------------------------------------------------------------------------

WORK_FUNCTION_EV: frozenset[tuple[str, float]] = frozenset({
    ("U", 3.63), ("Th", 3.38), ("Pa", 3.5), ("Np", 3.4), ("Pu", 3.2),
    ("Ti", 4.33), ("V", 4.44), ("Cr", 4.50), ("Mn", 4.10),
    ("Fe", 4.67), ("Co", 5.00), ("Ni", 5.15), ("Cu", 4.65),
    ("Zr", 4.05), ("Nb", 4.30), ("Mo", 4.60), ("Tc", 4.6),
    ("Ru", 4.71), ("Rh", 4.80), ("Pd", 5.12), ("Ag", 4.26),
    ("Hf", 3.90), ("Ta", 4.25), ("W", 4.55), ("Re", 4.72),
    ("Os", 4.83), ("Ir", 5.27), ("Pt", 5.65), ("Au", 5.10),
    ("Al", 4.28), ("Si", 4.85), ("Ga", 4.32), ("Ge", 4.80),
    ("Sn", 4.42), ("Pb", 4.25), ("Bi", 4.34),
    ("La", 3.50), ("Ce", 2.90), ("Nd", 3.20), ("Gd", 3.10),
    ("Dy", 3.00), ("Er", 3.00), ("Yb", 2.60), ("Lu", 3.30),
    ("Y", 3.10), ("Sc", 3.50),
})


# ---------------------------------------------------------------------------
# Lookup caches (populated once on first use)
# ---------------------------------------------------------------------------

_ALLEN_CHI: dict[str, float] | None = None
_ATOMIC_RADIUS: dict[str, float] | None = None
_ATOMIC_VOLUME: dict[str, float] | None = None
_BULK_MODULUS: dict[str, float] | None = None
_D_ELECTRON: dict[str, float] = {el: float(n) for el, n in D_ELECTRON_COUNT}
_WORK_FUNCTION: dict[str, float] = dict(WORK_FUNCTION_EV)


def _get_lookups() -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
]:
    """Lazy-initialize and return all lookup dicts."""
    global _ALLEN_CHI, _ATOMIC_RADIUS, _ATOMIC_VOLUME, _BULK_MODULUS
    if _ALLEN_CHI is None:
        from nfm_db.ml.feature_engineering import (
            ALLEN_ELECTRONEGATIVITY,
            ATOMIC_RADIUS,
            ATOMIC_VOLUME_CM3_PER_MOL,
            BULK_MODULUS,
        )
        _ALLEN_CHI = dict(ALLEN_ELECTRONEGATIVITY)
        _ATOMIC_RADIUS = dict(ATOMIC_RADIUS)
        _ATOMIC_VOLUME = dict(ATOMIC_VOLUME_CM3_PER_MOL)
        _BULK_MODULUS = dict(BULK_MODULUS)
    return _ALLEN_CHI, _ATOMIC_RADIUS, _ATOMIC_VOLUME, _BULK_MODULUS


def _normalize(composition: dict[str, float]) -> dict[str, float]:
    """Normalize composition to atomic fractions summing to 1.0."""
    total = sum(composition.values())
    if total <= 0:
        return {}
    return {el: frac / total for el, frac in composition.items()}


def _weighted_avg(
    composition: dict[str, float],
    lookup: dict[str, float],
) -> float:
    """Compute composition-weighted average of a property.

    Elements without lookup data are excluded; their fraction is
    redistributed proportionally (same approach as v1.0 features).
    """
    norm = _normalize(composition)
    if not norm:
        return 0.0
    weighted_sum = 0.0
    known_frac = 0.0
    for el, frac in norm.items():
        if el in lookup:
            weighted_sum += frac * lookup[el]
            known_frac += frac
    return weighted_sum / known_frac if known_frac > 0 else 0.0


def _weighted_variance(
    composition: dict[str, float],
    lookup: dict[str, float],
) -> float:
    """Compute composition-weighted variance of a property.

    Var(X) = Σ x_i × (X_i - X̄)² where X̄ is the weighted average.
    """
    norm = _normalize(composition)
    if not norm:
        return 0.0
    avg = _weighted_avg(composition, lookup)
    var_sum = 0.0
    known_frac = 0.0
    for el, frac in norm.items():
        if el in lookup:
            var_sum += frac * (lookup[el] - avg) ** 2
            known_frac += frac
    return var_sum / known_frac if known_frac > 0 else 0.0


def _pairwise_max(
    composition: dict[str, float],
    lookup: dict[str, float],
) -> float:
    """Compute max pairwise absolute difference across all element pairs."""
    norm = _normalize(composition)
    elements = [el for el in norm if el in lookup]
    if len(elements) < 2:
        return 0.0
    max_val = 0.0
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            val = abs(lookup[elements[i]] - lookup[elements[j]])
            max_val = max(max_val, val)
    return max_val


def _pairwise_avg_distance(
    composition: dict[str, float],
    lookup_a: dict[str, float],
    lookup_b: dict[str, float],
) -> float:
    """Compute weighted average pairwise 2D distance.

    For each pair (i, j): d = sqrt((A_i - A_j)² + (B_i - B_j)²).
    Weighted by x_i * x_j.
    """
    norm = _normalize(composition)
    elements = [el for el in norm if el in lookup_a and el in lookup_b]
    if len(elements) < 2:
        return 0.0
    dist_sum = 0.0
    weight_sum = 0.0
    for i in range(len(elements)):
        for j in range(i + 1, len(elements)):
            el_a, el_b = elements[i], elements[j]
            da = lookup_a[el_a] - lookup_a[el_b]
            db = lookup_b[el_a] - lookup_b[el_b]
            dist = math.sqrt(da * da + db * db)
            w = norm[el_a] * norm[el_b]
            dist_sum += w * dist
            weight_sum += w
    return dist_sum / weight_sum if weight_sum > 0 else 0.0


# ---------------------------------------------------------------------------
# v1.1 Feature Calculators
# ---------------------------------------------------------------------------


def calculate_avg_allen_chi(composition: dict[str, float]) -> float:
    """Weighted average Allen electronegativity."""
    allen_chi, _, _, _ = _get_lookups()
    return _weighted_avg(composition, allen_chi)


def calculate_avg_atomic_volume(composition: dict[str, float]) -> float:
    """Weighted average atomic volume (cm³/mol)."""
    _, _, vol, _ = _get_lookups()
    return _weighted_avg(composition, vol)


def calculate_avg_d_electron(composition: dict[str, float]) -> float:
    """Weighted average d-electron count."""
    return _weighted_avg(composition, _D_ELECTRON)


def calculate_avg_work_function(composition: dict[str, float]) -> float:
    """Weighted average work function (eV)."""
    return _weighted_avg(composition, _WORK_FUNCTION)


def calculate_avg_bulk_modulus(composition: dict[str, float]) -> float:
    """Weighted average bulk modulus (GPa)."""
    _, _, _, bm = _get_lookups()
    return _weighted_avg(composition, bm)


def calculate_hr_valence_diff(composition: dict[str, float]) -> float:
    """Hume-Rothery valence electron difference: max |VEC_i - VEC_j|.

    Large values indicate strong electronic contrast between solute
    elements, affecting solid solution stability.
    """
    from nfm_db.ml.feature_engineering import VALENCE_ELECTRON_COUNT
    vec_lookup = dict(VALENCE_ELECTRON_COUNT)
    return _pairwise_max(composition, vec_lookup)


def calculate_dg_en_radius_distance(composition: dict[str, float]) -> float:
    """Darken-Gurry average 2D distance: EN vs atomic radius.

    For each pair (i, j): d = sqrt((χ_i - χ_j)² + (r_i - r_j)²).
    Returns the composition-weighted average of all pair distances.
    """
    allen_chi, radius, _, _ = _get_lookups()
    return _pairwise_avg_distance(composition, allen_chi, radius)


def calculate_max_pair_en_diff(composition: dict[str, float]) -> float:
    """Maximum pairwise Allen electronegativity difference.

    Captures the most extreme electronegativity mismatch in the alloy.
    """
    allen_chi, _, _, _ = _get_lookups()
    return _pairwise_max(composition, allen_chi)


def calculate_en_variance(composition: dict[str, float]) -> float:
    """Variance of per-element Allen electronegativities.

    High variance indicates diverse chemical environments.
    """
    allen_chi, _, _, _ = _get_lookups()
    return _weighted_variance(composition, allen_chi)


def calculate_volume_variance(composition: dict[str, float]) -> float:
    """Variance of per-element atomic volumes.

    High variance indicates significant size mismatch (beyond δ).
    """
    _, _, vol, _ = _get_lookups()
    return _weighted_variance(composition, vol)


def calculate_d_electron_variance(composition: dict[str, float]) -> float:
    """Variance of per-element d-electron counts.

    Captures d-band filling diversity, critical for bonding character.
    """
    return _weighted_variance(composition, _D_ELECTRON)


def calculate_bulk_modulus_variance(composition: dict[str, float]) -> float:
    """Variance of per-element bulk moduli.

    High variance indicates elastic stiffness contrast between elements.
    """
    _, _, _, bm = _get_lookups()
    return _weighted_variance(composition, bm)


# ---------------------------------------------------------------------------
# v1.1 Feature Registry
# ---------------------------------------------------------------------------

V11_ADDITIONAL_FEATURE_NAMES: list[str] = [
    "avg_allen_chi",
    "avg_atomic_volume",
    "avg_d_electron",
    "avg_work_function",
    "avg_bulk_modulus",
    "hr_valence_diff",
    "dg_en_radius_distance",
    "max_pair_en_diff",
    "en_variance",
    "volume_variance",
    "d_electron_variance",
    "bulk_modulus_variance",
]

_V11_CALCULATORS: list[tuple[str, object]] = [
    ("avg_allen_chi", calculate_avg_allen_chi),
    ("avg_atomic_volume", calculate_avg_atomic_volume),
    ("avg_d_electron", calculate_avg_d_electron),
    ("avg_work_function", calculate_avg_work_function),
    ("avg_bulk_modulus", calculate_avg_bulk_modulus),
    ("hr_valence_diff", calculate_hr_valence_diff),
    ("dg_en_radius_distance", calculate_dg_en_radius_distance),
    ("max_pair_en_diff", calculate_max_pair_en_diff),
    ("en_variance", calculate_en_variance),
    ("volume_variance", calculate_volume_variance),
    ("d_electron_variance", calculate_d_electron_variance),
    ("bulk_modulus_variance", calculate_bulk_modulus_variance),
]

# Full 20D feature names: 8 from v1.0 + 12 new
ENERGY_V11_FEATURE_NAMES: list[str] = [
    # v1.0 baseline (8D)
    "mo_equivalent",
    "lattice_distortion",
    "allen_chi_diff",
    "vec",
    "cluster_I",
    "cluster_II",
    "cluster_III",
    "cluster_IV",
    # v1.1 additions (12D)
    *V11_ADDITIONAL_FEATURE_NAMES,
]


def compute_energy_features_v11(composition: dict[str, float]) -> dict[str, float]:
    """Compute the full 20D feature vector for EnergyPredictor v1.1.

    Combines the v1.0 8D Miedema-style features with 12 new element-resolved
    electronic structure and pairwise interaction features.

    Args:
        composition: Element name to atomic percent or atomic fraction mapping.

    Returns:
        Dictionary with 20 keys matching ENERGY_V11_FEATURE_NAMES.
    """
    from nfm_db.ml.feature_engineering import compute_ml_features

    result = compute_ml_features(composition)
    for name, calc in _V11_CALCULATORS:
        result[name] = calc(composition)
    return result


# ---------------------------------------------------------------------------
# v1.1 Model Loading and Inference (NFM-1802)
# ---------------------------------------------------------------------------

import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_V11_MODELS_DIR = Path(__file__).resolve().parents[5] / "apps" / "api" / "models"
_V11_MODEL_PATH = _V11_MODELS_DIR / "energy_predictor_v11.joblib"

_v11_model_cache: dict | None = None


def load_v11_model() -> dict | None:
    """Load the v1.1 energy predictor artifact (lazy).

    The artifact is a dict with keys:
    - ``model``: XGBRegressor
    - ``version``: "v1.1"
    - ``metrics``: dict with r2, rmse, mae
    - ``feature_names``: list[str]

    Returns:
        The artifact dict, or None if unavailable.
    """
    global _v11_model_cache
    if _v11_model_cache is not None:
        return _v11_model_cache

    model_path = os.environ.get(
        "ENERGY_PREDICTOR_PATH", str(_V11_MODEL_PATH),
    )
    if not Path(model_path).exists():
        logger.warning("v1.1 energy model not found at %s", model_path)
        return None

    try:
        import joblib
        raw = joblib.load(model_path)
        if isinstance(raw, dict) and "model" in raw:
            _v11_model_cache = raw
            logger.info("Loaded v1.1 energy model from %s", model_path)
            return _v11_model_cache
        _v11_model_cache = {"model": raw}
        return _v11_model_cache
    except Exception:
        logger.exception("Failed to load v1.1 model from %s", model_path)
        return None


def predict_energy_v11(features: dict[str, float]) -> dict | None:
    """Predict formation energy using the v1.1 20D model.

    Args:
        features: Dictionary of 20 feature values matching ENERGY_V11_FEATURE_NAMES.

    Returns:
        Dict with predicted_energy, confidence, model_version. None if unavailable.
    """
    model_data = load_v11_model()
    if model_data is None:
        return None
    try:
        model = model_data["model"]
        feature_names = model_data.get("feature_names", ENERGY_V11_FEATURE_NAMES)
        vals = [features.get(n, 0.0) for n in feature_names]
        X = np.array(vals, dtype=np.float64).reshape(1, -1)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = float(model.predict(X)[0])
        metrics = model_data.get("metrics", {})
        r2 = metrics.get("r2", 0.0)
        confidence = max(0.0, min(float(r2), 1.0))
        return {
            "predicted_energy": round(predicted, 6),
            "confidence": round(confidence, 4),
            "model_version": model_data.get("version", "v1.1"),
        }
    except Exception:
        logger.exception("v1.1 energy prediction failed")
        return None


def predict_energy_from_composition(
    composition: dict[str, float],
) -> dict | None:
    """Predict formation energy from a raw composition dict.

    Computes the full 20D feature vector, then runs v1.1 prediction.

    Args:
        composition: Element name to atomic percent or fraction mapping.

    Returns:
        Dict with predicted_energy, confidence, model_version. None if unavailable.
    """
    features = compute_energy_features_v11(composition)
    return predict_energy_v11(features)
