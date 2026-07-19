"""Model loading and inference service for prediction endpoints (NFM-1598).

Provides lazy-loaded model instances and inference functions for:
- Phase classification (RF+XGB VotingClassifier)
- Temperature prediction (GPR+SVR ensemble)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODELS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "models"

PHASE_MODEL_PATH = MODELS_DIR / "phase_classifier_v01.joblib"
TEMP_MODEL_PATH = MODELS_DIR / "temp_predictor_v01.joblib"

PHYSICAL_FEATURE_NAMES: List[str] = [
    "mo_equivalent",
    "pauling_chi_diff",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
]

CLUSTER_TYPE_NAMES: List[str] = [
    "type_I",
    "type_II",
    "type_III",
    "type_IV",
]

CLUSTER_TYPE_LABELS: List[str] = ["I", "II", "III", "IV"]

CLUSTER_PHASE_LABELS: Dict[str, str] = {
    "I": "α-U (single phase)",
    "II": "α+γ two-phase",
    "III": "γ (single phase)",
    "IV": "amorphous / metastable",
}

# ---------------------------------------------------------------------------
# Cached model instances (lazy loaded)
# ---------------------------------------------------------------------------

_phase_model = None
_temp_model = None


# ---------------------------------------------------------------------------
# Cluster type inference
# ---------------------------------------------------------------------------


def _cluster_type_from_features(features: Dict[str, float]) -> str:
    """Infer dominant cluster type from physical features.

    Uses a heuristic based on mixing_enthalpy and pauling_chi_diff:
    - Type I:  strongly exothermic (mixing_enthalpy < -3 kJ/mol)
    - Type II: mildly exothermic or small positive
    - Type III: moderately endothermic
    - Type IV: strongly endothermic

    This mirrors the cluster_model classification logic for when
    cluster type is not explicitly provided.
    """
    delta_h = features.get("mixing_enthalpy", 0.0)
    chi_diff = features.get("pauling_chi_diff", 0.0)

    if delta_h < -3.0:
        return "I"
    if delta_h < 3.0 and chi_diff < 0.15:
        return "II"
    if delta_h < 10.0:
        return "III"
    return "IV"


def _cluster_type_to_one_hot(cluster_type: str) -> List[float]:
    """Convert a cluster type label to one-hot encoding."""
    idx = CLUSTER_TYPE_LABELS.index(cluster_type)
    return [1.0 if i == idx else 0.0 for i in range(len(CLUSTER_TYPE_LABELS))]


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------


def build_feature_vector(
    features: Dict[str, float],
    cluster_type: str | None = None,
) -> np.ndarray:
    """Build the 12-dimensional feature vector expected by the model.

    Combines 8 physical features with 4 cluster-type one-hot features.
    If cluster_type is not provided, it is inferred from physical features.

    Args:
        features: Dictionary of 8 physical feature values.
        cluster_type: Optional explicit cluster type ("I", "II", "III", "IV").

    Returns:
        NumPy array of shape (12,).
    """
    if cluster_type is None:
        cluster_type = _cluster_type_from_features(features)

    physical_values = [features[name] for name in PHYSICAL_FEATURE_NAMES]
    one_hot = _cluster_type_to_one_hot(cluster_type)

    return np.array(physical_values + one_hot, dtype=np.float64)


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load_phase_classifier():
    """Load the phase classifier model from disk (lazy)."""
    global _phase_model  # noqa: PLW0603

    if _phase_model is not None:
        return _phase_model

    model_path = os.environ.get("PHASE_CLASSIFIER_PATH", str(PHASE_MODEL_PATH))

    if not Path(model_path).exists():
        logger.warning("Phase classifier model not found at %s", model_path)
        return None

    try:
        import joblib

        _phase_model = joblib.load(model_path)
        logger.info("Loaded phase classifier from %s", model_path)
        return _phase_model
    except Exception:
        logger.exception("Failed to load phase classifier from %s", model_path)
        return None


def _load_temp_predictor():
    """Load the temperature predictor model from disk (lazy)."""
    global _temp_model  # noqa: PLW0603

    if _temp_model is not None:
        return _temp_model

    model_path = os.environ.get("TEMP_PREDICTOR_PATH", str(TEMP_MODEL_PATH))

    if not Path(model_path).exists():
        logger.warning("Temperature predictor model not found at %s", model_path)
        return None

    try:
        import joblib

        _temp_model = joblib.load(model_path)
        logger.info("Loaded temperature predictor from %s", model_path)
        return _temp_model
    except Exception:
        logger.exception("Failed to load temperature predictor from %s", model_path)
        return None


# ---------------------------------------------------------------------------
# Inference functions
# ---------------------------------------------------------------------------


def predict_phase(features: Dict[str, float]) -> Dict | None:
    """Run phase classification on 8 physical features.

    Args:
        features: Dictionary of 8 physical feature values.

    Returns:
        Dictionary with keys: predicted_phase, predicted_phase_label,
        probabilities, model_version. Returns None if model unavailable.
    """
    model = _load_phase_classifier()
    if model is None:
        return None

    feature_vec = build_feature_vector(features).reshape(1, -1)

    try:
        predicted_index = model.predict(feature_vec)[0]
        proba = model.predict_proba(feature_vec)[0]

        predicted_label = CLUSTER_TYPE_LABELS[predicted_index]

        probabilities = [
            {
                "cluster_type": CLUSTER_TYPE_LABELS[i],
                "probability": round(float(proba[i]), 6),
            }
            for i in range(len(CLUSTER_TYPE_LABELS))
        ]

        return {
            "predicted_phase": predicted_label,
            "predicted_phase_label": CLUSTER_PHASE_LABELS[predicted_label],
            "probabilities": probabilities,
            "model_version": "v0.1",
        }
    except Exception:
        logger.exception("Phase prediction failed")
        return None


def predict_temperature(features: Dict[str, float]) -> Dict | None:
    """Run temperature prediction on 8 physical features.

    Args:
        features: Dictionary of 8 physical feature values.

    Returns:
        Dictionary with keys: predicted_temp_c, confidence_lower_c,
        confidence_upper_c, model_version. Returns None if model
        unavailable.
    """
    model = _load_temp_predictor()
    if model is None:
        return None

    feature_vec = build_feature_vector(features).reshape(1, -1)

    try:
        predicted_temp = float(model.predict(feature_vec)[0])

        # Try to get uncertainty estimates
        confidence_width = 30.0  # Default ±30°C if no uncertainty method
        if hasattr(model, "predict_std"):
            std = float(model.predict_std(feature_vec)[0])
            confidence_width = max(std * 1.96, 15.0)  # 95% CI, floor 15°C
        elif hasattr(model, "estimators"):
            # Ensemble: use std across individual predictions
            preds = [float(e.predict(feature_vec)[0]) for e in model.estimators]
            std = float(np.std(preds))
            confidence_width = max(std * 1.96, 15.0)

        gpr_temp = None
        svr_temp = None

        # If it's an ensemble with named components, extract them
        if hasattr(model, "named_estimators") or (
            hasattr(model, "estimators") and hasattr(model, "estimator_weights")
        ):
            names = list(getattr(model, "estimators_", []))
            if hasattr(model, "named_estimators"):
                names = list(model.named_estimators.keys())
            for i, name in enumerate(names):
                pred = float(model.estimators_[i].predict(feature_vec)[0])
                if "gpr" in name.lower():
                    gpr_temp = round(pred, 1)
                elif "svr" in name.lower():
                    svr_temp = round(pred, 1)

        return {
            "predicted_temp_c": round(predicted_temp, 1),
            "confidence_lower_c": round(predicted_temp - confidence_width, 1),
            "confidence_upper_c": round(predicted_temp + confidence_width, 1),
            "gpr_predicted_temp_c": gpr_temp,
            "svr_predicted_temp_c": svr_temp,
            "model_version": "v0.1",
        }
    except Exception:
        logger.exception("Temperature prediction failed")
        return None
