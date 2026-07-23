"""EnergyPredictor v1.1 — XGBoost regression for formation energy (NFM-1788, NFM-1802).

Predicts formation energy (eV/atom) from 20 expanded features using XGBoost
regression.  v1.1 extends the v1.0 8D Miedema-style aggregate descriptors
with per-element weighted electronic structure descriptors and pairwise
interaction terms to capture element-resolved d-band filling, charge transfer,
and lattice relaxation effects.

v1.1 achieved R^2 ~ 0.83 (CV) on 1400 DFT samples with 20D features.

v1.1 feature set (20 features):
  v1.0 baseline (8): mo_equivalent, lattice_distortion, allen_chi_diff,
    vec, cluster_I-IV
  v1.1 element-resolved (5): avg_allen_chi, avg_atomic_volume,
    avg_d_electron, avg_work_function, avg_bulk_modulus
  v1.1 pairwise interaction (7): hr_valence_diff, dg_en_radius_distance,
    max_pair_en_diff, en_variance, volume_variance,
    d_electron_variance, bulk_modulus_variance
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model path
# ---------------------------------------------------------------------------

_MODELS_DIR = (
    Path(__file__)
    .resolve()
    .parent.parent.parent.parent
    / "models"
)
ENERGY_MODEL_PATH = _MODELS_DIR / "energy_predictor_v11.joblib"

# ---------------------------------------------------------------------------
# Feature column names — imported from feature_engineering v1.1
# ---------------------------------------------------------------------------

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_features_v11,
)  # noqa: E402

# ---------------------------------------------------------------------------
# Cached model instance (lazy loaded)
# ---------------------------------------------------------------------------

_energy_model: dict | None = None


# ---------------------------------------------------------------------------
# Model loading (lazy)
# ---------------------------------------------------------------------------


def _load_energy_predictor() -> dict | None:
    """Load the energy predictor model artifact from disk (lazy).

    The artifact is a dict with keys:
    - ``model``: XGBRegressor
    - ``scaler``: StandardScaler (fitted on training features)
    - ``target_mean``: float (z-score mean of formation energy target)
    - ``target_std``: float (z-score std of formation energy target)
    - ``metrics``: dict with r2, rmse, mae
    - ``feature_names``: list of feature column names

    Returns:
        The full artifact dict, or None if unavailable.
    """
    global _energy_model

    if _energy_model is not None:
        return _energy_model

    model_path = os.environ.get(
        "ENERGY_PREDICTOR_PATH", str(ENERGY_MODEL_PATH),
    )

    if not Path(model_path).exists():
        logger.warning("Energy predictor model not found at %s", model_path)
        return None

    try:
        import joblib

        raw = joblib.load(model_path)

        if isinstance(raw, dict) and "model" in raw:
            _energy_model = raw
            logger.info(
                "Loaded energy predictor v1.1 from %s "
                "(dict: model + scaler + target stats)",
                model_path,
            )
        else:
            _energy_model = raw
            logger.info("Loaded energy predictor from %s", model_path)

        return _energy_model
    except Exception:
        logger.exception(
            "Failed to load energy predictor from %s", model_path,
        )
        return None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _artifact_version(model_data: dict) -> str:
    """Detect whether the artifact is v1.0 (scaler+z-score) or v1.1 (direct).

    v1.0 artifacts contain a 'scaler' key.  v1.1 artifacts contain a
    'version' key but no 'scaler'.
    """
    if "scaler" in model_data:
        return "v1.0"
    return model_data.get("version", "v1.1")


def predict_energy(features: dict[str, float]) -> dict | None:
    """Predict formation energy from pre-computed feature values.

    Handles both v1.0 (scaler + z-score normalization) and v1.1 (direct
    prediction, no scaler) model artifacts transparently.

    Args:
        features: Dictionary of feature values with keys matching the
            model's expected feature names.

    Returns:
        Dictionary with keys: predicted_energy (eV/atom), confidence (float),
        model_version (str).  Returns None if model unavailable.
    """
    from nfm_db.ml.model_version import ENERGY_PREDICTOR_VERSION

    model_data = _load_energy_predictor()
    if model_data is None:
        return None

    try:
        model = model_data["model"]
        version = _artifact_version(model_data)
        feature_names = model_data.get(
            "feature_names", ENERGY_V11_FEATURE_NAMES,
        )

        feature_values = [features.get(name, 0.0) for name in feature_names]
        X = np.array(feature_values, dtype=np.float64).reshape(1, -1)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        if version == "v1.0":
            scaler = model_data["scaler"]
            target_mean = model_data.get("target_mean", 0.0)
            target_std = model_data.get("target_std", 1.0)
            X_scaled = scaler.transform(X)
            y_z = float(model.predict(X_scaled)[0])
            predicted_energy = y_z * target_std + target_mean
        else:
            predicted_energy = float(model.predict(X)[0])

        stored_metrics = model_data.get("metrics", {})
        r2 = stored_metrics.get("r2", 0.0)
        confidence = max(0.0, min(float(r2), 1.0))

        return {
            "predicted_energy": round(predicted_energy, 6),
            "confidence": round(confidence, 4),
            "model_version": version,
        }
    except Exception:
        logger.exception("Energy prediction failed")
        return None


def predict_energy_from_composition(
    composition: dict[str, float],
) -> dict | None:
    """Predict formation energy from a raw composition dict.

    Computes the full 20D v1.1 feature vector from the composition, then
    runs prediction.  This is the preferred entry point for v1.1 — it
    does not require pre-computed features.

    Args:
        composition: Element name to atomic percent or fraction mapping.
            E.g. {"U": 0.7, "Mo": 0.2, "Ti": 0.1}

    Returns:
        Dictionary with keys: predicted_energy (eV/atom), confidence,
        model_version.  Returns None if model unavailable.
    """
    features = compute_energy_features_v11(composition)
    return predict_energy(features)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_energy_predictor(
    X_train: np.ndarray,
    y_train: np.ndarray,
    n_features: int | None = None,
) -> dict:
    """Train an XGBoost regression model for energy prediction.

    Fits a StandardScaler on **training features only** (no data leakage),
    z-score-normalizes the target, trains an XGBRegressor, and evaluates
    on a held-out 20 %% test set.

    Args:
        X_train: Feature matrix of shape (n_samples, n_features).
        y_train: Target vector of shape (n_samples,).
        n_features: Expected feature count for validation. If None, inferred.

    Returns:
        Dict with keys: model, scaler, target_mean, target_std, metrics,
        feature_names. The metrics dict contains r2, rmse, mae from
        an 80/20 hold-out.
    """
    X = np.nan_to_num(X_train, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.asarray(y_train, dtype=np.float64)

    if n_features is not None and X.shape[1] != n_features:
        logger.warning(
            "Feature dimension mismatch: expected %d, got %d",
            n_features, X.shape[1],
        )

    # Z-score normalize target for better XGBoost convergence
    target_mean = float(y.mean())
    target_std = float(y.std())
    if target_std < 1e-10:
        target_std = 1.0
    y_z = (y - target_mean) / target_std

    # Split BEFORE scaling to prevent data leakage
    X_tr, X_val, y_tr_z, y_val_z = train_test_split(
        X, y_z, test_size=0.2, random_state=42,
    )

    # Fit scaler on training split only
    scaler = StandardScaler()
    X_tr_scaled = scaler.fit_transform(X_tr)
    X_val_scaled = scaler.transform(X_val)

    # Train XGBoost with hyperparameters tuned for 22D features
    from xgboost import XGBRegressor

    model = XGBRegressor(
        n_estimators=500,
        max_depth=7,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
    )
    model.fit(X_tr_scaled, y_tr_z)

    # Evaluate on hold-out set (inverse z-score to original scale)
    y_pred_z = model.predict(X_val_scaled)
    y_pred = y_pred_z * target_std + target_mean
    y_val_orig = y_val_z * target_std + target_mean

    r2 = float(r2_score(y_val_orig, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_val_orig, y_pred)))
    mae = float(mean_absolute_error(y_val_orig, y_pred))

    logger.info(
        "EnergyPredictor v1.1 training: R2=%.4f, RMSE=%.4f, MAE=%.4f "
        "(80/20 split, %d features)",
        r2, rmse, mae, X.shape[1],
    )

    return {
        "model": model,
        "scaler": scaler,
        "target_mean": target_mean,
        "target_std": target_std,
        "metrics": {
            "r2": round(r2, 4),
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
        },
        "feature_names": list(ENERGY_V11_FEATURE_NAMES),
    }
