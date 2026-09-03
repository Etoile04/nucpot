"""Model loading and inference service for prediction endpoints (NFM-1598, NFM-1669, NFM-1789, NFM-2201).

Provides lazy-loaded model instances and inference functions for:
- Phase classification (RF+XGB VotingClassifier) with confidence scoring
- Temperature prediction (GPR+SVR ensemble) with confidence scoring
- Energy prediction (formation energy) with confidence scoring
- EnergyPredictor: v3.0 (default — see confidence disclosure below), v1.1, v1.0.
  predict_energy() dispatches by ``model_version`` so legacy callers
  don't regress (backward compat).

Confidence disclosure for EnergyPredictor v3.0 (NFM-3959 — LE handoff
from NFM-3953 grouped-CV confirmatory run; supersedes NFM-3956):
  EnergyPredictor v3.0 is labeled **[EXPLORATORY]** on this branch. The
  random 80/20 hold-out headline (``metrics.r2``) and the random
  ``KFold(shuffle=True)`` CV figure (``metrics.cv_r2``) were both
  materially optimistic because element-system near-neighbour compositions
  leak across splits. The GroupKFold(n_splits=5)-by-element-system
  re-evaluation (NFM-3953, locked protocol = same as PhaseClassifier v2.0)
  produced R^2 = 0.3111 +/- 0.4777 (LOW bucket). Per-fold spread includes
  a negative-R^2 fold (fold 3: R^2 = -0.5652).

  When the artifact (or sidecar JSON) carries ``rd2_label == "[EXPLORATORY]"``
  and ``grouped_cv_summary.r2_mean``, this module reports the **clamped
  grouped-CV mean** as ``confidence`` and emits a
  ``energy_model_exploratory`` warning. The clamp is
  ``max(0, min(r2_mean, r2_random, 1.0))`` so ``confidence`` can never
  exceed ``r2_mean`` for any model version (NFM-3959 mandate 2).

  When ``rd2_label == "[EXPLORATORY]"`` is set but
  ``grouped_cv_summary.r2_mean`` is absent, the helper raises
  ``RuntimeError`` (NFM-3959 mandate 1: FAIL LOUDLY on misconfigured
  artifact) rather than silently falling back to the random-split headline.
  When both ``rd2_label`` and ``grouped_cv_summary`` are absent
  (pre-NFM-3953 legacy artifact), the helper returns ``confidence=None``
  and a ``energy_model_pre_grouped_cv`` warning so UIs render the
  at-risk figure with a clear disclaimer instead of as a primary claim.

  See ``models/energy_predictor_v3.0_metrics.json`` for the full
  grouped-CV summary and per-fold breakdown.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
)
from nfm_db.ml.energy_features_v31 import (
    ENERGY_V31_FEATURE_NAMES,
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

# File lives at src/nfm_db/ml/ → 4 .parent hops reach app root (works in both local and Docker)
MODELS_DIR = Path(__file__).resolve().parents[3] / "models"

PHASE_MODEL_PATH = MODELS_DIR / "phase_classifier_v01.joblib"
TEMP_MODEL_PATH = MODELS_DIR / "temp_predictor_v01.joblib"
ENERGY_MODEL_PATH = MODELS_DIR / "energy_predictor_v01.joblib"

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
_energy_model = None


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

    Per NFM-3954 (NDE ruling): every prediction response must carry the
    per-class recall the model was validated against, alongside the headline
    probability. Hiding per-class recall is what allowed the original
    accuracy-based KR-ML-1 acceptance bar to drift in the first place.

    Args:
        features: Dictionary of 8 physical feature values.

    Returns:
        Dictionary with keys: predicted_phase, predicted_phase_label,
        probabilities, confidence, warnings, model_version,
        per_class_recall, acceptance_criterion.
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

        per_class_recall = _phase_per_class_recall()
        acceptance_criterion = _phase_acceptance_criterion()

        return {
            "predicted_phase": predicted_label,
            "predicted_phase_label": phase_labels.get(predicted_label, predicted_label),
            "probabilities": probabilities,
            "confidence": confidence.score,
            "warnings": warnings_to_dicts(confidence.warnings),
            "model_version": PHASE_CLASSIFIER_VERSION,
            "per_class_recall": per_class_recall,
            "acceptance_criterion": acceptance_criterion,
        }
    except Exception:
        logger.exception("Phase prediction failed")
        return None


# ---------------------------------------------------------------------------
# Phase-classifier model card surface (NFM-3954)
# ---------------------------------------------------------------------------

_PHASE_METRICS_CACHE: dict | None = None


def _phase_metrics_path() -> Path:
    """Locate the v2.0 metrics JSON sibling to the phase classifier joblib."""
    model_path = os.environ.get("PHASE_CLASSIFIER_PATH", str(PHASE_MODEL_PATH))
    return Path(model_path).with_name("phase_classifier_v2.0_metrics.json")


def _load_phase_metrics() -> dict | None:
    """Read the v2.0 metrics JSON once per process; return None if missing."""
    global _PHASE_METRICS_CACHE
    if _PHASE_METRICS_CACHE is not None:
        return _PHASE_METRICS_CACHE
    metrics_path = _phase_metrics_path()
    if not metrics_path.is_file():
        logger.warning(
            "Phase classifier metrics JSON not found at %s; "
            "per_class_recall will be omitted from predictions",
            metrics_path,
        )
        _PHASE_METRICS_CACHE = {}
        return None
    try:
        with metrics_path.open("r", encoding="utf-8") as source:
            _PHASE_METRICS_CACHE = json.load(source)
    except (OSError, ValueError):
        logger.exception("Failed to read phase classifier metrics at %s", metrics_path)
        _PHASE_METRICS_CACHE = {}
        return None
    return _PHASE_METRICS_CACHE


def _phase_per_class_recall() -> dict[str, float] | None:
    """Per-class recall (H, M) from the model card; None if unavailable.

    Returned alongside every prediction per NFM-3954 NDE ruling.
    """
    metrics = _load_phase_metrics()
    if not metrics:
        return None
    recall = metrics.get("per_class_recall_overall")
    if not isinstance(recall, dict):
        return None
    out: dict[str, float] = {}
    for label in ("H", "M"):
        value = recall.get(label)
        if isinstance(value, (int, float)):
            out[label] = float(value)
    return out or None


def _phase_acceptance_criterion() -> dict[str, Any] | None:
    """Trimmed acceptance-criterion metadata for the active Sprint bar.

    The model card carries the full table; only the active Sprint row plus
    primary/secondary metric names is exposed on every prediction to keep the
    response envelope small.
    """
    metrics = _load_phase_metrics()
    if not metrics:
        return None
    criteria = metrics.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        return None
    active: dict[str, Any] | None = None
    for bar in criteria.get("sprint_bars", []):
        if isinstance(bar, dict) and bar.get("sprint", "").startswith("Sprint 4"):
            active = bar
            break
    if active is None:
        return None
    return {
        "primary_metric": criteria.get("primary_metric"),
        "secondary_metric": criteria.get("secondary_metric"),
        "sprint": active.get("sprint"),
        "macro_f1_min": active.get("macro_f1_min"),
        "M_recall_min": active.get("M_recall_min"),
        "model_macro_f1": active.get("model_macro_f1"),
        "model_M_recall": active.get("model_M_recall"),
        "verdict": active.get("verdict"),
    }


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

ENERGY_MODEL_V30_FILENAME = "energy_predictor_v30.joblib"
ENERGY_MODEL_V31_FILENAME = "energy_predictor_v31.joblib"
ENERGY_MODEL_V11_FILENAME = "energy_predictor_v11.joblib"
ENERGY_MODEL_V10_FILENAME = "energy_predictor_v10.joblib"

ENERGY_MODEL_PATH = MODELS_DIR / ENERGY_MODEL_V30_FILENAME


def _env_path(filename: str) -> Path:
    """Resolve model path with ENERGY_PREDICTOR_PATH env override (v3.0 default)."""
    env_override = os.environ.get("ENERGY_PREDICTOR_PATH")
    if env_override:
        return Path(env_override)
    return MODELS_DIR / filename


# ---------------------------------------------------------------------------
# EnergyPredictor model-card surface (NFM-4059)
# ---------------------------------------------------------------------------
#
# The v3.0 joblib ships without ``rd2_label`` / ``rd2_label_status`` /
# ``grouped_cv_summary`` keys in its metrics dict — those honesty tokens
# live in the sidecar JSON ``models/energy_predictor_v3.0_metrics.json``
# (populated by NFM-4053 / AC-OC-1). The prediction endpoint merges the
# sidecar into the runtime ``metrics`` so the /predict/energy response
# surfaces the EXPLORATORY pin status and the honest grouped-CV figure
# without a follow-up model-card fetch (NFM-4054 / AC-OC-4).
#
# Precedence: artifact-embedded keys always win; the card fills only the
# keys the artifact is missing. This lets a future rebuild re-pickle the
# card into the joblib and override the sidecar without code change.
#
# Graceful degradation: a missing or malformed card degrades to ``None``
# (the loader never raises). The merge is then a no-op and the legacy
# honesty path takes over — no 5xx for the operator.

# Cardinal honesty keys the merge step is allowed to fill from the card.
# Centralising the list keeps the merge contract auditable in one place
# and prevents accidental drift between the loader and the merge.
_ENERGY_CARD_MERGE_KEYS: tuple[str, ...] = (
    "rd2_label",
    "rd2_label_status",
    "grouped_cv_summary",
)

_ENERGY_CARD_PATH: Path = MODELS_DIR / "energy_predictor_v3.0_metrics.json"
_ENERGY_CARD_CACHE: dict | None = None


def _load_energy_card_metrics() -> dict | None:
    """Read the v3.0 model-card sidecar JSON once per process.

    Returns the parsed dict on success, ``None`` if the card is missing
    or malformed. The result is cached module-locally so repeated
    inference calls don't re-open the file (mirrors the pattern used
    by ``_load_phase_metrics``).

    NFM-4059: this is the loader the AC-OC-4 fix relies on. A missing
    card must NEVER propagate as a 5xx — callers fall back to legacy
    behaviour (``confidence=None`` + ``energy_model_pre_grouped_cv``
    warning) when this returns ``None``.
    """
    global _ENERGY_CARD_CACHE
    if _ENERGY_CARD_CACHE is not None:
        return _ENERGY_CARD_CACHE
    if not _ENERGY_CARD_PATH.is_file():
        logger.warning(
            "EnergyPredictor model card not found at %s; "
            "rd2_label / rd2_label_status / grouped_cv_summary will "
            "fall back to artifact-embedded values",
            _ENERGY_CARD_PATH,
        )
        _ENERGY_CARD_CACHE = {}
        return None
    try:
        with _ENERGY_CARD_PATH.open("r", encoding="utf-8") as source:
            _ENERGY_CARD_CACHE = json.load(source)
    except (OSError, ValueError):
        logger.exception(
            "Failed to read EnergyPredictor model card at %s; "
            "falling back to artifact-embedded metrics",
            _ENERGY_CARD_PATH,
        )
        _ENERGY_CARD_CACHE = {}
        return None
    return _ENERGY_CARD_CACHE


def _merge_energy_card_metrics(
    artifact_metrics: dict,
    card_metrics: dict | None,
) -> dict:
    """Merge the sidecar card into the artifact metrics.

    Cardinal honesty keys (``rd2_label``, ``rd2_label_status``,
    ``grouped_cv_summary``) from the card fill any key the artifact is
    missing. Artifact values always win — the card is the fallback, not
    the override. Pure function: neither input is mutated.

    NFM-4059: this is the precedence rule the AC-OC-4 fix relies on.
    A future v3.0 rebuild that re-pickles the card into the joblib
    keeps the same behaviour (artifact wins, card fills gaps) without
    any code change.
    """
    merged = dict(artifact_metrics)
    if not card_metrics:
        return merged
    for key in _ENERGY_CARD_MERGE_KEYS:
        if key in merged:
            continue  # artifact wins
        if key in card_metrics:
            merged[key] = card_metrics[key]
    return merged


_ENERGY_V31_CARD_PATH: Path = MODELS_DIR / "energy_predictor_v3.1_metrics.json"
_ENERGY_V31_CARD_CACHE: dict | None = None


def _load_energy_v31_card_metrics() -> dict | None:
    """Read the v3.1 model-card sidecar JSON once per process.

    The NFM-3990 card uses flat top-level keys (``grouped_cv_r2``,
    ``grouped_cv_r2_std``, ``rd2_label``) rather than a nested
    ``grouped_cv_summary`` block. Mirrors ``_load_energy_card_metrics``
    (NFM-4059): a missing or malformed card degrades to ``None`` — never
    a 5xx — and the v3.1 dispatch falls back to artifact-embedded
    metrics.
    """
    global _ENERGY_V31_CARD_CACHE
    if _ENERGY_V31_CARD_CACHE is not None:
        return _ENERGY_V31_CARD_CACHE
    if not _ENERGY_V31_CARD_PATH.is_file():
        logger.warning(
            "EnergyPredictor v3.1 model card not found at %s; "
            "rd2_label / grouped_cv_summary will fall back to "
            "artifact-embedded values",
            _ENERGY_V31_CARD_PATH,
        )
        _ENERGY_V31_CARD_CACHE = {}
        return None
    try:
        with _ENERGY_V31_CARD_PATH.open("r", encoding="utf-8") as source:
            _ENERGY_V31_CARD_CACHE = json.load(source)
    except (OSError, ValueError):
        logger.exception(
            "Failed to read EnergyPredictor v3.1 model card at %s; "
            "falling back to artifact-embedded metrics",
            _ENERGY_V31_CARD_PATH,
        )
        _ENERGY_V31_CARD_CACHE = {}
        return None
    return _ENERGY_V31_CARD_CACHE


def _merge_energy_v31_card_metrics(
    artifact_metrics: dict,
    card_metrics: dict | None,
) -> dict:
    """Merge the flat NFM-3990 card into the v3.1 artifact metrics.

    The v3.1 trainer writes ``rd2_label`` / grouped-CV figures only to
    the sidecar JSON, so a rebuild from the canonical trainer relies on
    this merge to keep the NFM-3959 honesty contract (mandates 1-3)
    reachable without re-running the repack script. The flat card is
    projected onto the runtime ``grouped_cv_summary`` shape and then run
    through the shared NFM-4059 precedence rule: artifact-embedded keys
    win, the card fills only what is absent. Pure function.
    """
    if not card_metrics:
        return dict(artifact_metrics)
    normalized: dict = {
        key: card_metrics[key]
        for key in ("rd2_label", "rd2_label_status")
        if card_metrics.get(key) is not None
    }
    if "grouped_cv_r2" in card_metrics:
        normalized["grouped_cv_summary"] = {
            "r2_mean": card_metrics["grouped_cv_r2"],
            "r2_std": card_metrics.get("grouped_cv_r2_std"),
            "per_fold_r2": card_metrics.get("grouped_cv_per_fold_r2", []),
        }
    return _merge_energy_card_metrics(artifact_metrics, normalized)


def _predict_energy_v11(features: dict[str, float]) -> dict | None:
    """Run the v1.1 20D EnergyPredictor (lazy-loaded).

    Returns None when the v1.1 artifact is unavailable. The v1.1 path is
    plumbed so v1.1 callers can request ``model_version='v1.1'`` without
    raising (AC backward compat).
    """
    v11_path = MODELS_DIR / ENERGY_MODEL_V11_FILENAME
    if not v11_path.exists():
        logger.warning(
            "v1.1 energy model not found at %s; v1.1 callers must accept None",
            v11_path,
        )
        return None
    try:
        import joblib

        raw = joblib.load(v11_path)
        if isinstance(raw, dict) and "model" in raw:
            model = raw["model"]
            feature_names = raw.get("feature_names", ENERGY_V11_FEATURE_NAMES)
            model_data = raw
        else:
            model = raw
            feature_names = ENERGY_V11_FEATURE_NAMES
            model_data = {"model": raw}

        vals = [features.get(n, 0.0) for n in (feature_names or [])]
        X = np.array(vals, dtype=np.float64).reshape(1, -1)
        if X is None or X.shape[1] == 0:
            return None
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = float(model.predict(X)[0])
        metrics = model_data.get("metrics", {}) if isinstance(model_data, dict) else {}
        r2 = metrics.get("r2", 0.0)
        confidence = max(0.0, min(float(r2), 1.0))
        return {
            "predicted_energy": round(predicted, 6),
            "confidence": round(confidence, 4),
            # v1.1 has not been re-evaluated under grouped-CV; it is out of
            # NFM-3956 scope. The honest provenance label is
            # ``v10_or_v11_unevaluated`` so UIs know to render the v1.1
            # figure with a "not grouped-CV validated" disclaimer rather
            # than treating it as the NFM-3953 LOW bucket figure.
            "confidence_source": "v10_or_v11_unevaluated",
            "model_version": raw.get("version", "v1.1") if isinstance(raw, dict) else "v1.1",
            "warnings": [],
        }
    except Exception:
        logger.exception("v1.1 energy prediction failed")
        return None


def _predict_energy_v10(features: dict[str, float]) -> dict | None:
    """Run the v1.0 8D Miedema baseline EnergyPredictor (lazy-loaded).

    Returns None when the v1.0 artifact is unavailable (e.g., not yet deployed
    on this branch). The v1.0 path is plumbed so v1.0 callers can request
    ``model_version='v1.0'`` without raising (AC #3 backward compat).

    Expected artifact: ``models/energy_predictor_v10.joblib`` (joblib dict
    with keys ``model``, ``version``, ``metrics``, ``feature_names``).
    """
    v10_path = Path(
        os.environ.get("ENERGY_PREDICTOR_V10_PATH", str(MODELS_DIR / ENERGY_MODEL_V10_FILENAME))
    )
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
            # v1.0 has not been re-evaluated under grouped-CV; it is out of
            # NFM-3956 scope. See v1.1 comment for the rationale.
            "confidence_source": "v10_or_v11_unevaluated",
            "model_version": raw.get("version", "v1.0") if isinstance(raw, dict) else "v1.0",
            "warnings": [],
        }
    except Exception:
        logger.exception("v1.0 energy prediction failed")
        return None


def _compute_energy_confidence(
    metrics: dict, model_version_label: str = "v3.0"
) -> tuple[float | None, str, list[dict]]:
    """Compute the honest confidence score for an EnergyPredictor artifact.

    Implements the CTO architectural mandates from NFM-3959 (RD-3
    remediation), which tightened the NFM-3956 disclosure contract:

    Mandate 1 (FAIL LOUDLY):
        An artifact labeled ``rd2_label == "[EXPLORATORY]"`` MUST carry
        ``grouped_cv_summary.r2_mean``; if the key is absent the helper
        raises ``RuntimeError`` rather than silently falling back to the
        inflated random-split ``metrics.r2`` headline. The misconfiguration
        is surfaced to the operator (it propagates through
        ``_predict_energy_v30`` as a 5xx), not hidden behind a soft warning.

    Mandate 2 (clamp invariant):
        ``confidence`` must NEVER exceed ``grouped_cv_summary.r2_mean`` for
        any artifact that embeds the summary. The clamp is enforced here,
        next to the computation, so it holds for every model version (v3.0,
        v3.1, ...) not only for the current [EXPLORATORY] v3.0.

    Mandate 3 (rd2_label-driven warning):
        ``PredictionWarning(code='energy_model_exploratory')`` is emitted
        iff the model card carries ``rd2_label == '[EXPLORATORY]'``. When
        NFM-3958 clears the label on v3.1 the warning disappears
        automatically.

    NFM-3956 legacy fallback:
        Pre-NFM-3953 artifacts (no grouped-CV summary, no [EXPLORATORY]
        label) still return ``confidence=None`` with a
        ``energy_model_pre_grouped_cv`` warning so UIs render the at-risk
        figure with a clear disclaimer instead of as a primary claim.

    Args:
        metrics: ``metrics`` dict from the model artifact. May contain:
            - ``r2``: random 80/20 hold-out R^2 (legacy headline)
            - ``cv_r2``: random ``KFold(shuffle=True)`` CV R^2
            - ``rd2_label``: ``"[EXPLORATORY]"`` once NFM-3953 is applied
            - ``grouped_cv_summary``: dict with ``r2_mean``, ``r2_std``,
              ``decision_bucket`` (LOW/MID/HIGH)

    Returns:
        Tuple of:
            - ``confidence``: clamped to ``[0, min(r2_mean, r2_random)]``
              when ``grouped_cv_summary.r2_mean`` is present; ``None`` for
              legacy artifacts (no trustworthy figure available until
              retraining)
            - ``confidence_source``: ``"grouped_cv_r2_mean"`` (preferred,
              for any artifact with grouped_cv_summary) or
              ``"random_split_r2"`` (legacy fallback)
            - ``warnings``: list of ``{code, message}`` dicts; empty when
              the artifact is post-NFM-3953 with no [EXPLORATORY] label

    Raises:
        RuntimeError: ``rd2_label == "[EXPLORATORY]"`` but the artifact
            does not carry ``grouped_cv_summary.r2_mean``. Mandate 1.
    """
    r2_random_raw = metrics.get("r2", 0.0) or 0.0
    try:
        r2_random = float(r2_random_raw)
    except (TypeError, ValueError):
        r2_random = 0.0
    grouped_cv = metrics.get("grouped_cv_summary") or {}
    # NFM-3990 model card carries the provenance suffix after the
    # [EXPLORATORY] marker (e.g. ``"[EXPLORATORY] — grouped R² 0.2598
    # landed in FAIL bucket..."``), so we match by prefix instead of strict
    # equality. The original NFM-3953 contract used the bare label; the
    # extended provenance text is forward-compatible.
    is_exploratory = (
        isinstance(metrics.get("rd2_label"), str)
        and metrics["rd2_label"].startswith("[EXPLORATORY]")
    )
    has_grouped_r2_mean = "r2_mean" in grouped_cv

    # Mandate 1 (NFM-3959): FAIL LOUDLY when the artifact is labeled
    # [EXPLORATORY] but the grouped_cv_summary.r2_mean key is absent. We
    # must NOT silently fall back to the random-split headline; that would
    # re-introduce the very bug NFM-3956 fixed. Raising RuntimeError
    # propagates through _predict_energy_v30 as a 5xx so the operator sees
    # the misconfiguration instead of consumers seeing a quiet fallback.
    if is_exploratory and not has_grouped_r2_mean:
        raise RuntimeError(
            "EnergyPredictor artifact is labeled [EXPLORATORY] but does not "
            "carry grouped_cv_summary.r2_mean; refusing to compute "
            "confidence (NFM-3959 mandate 1). Re-train / re-evaluate the "
            "artifact under NFM-3953 GroupKFold protocol and embed "
            "grouped_cv_summary.r2_mean before redeploy."
        )

    if has_grouped_r2_mean:
        r2_mean_raw = float(grouped_cv["r2_mean"])
        # Mandate 2 (NFM-3959): NEVER advertise a confidence higher than
        # the grouped-CV R^2, for ANY model version. The clamp is
        # ``min(r2_mean, r2_random, 1.0)`` so the invariant holds even if a
        # future v3.1 ships with a random-split headline that exceeds the
        # grouped-CV figure (or if metrics.r2 is absent, the clamp falls
        # back to r2_mean as its own floor).
        r2_clamp_floor = r2_random if r2_random > 0 else r2_mean_raw
        confidence = max(0.0, min(r2_mean_raw, r2_clamp_floor, 1.0))

        if is_exploratory:
            # Mandate 3 (NFM-3959): the exact message per the spec. The
            # warning drives off rd2_label so it disappears automatically
            # when a successor artifact clears the label. The model version
            # is included so the message reads correctly for v3.0 vs v3.1.
            if model_version_label == "v3.0":
                warning_msg = (
                    f"v3.0 metrics re-labeled [EXPLORATORY] under RD-3; "
                    f"grouped-CV R^2={r2_mean_raw:.4f} reported as confidence "
                    f"until v3.1 ships (NFM-3958)."
                )
            else:
                warning_msg = (
                    f"{model_version_label} metrics labeled [EXPLORATORY] "
                    f"per NFM-3958 PREREG §6 (FAIL bucket, grouped-CV "
                    f"R^2={r2_mean_raw:.4f} reported as confidence)."
                )
            warnings: list[dict] = [
                {
                    "code": "energy_model_exploratory",
                    "message": warning_msg,
                }
            ]
            return round(confidence, 4), "grouped_cv_r2_mean", warnings

        # Non-exploratory artifact with grouped_cv_summary (e.g. v3.1 once
        # NFM-3958 ships). Confidence is the clamped grouped-CV mean, no
        # warning is emitted — rd2_label == "[EXPLORATORY]" is the
        # precondition for the warning, and v3.1 will clear it.
        return round(confidence, 4), "grouped_cv_r2_mean", []

    # Legacy artifact: no grouped-CV summary, no [EXPLORATORY] label.
    # Return ``confidence=None`` so the response does NOT advertise the
    # random-split headline as the user-facing confidence score. The raw
    # R^2 figure is surfaced in the warning message so UIs can render it
    # with a clear "at-risk" disclaimer instead of as a primary claim.
    # NFM-3956 round 2: this is the fix for E2E QA Finding #2 (AC text
    # "user-facing surfaces must not advertise R^2 = 0.9858").
    warnings = [
        {
            "code": "energy_model_pre_grouped_cv",
            "message": (
                "EnergyPredictor artifact predates the NFM-3953 grouped-CV "
                "re-evaluation; the random-split R^2 = "
                f"{r2_random:.4f} may be materially optimistic. "
                "Confidence is reported as null until the artifact is "
                "re-trained to embed grouped_cv_summary and rd2_label."
            ),
        }
    ]
    return None, "random_split_r2", warnings


def _predict_energy_v31(features: dict[str, float]) -> dict | None:
    """Run the v3.1 EnergyPredictor (12D aggregates-only, lazy-loaded).

    v3.1 is the NFM-3958 PREREG ablation: the 8 pairwise/variance stratum
    from v3.0's 20D feature set is dropped, leaving a locked 12D
    aggregates-only feature vector (NFM-3988, ENERGY_V31_FEATURE_NAMES).
    The 12D ablation landed in the FAIL bucket under GroupKFold
    (grouped R² = 0.2598 ± 0.5075 per NFM-3990 model card), so the
    artifact carries ``rd2_label == "[EXPLORATORY]"`` and the response
    surfaces the clamped grouped-CV R² as ``confidence`` plus an
    ``energy_model_exploratory`` warning. Mandate 1 (FAIL LOUDLY) still
    applies: an [EXPLORATORY] artifact without ``grouped_cv_summary.r2_mean``
    raises ``RuntimeError`` (NFM-3959).

    Returns None when the v3.1 artifact is unavailable.

    Expected artifact: ``models/energy_predictor_v31.joblib`` (joblib dict
    with keys ``model``, ``version``, ``metrics``, ``feature_names``). The
    ``metrics`` dict carries ``rd2_label`` and ``grouped_cv_summary`` per
    NFM-3990.
    """
    v31_path = _env_path(ENERGY_MODEL_V31_FILENAME)
    if not v31_path.exists():
        logger.warning(
            "v3.1 energy model not found at %s; v3.1 callers must accept None",
            v31_path,
        )
        return None
    try:
        import joblib

        raw = joblib.load(v31_path)
        if isinstance(raw, dict) and "model" in raw:
            model = raw["model"]
            feature_names = raw.get("feature_names", ENERGY_V31_FEATURE_NAMES)
            model_data = raw
        else:
            model = raw
            feature_names = ENERGY_V31_FEATURE_NAMES
            model_data = {"model": raw}

        vals = [features.get(n, 0.0) for n in (feature_names or [])]
        X = np.array(vals, dtype=np.float64).reshape(1, -1)
        if X is None or X.shape[1] == 0:
            return None
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = float(model.predict(X)[0])
        artifact_metrics = model_data.get("metrics", {}) if isinstance(model_data, dict) else {}
        # NFM-4059 precedence rule, v3.1 side: the flat NFM-3990 card fills
        # rd2_label / grouped_cv_summary only when the artifact lacks them,
        # so a rebuild from the canonical trainer keeps the NFM-3959
        # honesty contract without re-running the repack script.
        metrics = _merge_energy_v31_card_metrics(
            artifact_metrics, _load_energy_v31_card_metrics()
        )
    except Exception:
        logger.exception("v3.1 energy prediction failed")
        return None

    # Confidence computation lives outside the broad except above: the
    # NFM-3959 mandate-1 RuntimeError (artifact labeled [EXPLORATORY] but
    # grouped_cv_summary.r2_mean absent) MUST propagate as a 5xx so the
    # operator sees the misconfiguration.
    confidence, confidence_source, warnings = _compute_energy_confidence(
        metrics, model_version_label="v3.1"
    )
    return {
        "predicted_energy": round(predicted, 6),
        "confidence": confidence,
        "confidence_source": confidence_source,
        "model_version": raw.get("version", "v3.1") if isinstance(raw, dict) else "v3.1",
        "warnings": warnings,
    }


def _predict_energy_v30(features: dict[str, float]) -> dict | None:
    """Run the v3.0 EnergyPredictor trained on 2,909 PBE DFT compositions.

    Returns None when the v3.0 artifact is unavailable. Uses the same 20D
    feature schema as v1.1 (ENERGY_V11_FEATURE_NAMES); the improvement comes
    from the larger training set, not from feature engineering changes.

    Confidence reporting follows the NFM-3959 contract (which tightens
    NFM-3956): when the artifact is labeled ``[EXPLORATORY]``
    (NFM-3953 LOW bucket), the response carries the clamped grouped-CV
    mean R^2 as ``confidence`` and a ``energy_model_exploratory``
    warning. The mandate-1 misconfiguration (artifact labeled
    [EXPLORATORY] but ``grouped_cv_summary.r2_mean`` absent) raises
    ``RuntimeError`` from ``_compute_energy_confidence`` and is NOT
    swallowed — it propagates as a 5xx so the operator sees the bug
    instead of consumers seeing a quiet fallback. See module docstring +
    the ``_compute_energy_confidence`` helper for the full rule.

    Expected artifact: ``models/energy_predictor_v30.joblib`` (joblib dict
    with keys ``model``, ``version``, ``metrics``, ``feature_names``).
    """
    v30_path = _env_path(ENERGY_MODEL_V30_FILENAME)
    if not v30_path.exists():
        logger.warning("v3.0 energy model not found at %s", v30_path)
        return None
    try:
        import joblib

        raw = joblib.load(v30_path)
        if isinstance(raw, dict) and "model" in raw:
            model = raw["model"]
            feature_names = raw.get("feature_names", ENERGY_V11_FEATURE_NAMES)
            model_data = raw
        else:
            model = raw
            feature_names = ENERGY_V11_FEATURE_NAMES
            model_data = {"model": raw}

        vals = [features.get(n, 0.0) for n in (feature_names or [])]
        X = np.array(vals, dtype=np.float64).reshape(1, -1)
        if X is None or X.shape[1] == 0:
            return None
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = float(model.predict(X)[0])
        artifact_metrics = model_data.get("metrics", {}) if isinstance(model_data, dict) else {}
        # NFM-4059: merge the v3.0 model-card sidecar JSON into the runtime
        # metrics so the response surfaces rd2_label / rd2_label_status /
        # grouped_cv_summary without a follow-up model-card fetch. The
        # artifact-embedded keys win (card fills only what is absent). A
        # missing or malformed card degrades gracefully (no 5xx; legacy
        # honesty path takes over).
        card_metrics = _load_energy_card_metrics()
        metrics = _merge_energy_card_metrics(artifact_metrics, card_metrics)
    except Exception:
        logger.exception("v3.0 energy prediction failed")
        return None

    # Confidence computation is intentionally OUTSIDE the broad except
    # above: a RuntimeError raised by ``_compute_energy_confidence`` for
    # mandate-1 misconfigurations must propagate as a 5xx so the operator
    # sees the bug (NFM-3959 mandate 1: FAIL LOUDLY).
    confidence, confidence_source, warnings = _compute_energy_confidence(metrics)
    return {
        "predicted_energy": round(predicted, 6),
        "confidence": confidence,
        "confidence_source": confidence_source,
        "model_version": raw.get("version", "v3.0") if isinstance(raw, dict) else "v3.0",
        "warnings": warnings,
        # NFM-4054 / AC-OC-4: surface the model's rd2 label pair so the
        # /predict/energy response can render the EXPLORATORY pin status
        # directly to downstream UIs without a follow-up model-card fetch.
        "rd2_label": metrics.get("rd2_label"),
        "rd2_label_status": metrics.get("rd2_label_status"),
    }


def predict_energy(
    features: dict[str, float],
    model_version: str | None = None,
) -> dict | None:
    """Predict formation energy, dispatching by ``model_version``.

    Args:
        features: Feature dict. All versions share the 20D feature schema
            (ENERGY_V11_FEATURE_NAMES); missing keys are back-filled with 0.0.
        model_version: ``'v3.0'`` (default) uses the 2,909-composition model.
            ``'v1.1'`` uses the legacy 855-composition model.
            ``'v1.0'`` uses the original 8D Miedema baseline.

    Returns:
        Dict with ``predicted_energy``, ``confidence`` (may be ``None``
        for legacy pre-NFM-3953 v3.0 artifacts), ``confidence_source``
        (``"grouped_cv_r2_mean"`` | ``"random_split_r2"`` |
        ``"v10_or_v11_unevaluated"``), ``model_version``, ``warnings``.
        ``None`` if the requested artifact is unavailable.
    """
    effective = model_version or ENERGY_PREDICTOR_VERSION
    if effective == "v3.1":
        return _predict_energy_v31(features)
    if effective == "v3.0":
        return _predict_energy_v30(features)
    if effective == "v1.1":
        return _predict_energy_v11(features)
    if effective == "v1.0":
        return _predict_energy_v10(features)
    # Unknown version — fall back to v3.0 default
    return _predict_energy_v30(features)


def predict_energy_from_composition(
    composition: dict[str, float],
    model_version: str | None = None,
) -> dict | None:
    """Convenience wrapper: composition → features → predict_energy().

    Computes the 20D feature vector for all model versions (v3.0, v1.1, v1.0).
    For v1.0, the 8D baseline features are extracted from the 20D dict.
    """
    effective = model_version or ENERGY_PREDICTOR_VERSION
    if effective == "v10" or effective == "v1.0":
        from nfm_db.ml.feature_engineering import compute_ml_features

        v10_features = compute_ml_features(composition)
        return predict_energy(v10_features, model_version="v1.0")
    if effective == "v3.1":
        # v3.1 uses the locked 12D aggregates-only feature vector
        # (NFM-3958 PREREG §3-§4, ENERGY_V31_FEATURE_NAMES). The 8D
        # pairwise/variance stratum from v3.0 is deliberately omitted.
        from nfm_db.ml.energy_features_v31 import compute_energy_features_v31

        features = compute_energy_features_v31(composition)
        return predict_energy(features, model_version="v3.1")
    # v3.0 (default) and v1.1 share the 20D feature computation.
    # Compute features once, then dispatch through predict_energy()
    # which routes to the correct model artifact.
    from nfm_db.ml.energy_features_v11 import compute_energy_features_v11

    features = compute_energy_features_v11(composition)
    return predict_energy(features, model_version=effective)
