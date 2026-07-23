"""EnergyPredictor — XGBoost regression model for alloy binding energy prediction (NFM-1788).

Predicts formation energy (eV/atom) from 8D physical features using XGBoost regression.
Follows the lazy-load pattern from prediction_service.py.

Acceptance criterion: R² > 0.90 on 80/20 hold-out split.
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
# Model path — 4 parent hops from this file to apps/api/ (same as
# prediction_service.py).  Models live at apps/api/models/.
# ---------------------------------------------------------------------------

_MODELS_DIR = (
    Path(__file__)
    .resolve()
    .parent.parent.parent.parent
    / "models"
)
ENERGY_MODEL_PATH = _MODELS_DIR / "energy_predictor_v01.joblib"

# ---------------------------------------------------------------------------
# Feature column names — imported from the canonical location in
# prediction_service.py to avoid duplication.
# ---------------------------------------------------------------------------

from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES  # noqa: E402

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

    Returns:
        The full artifact dict, or None if unavailable.
    """
    global _energy_model

    if _energy_model is not None:
        return _energy_model

    model_path = os.environ.get(
        "ENERGY_PREDICTOR_PATH", str(ENERGY_MODEL_PATH)
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
                "Loaded energy predictor from %s "
                "(dict: model + scaler + target stats)",
                model_path,
            )
        else:
            _energy_model = raw
            logger.info("Loaded energy predictor from %s", model_path)

        return _energy_model
    except Exception:
        logger.exception(
            "Failed to load energy predictor from %s", model_path
        )
        return None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def predict_energy(features: dict[str, float]) -> dict | None:
    """Predict formation energy from 8 physical features.

    Uses the lazy-loaded XGBoost model.  Input features are:
    mo_equivalent, pauling_chi_diff, allen_chi_diff, config_entropy,
    bv_ratio, u_density, mixing_enthalpy, lattice_distortion.

    Args:
        features: Dictionary of 8 physical feature values.

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
        scaler = model_data["scaler"]
        target_mean = model_data.get("target_mean", 0.0)
        target_std = model_data.get("target_std", 1.0)

        # Build feature vector in column order expected by scaler/model
        feature_values = [features[name] for name in PHYSICAL_FEATURE_NAMES]
        X = np.array(feature_values, dtype=np.float64).reshape(1, -1)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # Scale features
        X_scaled = scaler.transform(X)

        # Predict (on z-score scale)
        y_z = float(model.predict(X_scaled)[0])

        # Inverse z-score transform → eV/atom
        predicted_energy = y_z * target_std + target_mean

        # Confidence from stored training R² (clamped to [0, 1])
        stored_metrics = model_data.get("metrics", {})
        r2 = stored_metrics.get("r2", 0.0)
        confidence = max(0.0, min(float(r2), 1.0))

        return {
            "predicted_energy": round(predicted_energy, 4),
            "confidence": round(confidence, 4),
            "model_version": ENERGY_PREDICTOR_VERSION,
        }
    except Exception:
        logger.exception("Energy prediction failed")
        return None


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_energy_predictor(
    X_train: np.ndarray,
    y_train: np.ndarray,
) -> dict:
    """Train an XGBoost regression model for energy prediction.

    Fits a StandardScaler on **training features only** (no data leakage),
    z-score-normalizes the target, trains an XGBRegressor, and evaluates
    on a held-out 20 % test set.

    Args:
        X_train: Feature matrix of shape (n_samples, 8).
        y_train: Target vector of shape (n_samples,).

    Returns:
        Dict with keys: model, scaler, target_mean, target_std, metrics.
        The metrics dict contains r2, rmse, mae from an 80/20 hold-out.
    """
    X = np.nan_to_num(X_train, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.asarray(y_train, dtype=np.float64)

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

    # Train XGBoost
    from xgboost import XGBRegressor

    model = XGBRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.1,
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
        "EnergyPredictor training: R2=%.4f, RMSE=%.4f, MAE=%.4f "
        "(80/20 split)",
        r2,
        rmse,
        mae,
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
    }