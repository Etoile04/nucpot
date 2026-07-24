"""Model loading and inference service for prediction endpoints (NFM-1598, NFM-1669, NFM-1802).

Provides lazy-loaded model instances and inference functions for:
- Phase classification (RF+XGB VotingClassifier) with confidence scoring
- Temperature prediction (GPR+SVR ensemble) with confidence scoring
- EnergyPredictor (NFM-1802): v1.0 8D Miedema baseline + v1.1 20D expansion.
  predict_energy() dispatches by ``model_version`` so v1.0 callers don't
  regress (AC #3 backward compat).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_features_v11,
    load_v11_model,
    predict_energy_from_composition as _predict_energy_v11_from_composition,
    predict_energy_v11,
)
from nfm_db.ml.model_version import (
    ENERGY_PREDICTOR_VERSION,
    PHASE_CLASSIFIER_VERSION,
    TEMP_PREDICTOR_VERSION,
    confidence_from_default,
    confidence_from_gpr_std,
    confidence_from_probability,
    warnings_to_dicts,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File lives at apps/api/src/nfm_db/ml/ → 6 .parent hops reach project root
MODELS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "models"

PHASE_MODEL_PATH = MODELS_DIR / "phase_classifier_v01.joblib"
TEMP_MODEL_PATH = MODELS_DIR / "temp_predictor_v01.joblib"

PHYSICAL_FEATURE_NAMES: list[str] = [
    "mo_equivalent",
    "pauling_chi_diff",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
]

CLUSTER_TYPE_NAMES: list[str] = [
    "type_I",
    "type_II",
    "type_III",
    "type_IV",
]

CLUSTER_TYPE_LABELS: list[str] = ["I", "II", "III", "IV"]

CLUSTER_PHASE_LABELS: dict[str, str] = {
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


def _cluster_type_from_features(features: dict[str, float]) -> str:
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


def _cluster_type_to_one_hot(cluster_type: str) -> list[float]:
    """Convert a cluster type label to one-hot encoding."""
    idx = CLUSTER_TYPE_LABELS.index(cluster_type)
    return [1.0 if i == idx else 0.0 for i in range(len(CLUSTER_TYPE_LABELS))]


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------


def build_feature_vector(
    features: dict[str, float],
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
    """Load the phase classifier model from disk (lazy).

    The trained artifact (``phase_classifier_v01.joblib``) is a dict with a
    ``model`` key holding the actual VotingClassifier.  We extract that key
    so downstream inference code can call ``model.predict()`` directly.
    """
    global _phase_model

    if _phase_model is not None:
        return _phase_model

    model_path = os.environ.get("PHASE_CLASSIFIER_PATH", str(PHASE_MODEL_PATH))

    if not Path(model_path).exists():
        logger.warning("Phase classifier model not found at %s", model_path)
        return None

    try:
        import joblib

        raw = joblib.load(model_path)

        # Artifact may be a dict wrapper or a bare estimator
        if isinstance(raw, dict):
            _phase_model = raw["model"]
            logger.info(
                "Loaded phase classifier from %s (dict wrapper, extracted 'model' key)",
                model_path,
            )
        else:
            _phase_model = raw
            logger.info("Loaded phase classifier from %s", model_path)

        return _phase_model
    except Exception:
        logger.exception("Failed to load phase classifier from %s", model_path)
        return None


def _load_temp_predictor() -> dict | None:
    """Load the temperature predictor model from disk (lazy).

    The trained artifact (``temp_predictor_v01.joblib``) is a dict containing
    ``gpr`` (GaussianProcessRegressor), ``svr`` (SVR), and ``scaler``
    (StandardScaler) rather than a single sklearn estimator.  We return the
    full dict so the inference function can orchestrate the ensemble manually.
    """
    global _temp_model

    if _temp_model is not None:
        return _temp_model

    model_path = os.environ.get("TEMP_PREDICTOR_PATH", str(TEMP_MODEL_PATH))

    if not Path(model_path).exists():
        logger.warning("Temperature predictor model not found at %s", model_path)
        return None

    try:
        import joblib

        raw = joblib.load(model_path)

        if isinstance(raw, dict):
            _temp_model = raw
            logger.info(
                "Loaded temperature predictor from %s (dict: gpr + svr + scaler)",
                model_path,
            )
        else:
            _temp_model = raw
            logger.info("Loaded temperature predictor from %s", model_path)

        return _temp_model
    except Exception:
        logger.exception("Failed to load temperature predictor from %s", model_path)
        return None


# ---------------------------------------------------------------------------
# Inference functions
# ---------------------------------------------------------------------------


def predict_phase(features: dict[str, float]) -> dict | None:
    """Run phase classification on 8 physical features.

    The model may be binary (2 classes) or multi-class.  We introspect
    ``model.classes_`` at inference time to build the correct probability
    mapping rather than assuming a fixed number of cluster types.

    Args:
        features: Dictionary of 8 physical feature values.

    Returns:
        Dictionary with keys: predicted_phase, predicted_phase_label,
        probabilities, confidence, warnings, model_version.
        Returns None if model unavailable.
    """
    model = _load_phase_classifier()
    if model is None:
        return None

    feature_vec = build_feature_vector(features).reshape(1, -1)

    try:
        predicted_index = int(model.predict(feature_vec)[0])
        proba = model.predict_proba(feature_vec)[0]

        # Use actual model classes to build probability list
        n_classes = len(model.classes_)

        # Map class indices to human-readable labels
        if n_classes == len(CLUSTER_TYPE_LABELS):
            labels = CLUSTER_TYPE_LABELS
            phase_labels = CLUSTER_PHASE_LABELS
        elif n_classes == 2:
            # Binary classifier: 0 = single phase, 1 = multi/two-phase
            labels = ["single_phase", "multi_phase"]
            phase_labels = {
                "single_phase": "single phase",
                "multi_phase": "multi/two-phase",
            }
        else:
            labels = [str(c) for c in model.classes_]
            phase_labels = {str(c): f"class_{c}" for c in model.classes_}

        predicted_label = labels[predicted_index]

        probabilities = [
            {
                "class": labels[i],
                "probability": round(float(proba[i]), 6),
            }
            for i in range(n_classes)
        ]

        # Confidence from max class probability
        proba_values = [float(proba[i]) for i in range(n_classes)]
        confidence = confidence_from_probability(proba_values)

        return {
            "predicted_phase": predicted_label,
            "predicted_phase_label": phase_labels.get(
                predicted_label, predicted_label
            ),
            "probabilities": probabilities,
            "confidence": confidence.score,
            "warnings": warnings_to_dicts(confidence.warnings),
            "model_version": PHASE_CLASSIFIER_VERSION,
        }
    except Exception:
        logger.exception("Phase prediction failed")
        return None


def predict_temperature(features: dict[str, float]) -> dict | None:
    """Run temperature prediction on 8 physical features.

    The model artifact is a dict containing ``gpr``, ``svr``, and ``scaler``.
    Inference: scale features → predict from GPR and SVR → equally-weighted
    ensemble → inverse-transform to original temperature scale.

    Args:
        features: Dictionary of 8 physical feature values.

    Returns:
        Dictionary with keys: predicted_temp_c, confidence_lower_c,
        confidence_upper_c, gpr_predicted_temp_c, svr_predicted_temp_c,
        confidence, warnings, model_version.
        Returns None if model unavailable.
    """
    model = _load_temp_predictor()
    if model is None:
        return None

    feature_vec = build_feature_vector(features).reshape(1, -1)

    try:
        # Handle both dict-based artifact and bare sklearn estimator
        if isinstance(model, dict):
            return _predict_temp_from_dict(model, feature_vec)

        # Fallback: bare estimator (e.g., if artifact is re-saved)
        predicted_temp = float(model.predict(feature_vec)[0])
        confidence_width = 30.0

        confidence = confidence_from_default(predicted_temp)

        return {
            "predicted_temp_c": round(predicted_temp, 1),
            "confidence_lower_c": round(predicted_temp - confidence_width, 1),
            "confidence_upper_c": round(predicted_temp + confidence_width, 1),
            "gpr_predicted_temp_c": None,
            "svr_predicted_temp_c": None,
            "confidence": confidence.score,
            "warnings": warnings_to_dicts(confidence.warnings),
            "model_version": TEMP_PREDICTOR_VERSION,
        }
    except Exception:
        logger.exception("Temperature prediction failed")
        return None


def _predict_temp_from_dict(
    model: dict,
    feature_vec: np.ndarray,
) -> dict:
    """Run ensemble temperature prediction from a dict-based model artifact.

    The dict contains:
    - ``gpr``: GaussianProcessRegressor (standardised-scale output)
    - ``svr``: SVR (standardised-scale output)
    - ``scaler``: StandardScaler fitted on training features
    - ``target_mean`` / ``target_std``: z-score normalisation of target

    Steps:
    1. Scale input features with ``scaler.transform()``
    2. Predict with GPR and SVR on scaled features
    3. Average (equal weights) for ensemble prediction
    4. Inverse z-score transform to get temperature in °C
    5. Estimate confidence from GPR std (or default ±30°C)
    6. Compute confidence score from GPR standard deviation
    """
    gpr = model["gpr"]
    svr = model["svr"]
    scaler = model["scaler"]
    target_mean = model.get("target_mean", 0.0)
    target_std = model.get("target_std", 1.0)

    scaled = scaler.transform(feature_vec)

    gpr_pred_z = float(gpr.predict(scaled)[0])
    svr_pred_z = float(svr.predict(scaled)[0])
    ensemble_z = 0.5 * gpr_pred_z + 0.5 * svr_pred_z

    # Inverse z-score → °C
    predicted_temp = ensemble_z * target_std + target_mean

    # Confidence from GPR uncertainty (if available)
    confidence_width = 30.0  # Default ±30°C
    gpr_std_c: float | None = None
    if hasattr(gpr, "predict") and hasattr(gpr, "_check_predict_params"):
        try:
            gpr_pred_std = gpr.predict(scaled, return_std=True)
            if isinstance(gpr_pred_std, tuple):
                std_z = float(gpr_pred_std[1][0])
            else:
                std_z = float(gpr_pred_std)
            gpr_std_c = std_z * target_std
            confidence_width = max(gpr_std_c * 1.96, 15.0)  # 95% CI, floor 15°C
        except Exception:
            logger.debug("GPR std estimation failed, using default confidence")

    gpr_temp_c = round(gpr_pred_z * target_std + target_mean, 1)
    svr_temp_c = round(svr_pred_z * target_std + target_mean, 1)

    # Compute confidence score
    if gpr_std_c is not None:
        confidence = confidence_from_gpr_std(gpr_std_c, predicted_temp)
    else:
        confidence = confidence_from_default(predicted_temp)

    return {
        "predicted_temp_c": round(predicted_temp, 1),
        "confidence_lower_c": round(predicted_temp - confidence_width, 1),
        "confidence_upper_c": round(predicted_temp + confidence_width, 1),
        "gpr_predicted_temp_c": gpr_temp_c,
        "svr_predicted_temp_c": svr_temp_c,
        "confidence": confidence.score,
        "warnings": warnings_to_dicts(confidence.warnings),
        "model_version": TEMP_PREDICTOR_VERSION,
    }


# ---------------------------------------------------------------------------
# EnergyPredictor (NFM-1802, AC #3 backward compat) — 20D v1.1 + v1.0 routing
# ---------------------------------------------------------------------------

ENERGY_MODEL_V11_FILENAME = "energy_predictor_v11.joblib"
ENERGY_MODEL_V10_FILENAME = "energy_predictor_v10.joblib"

ENERGY_MODEL_PATH = MODELS_DIR / ENERGY_MODEL_V11_FILENAME


def _env_path(filename: str) -> Path:
    """Resolve model path with ENERGY_PREDICTOR_PATH env override (v1.1 default)."""
    env_override = os.environ.get("ENERGY_PREDICTOR_PATH")
    if env_override:
        return Path(env_override)
    return MODELS_DIR / filename


def _predict_energy_v10(features: dict[str, float]) -> dict | None:
    """Run the v1.0 8D Miedema baseline EnergyPredictor (lazy-loaded).

    Returns None when the v1.0 artifact is unavailable (e.g., not yet deployed
    on this branch). The v1.0 path is plumbed so v1.0 callers can request
    ``model_version='v1.0'`` without raising (AC #3 backward compat).

    Expected artifact: ``models/energy_predictor_v10.joblib`` (joblib dict
    with keys ``model``, ``version``, ``metrics``, ``feature_names``).
    """
    v10_path = Path(os.environ.get("ENERGY_PREDICTOR_V10_PATH", str(MODELS_DIR / ENERGY_MODEL_V10_FILENAME)))
    if not v10_path.exists():
        logger.warning(
            "v1.0 energy model not found at %s; v1.0 callers must deploy v1.0 artifact or accept None",
            v10_path,
        )
        return None
    try:
        import joblib

        raw = joblib.load(v10_path)
        if isinstance(raw, dict) and "model" in raw:
            model = raw["model"]
            feature_names = raw.get("feature_names")
            model_data = raw
        else:
            model = raw
            feature_names = None
            model_data = {"model": raw}

        vals = [features.get(n, 0.0) for n in (feature_names or [])]
        X = np.array(vals, dtype=np.float64).reshape(1, -1) if vals else None
        if X is None or X.shape[1] == 0:
            logger.warning("v1.0 model has no declared feature_names; cannot run without schema")
            return None
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = float(model.predict(X)[0])
        metrics = model_data.get("metrics", {}) if isinstance(model_data, dict) else {}
        r2 = metrics.get("r2", 0.0)
        confidence = max(0.0, min(float(r2), 1.0))
        return {
            "predicted_energy": round(predicted, 6),
            "confidence": round(confidence, 4),
            "model_version": raw.get("version", "v1.0") if isinstance(raw, dict) else "v1.0",
            "warnings": [],
        }
    except Exception:
        logger.exception("v1.0 energy prediction failed")
        return None


def predict_energy(
    features: dict[str, float],
    model_version: str | None = None,
) -> dict | None:
    """Predict formation energy, dispatching by ``model_version`` (AC #3).

    Args:
        features: Feature dict. For ``model_version='v1.1'`` (default),
            expected keys match ``ENERGY_V11_FEATURE_NAMES`` (20D); the 12
            v1.1 additions may be absent on legacy v1.0 callers and are
            back-filled with zeros by the loader. For ``model_version='v1.0'``,
            expected keys match the v1.0 8D baseline (loaded from the v1.0
            artifact's ``feature_names``).
        model_version: ``'v1.0'`` to use the legacy 8D baseline; ``'v1.1'``
            (default) uses the expanded 20D model.

    Returns:
        Dict with ``predicted_energy``, ``confidence``, ``model_version``,
        ``warnings``. ``None`` if the requested artifact is unavailable.

    Notes:
        v1.0 callers must not be rejected. The path is plumbed end-to-end;
        the only step out of scope of this branch is deploying the v1.0
        artifact file, which is gated by NFM-1788's actual merge to main.
    """
    effective = model_version or ENERGY_PREDICTOR_VERSION
    if effective == "v1.0":
        return _predict_energy_v10(features)
    # v1.1 (default + fallback): back-fill missing v1.1 keys with 0.0 so
    # legacy v1.0 callers don't crash on the new schema (AC #3 backward compat).
    v11_input = {n: features.get(n, 0.0) for n in ENERGY_V11_FEATURE_NAMES}
    return predict_energy_v11(v11_input)


def predict_energy_from_composition(
    composition: dict[str, float],
    model_version: str | None = None,
) -> dict | None:
    """Convenience wrapper: composition → features → predict_energy().

    Computes the appropriate feature vector for the requested ``model_version``:
    - v1.1: full 20D via ``compute_energy_features_v11``
    - v1.0: the 8D Miedema baseline (extracted from the v1.1 feature dict, or
      computed independently via ``feature_engineering.compute_ml_features``).
    """
    effective = model_version or ENERGY_PREDICTOR_VERSION
    if effective == "v10" or effective == "v1.0":
        from nfm_db.ml.feature_engineering import compute_ml_features
        v10_features = compute_ml_features(composition)
        return predict_energy(v10_features, model_version="v1.0")
    return _predict_energy_v11_from_composition(composition)
