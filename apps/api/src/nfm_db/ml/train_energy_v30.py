#!/usr/bin/env python3
"""Train EnergyPredictor v3.0 — 2,909 unique PBE DFT compositions (NFM-2201).

Extends the v1.1 20D feature set with a significantly larger training dataset
(2,909 unique U-alloy compositions vs. v1.1's 855 unique compositions).

The v1.1 ceiling at R²=0.8486 was data-limited, not feature-limited:
  - v1.1 train R²=0.8641 vs test R²=0.8333 (overfitting gap)
  - Adding more features to 1,512 samples produced zero improvement past R²≈0.85

v3.0 targets R²≥0.90 by scaling from 855 → 2,909 unique compositions
from the NFM-1540 PathB DFT pipeline on Star-xingyi.

Split strategy: random 80/20 on unique compositions. Each composition appears
exactly once in the dataset, so random split is inherently leakage-free.

AC compliance (NFM-2201):
  - AC-2: R² ≥ 0.90 on fresh 80/20 hold-out from augmented set
  - AC-3: Honest metrics only — if R²<0.90, document the new ceiling

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v30
    cd apps/api && python -m nfm_db.ml.train_energy_v30 --data-dir /path/to/data
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, train_test_split

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_features_v11,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODELS_DIR = PROJECT_ROOT / "apps" / "api" / "models"

MODEL_VERSION = "v3.0"
MODEL_FILENAME = f"energy_predictor_{MODEL_VERSION.replace('.', '')}.joblib"
METRICS_FILENAME = f"energy_predictor_{MODEL_VERSION}_metrics.json"

RANDOM_STATE = 42
TEST_SIZE = 0.2


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _parse_composition(comp_str: str) -> dict[str, float] | None:
    """Parse a composition JSON string into a dict."""
    try:
        comp = json.loads(comp_str)
        if isinstance(comp, dict):
            return {k: float(v) for k, v in comp.items()}
        return None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def load_v30_data(data_dir: Path) -> pd.DataFrame:
    """Load the v3.0 DFT dataset exported from production DB.

    Expects data/training_set_v30_raw.csv with columns:
    composition, formation_energy, lattice_distortion, source, functional,
    calculation_id.

    Returns:
        DataFrame with composition (str) and formation_energy (float) columns.
    """
    csv_path = data_dir / "training_set_v30_raw.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"v3.0 training data not found at {csv_path}. "
            "Export from prod DB first: see NFM-2201 AC-1."
        )

    df = pd.read_csv(csv_path)
    if "composition" not in df.columns or "formation_energy" not in df.columns:
        raise ValueError(
            f"Expected 'composition' and 'formation_energy' columns, "
            f"got {list(df.columns)}"
        )

    df = df.dropna(subset=["formation_energy"])
    logger.info("Loaded %d records from %s", len(df), csv_path.name)
    return df[["composition", "formation_energy"]]


def build_dataset(raw: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Parse compositions, compute 20D features, and extract targets.

    Returns:
        (X, y) feature matrix and formation energy target vector.
    """
    features_list: list[dict[str, float]] = []
    targets: list[float] = []
    skipped = 0

    for _, row in raw.iterrows():
        comp = _parse_composition(str(row["composition"]))
        if comp is None:
            skipped += 1
            continue

        fe = row["formation_energy"]
        if pd.isna(fe) or not isinstance(fe, (int, float)):
            skipped += 1
            continue

        feat_dict = compute_energy_features_v11(comp)
        features_list.append(feat_dict)
        targets.append(float(fe))

    if skipped > 0:
        logger.warning("Skipped %d rows with invalid composition or energy", skipped)

    X = pd.DataFrame(features_list, columns=ENERGY_V11_FEATURE_NAMES).to_numpy()
    y = np.array(targets)
    logger.info(
        "Built dataset: %d samples x %d features, target range [%.4f, %.4f]",
        len(y), X.shape[1], y.min(), y.max(),
    )
    return X, y


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train_model(X_train: np.ndarray, y_train: np.ndarray, X_test: np.ndarray, y_test: np.ndarray) -> xgb.XGBRegressor:
    """Train XGBoost regressor on 20D features.

    Starts from v1.1's best config, adjusted for larger dataset:
    more trees, deeper, stronger regularization to prevent overfitting.
    """
    model = xgb.XGBRegressor(
        n_estimators=800,
        max_depth=5,
        learning_rate=0.02,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=1.5,
        reg_lambda=10.0,
        min_child_weight=10,
        gamma=0.1,
        random_state=RANDOM_STATE,
        verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    return model


def evaluate(
    model: xgb.XGBRegressor,
    X_test: np.ndarray,
    y_test: np.ndarray,
) -> dict[str, float]:
    """Compute hold-out metrics."""
    y_pred = model.predict(X_test)
    return {
        "r2": round(float(r2_score(y_test, y_pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_test, y_pred))), 6),
        "mae": round(float(mean_absolute_error(y_test, y_pred)), 6),
    }


# ---------------------------------------------------------------------------
# Cross-validation for honest estimation
# ---------------------------------------------------------------------------


def cross_validate(X: np.ndarray, y: np.ndarray, n_splits: int = 5) -> dict[str, list[float]]:
    """Run 5-fold CV for honest performance estimation.

    Returns dict of metric lists across folds.
    """
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE)
    cv_metrics: dict[str, list[float]] = {"r2": [], "rmse": [], "mae": []}

    for fold, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        model = xgb.XGBRegressor(
            n_estimators=800,
            max_depth=5,
            learning_rate=0.02,
            subsample=0.7,
            colsample_bytree=0.7,
            reg_alpha=1.5,
            reg_lambda=10.0,
            min_child_weight=10,
            gamma=0.1,
            random_state=RANDOM_STATE,
            verbosity=0,
        )
        model.fit(X_tr, y_tr, verbose=False)

        metrics = evaluate(model, X_val, y_val)
        for key in cv_metrics:
            cv_metrics[key].append(metrics[key])
        logger.info(
            "Fold %d: R²=%.4f, RMSE=%.6f, MAE=%.6f",
            fold + 1, metrics["r2"], metrics["rmse"], metrics["mae"],
        )

    return cv_metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Train EnergyPredictor v3.0")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Root data directory (default: project/data)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help="Model output directory (default: apps/api/models)",
    )
    parser.add_argument(
        "--cv-only",
        action="store_true",
        help="Run cross-validation only, skip final model training",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Load data
    raw = load_v30_data(args.data_dir)
    X, y = build_dataset(raw)

    logger.info("=== EnergyPredictor v3.0 Training ===")
    logger.info("Dataset: %d samples, %d features", len(y), X.shape[1])

    # Cross-validation for honest estimate
    logger.info("Running 5-fold cross-validation...")
    cv_metrics = cross_validate(X, y, n_splits=5)
    cv_r2 = np.mean(cv_metrics["r2"])
    cv_r2_std = np.std(cv_metrics["r2"])
    logger.info(
        "CV R²: %.4f ± %.4f (mean ± std across 5 folds)",
        cv_r2, cv_r2_std,
    )

    if args.cv_only:
        logger.info("CV-only mode. Final model not trained.")
        sys.exit(0)

    # 80/20 hold-out split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
    )
    logger.info(
        "Hold-out split: Train=%d, Test=%d",
        len(y_train), len(y_test),
    )

    # Train
    model = train_model(X_train, y_train, X_test, y_test)
    metrics: dict[str, object] = evaluate(model, X_test, y_test)

    # Train metrics for overfitting check
    train_metrics = evaluate(model, X_train, y_train)
    metrics["r2_train"] = train_metrics["r2"]
    metrics["rmse_train"] = train_metrics["rmse"]
    metrics["mae_train"] = train_metrics["mae"]

    logger.info("=== v3.0 Hold-out Metrics ===")
    logger.info("R² (test):  %.4f  [target >= 0.90]", metrics["r2"])
    logger.info("RMSE (test): %.6f eV/atom", metrics["rmse"])
    logger.info("MAE (test):  %.6f eV/atom", metrics["mae"])
    logger.info("R² (train): %.4f", metrics["r2_train"])

    # Cross-validation summary
    metrics["cv_r2"] = round(cv_r2, 4)
    metrics["cv_r2_std"] = round(cv_r2_std, 4)
    metrics["cv_rmse"] = round(float(np.mean(cv_metrics["rmse"])), 6)
    metrics["cv_mae"] = round(float(np.mean(cv_metrics["mae"])), 6)

    # Feature importance
    importance = model.feature_importances_
    paired = sorted(
        zip(ENERGY_V11_FEATURE_NAMES, importance, strict=True),
        key=lambda x: -x[1],
    )
    metrics["feature_importance"] = [
        {"name": name, "importance": round(float(imp), 4)}
        for name, imp in paired
    ]
    logger.info("Top 5 features: %s", paired[:5])

    # AC compliance
    if metrics["r2"] >= 0.90:
        logger.info("AC-2 PASSED: R²=%.4f >= 0.90", metrics["r2"])
    else:
        logger.warning(
            "AC-2 NOT MET: R²=%.4f < 0.90. Documenting new ceiling per AC-3.",
            metrics["r2"],
        )

    metadata = {
        "model_version": MODEL_VERSION,
        "n_samples": len(y),
        "n_train": len(y_train),
        "n_test": len(y_test),
        "n_features": X.shape[1],
        "random_state": RANDOM_STATE,
        "feature_names": ENERGY_V11_FEATURE_NAMES,
        "dataset_source": "NFM-1540 PathB Star-xingyi (2,909 unique PBE compositions)",
        **metrics,
    }

    # Save model artifact
    args.output_dir.mkdir(parents=True, exist_ok=True)
    import joblib

    model_path = args.output_dir / MODEL_FILENAME
    artifact = {
        "model": model,
        "version": MODEL_VERSION,
        "metrics": metrics,
        "feature_names": ENERGY_V11_FEATURE_NAMES,
    }
    joblib.dump(artifact, model_path)
    logger.info("Model saved to %s", model_path)

    # Save metrics
    metrics_path = args.output_dir / METRICS_FILENAME
    with open(metrics_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metrics saved to %s", metrics_path)

    logger.info("Model training complete. R²=%.4f on hold-out test set.", metrics["r2"])
    sys.exit(0)


if __name__ == "__main__":
    main()
