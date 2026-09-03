#!/usr/bin/env python3
"""Train EnergyPredictor v3.1 — 12D aggregates-only features (NFM-3988).

Per NFM-3958 PREREG §3-§4 (locked), v3.1 retrains on a 12D aggregates-only
stratum that strips the 8 pairwise/variance terms which act as element-system
fingerprints in v3.0 (root cause: NFM-3955 RD-3 anomaly review, where
v3.0's random-KFold R²=0.9678 collapses to 0.3111 ± 0.4777 under GroupKFold).

Single-variable comparison vs v3.0:
  - Same dataset (data/training_set_v30_raw.csv, 2,909 unique PBE compositions).
  - Same XGB_PARAMS verbatim from train_energy_v30.py:149-161.
  - Same random_state=42 and 5-fold KFold seed.
  - DIFFERENCE: feature set is the locked 12D subset (no pairwise / variance
    / vec stratum).

This script only ships initial random-CV metrics for v3.1. The full model
card with confirmatory GroupKFold (by element system, the protocol
PhaseClassifier v2.0 is held to under NFM-1756) lives in NFM-3990 (NFM-3958-C)
once GroupKFold is run by NFM-3989 (NFM-3958-B).

AC compliance (NFM-3988):
  - AC-A1: runs end-to-end and produces energy_predictor_v31.joblib + metrics JSON.
  - AC-A2: random KFold R² (5×, seed 42) reported alongside v3.0 incumbent
    (cv_r2=0.9678 ± 0.0102) for delta context.
  - AC-A3: 12D feature name list is literal and locked (ENERGY_V31_FEATURE_NAMES).
  - AC-A4: hyperparameters identical to v3.0; only feature set changes here.

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v31
    cd apps/api && python -m nfm_db.ml.train_energy_v31 --data-dir /path/to/data
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

from nfm_db.ml.energy_features_v31 import (
    ENERGY_V31_FEATURE_NAMES,
    compute_energy_features_v31,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODELS_DIR = PROJECT_ROOT / "apps" / "api" / "models"

MODEL_VERSION = "v3.1"
MODEL_FILENAME = "energy_predictor_v31.joblib"  # matches AC-A1 (no dot)
METRICS_FILENAME = "energy_predictor_v3.1_metrics.json"  # matches parent AC-2

RANDOM_STATE = 42
TEST_SIZE = 0.2

# v3.0 random-KFold incumbent for delta context (AC-A2).
# Sourced from apps/api/models/energy_predictor_v3.0_metrics.json.
V30_RANDOM_KFOLD_R2_MEAN = 0.9678
V30_RANDOM_KFOLD_R2_STD = 0.0102


# ---------------------------------------------------------------------------
# Data loading (mirrors train_energy_v30.load_v30_data)
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


def load_v31_data(data_dir: Path) -> pd.DataFrame:
    """Load the v3.1 training dataset — same v3.0 raw CSV.

    NFM-3988 AC: same dataset as v3.0 (2,909 unique PBE compositions from
    NFM-1540 PathB Star-xingyi). No data change vs v3.0.
    """
    csv_path = data_dir / "training_set_v30_raw.csv"
    if not csv_path.exists():
        raise FileNotFoundError(
            f"v3.1 training data not found at {csv_path}. "
            "v3.1 reuses v3.0's raw CSV (no data change)."
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
    """Parse compositions, compute 12D features, and extract targets.

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

        feat_dict = compute_energy_features_v31(comp)
        features_list.append(feat_dict)
        targets.append(float(fe))

    if skipped > 0:
        logger.warning("Skipped %d rows with invalid composition or energy", skipped)

    X = pd.DataFrame(features_list, columns=ENERGY_V31_FEATURE_NAMES).to_numpy()
    y = np.array(targets)
    logger.info(
        "Built v3.1 dataset: %d samples x %d features, target range [%.4f, %.4f]",
        len(y), X.shape[1], y.min(), y.max(),
    )

    # AC-A3 contract assertion — defense-in-depth against silent drift.
    assert X.shape[1] == 12, f"v3.1 must be 12D; got {X.shape[1]}"
    return X, y


# ---------------------------------------------------------------------------
# Training (XGB_PARAMS verbatim from train_energy_v30.py:149-161 — AC-A4)
# ---------------------------------------------------------------------------

XGB_PARAMS: dict[str, object] = {
    "n_estimators": 800,
    "max_depth": 5,
    "learning_rate": 0.02,
    "subsample": 0.7,
    "colsample_bytree": 0.7,
    "reg_alpha": 1.5,
    "reg_lambda": 10.0,
    "min_child_weight": 10,
    "gamma": 0.1,
    "random_state": 42,  # overridden by RANDOM_STATE at call sites
    "verbosity": 0,
}


def train_model(
    X_train: np.ndarray, y_train: np.ndarray,
    X_test: np.ndarray, y_test: np.ndarray,
) -> xgb.XGBRegressor:
    """Train XGBoost regressor on 12D aggregates-only features."""
    params = {**XGB_PARAMS, "random_state": RANDOM_STATE}
    model = xgb.XGBRegressor(**params)
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
# Cross-validation for honest estimation (AC-A2: random KFold 5× seed 42)
# ---------------------------------------------------------------------------


def cross_validate(X: np.ndarray, y: np.ndarray, n_splits: int = 5) -> dict[str, list[float]]:
    """Run 5-fold KFold CV (shuffle=True, seed=42) for honest v3.1 estimate."""
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_STATE)
    cv_metrics: dict[str, list[float]] = {"r2": [], "rmse": [], "mae": []}

    for fold, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        model = xgb.XGBRegressor(**{**XGB_PARAMS, "random_state": RANDOM_STATE})
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

    parser = argparse.ArgumentParser(description="Train EnergyPredictor v3.1 (12D aggregates-only)")
    parser.add_argument(
        "--data-dir", type=Path, default=DATA_DIR,
        help="Root data directory (default: project/data)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_MODELS_DIR,
        help="Model output directory (default: apps/api/models)",
    )
    parser.add_argument(
        "--cv-only", action="store_true",
        help="Run cross-validation only, skip final model training",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Load data
    raw = load_v31_data(args.data_dir)
    X, y = build_dataset(raw)

    logger.info("=== EnergyPredictor v3.1 Training (12D aggregates-only) ===")
    logger.info("Dataset: %d samples, %d features", len(y), X.shape[1])
    logger.info("Feature names (locked): %s", ENERGY_V31_FEATURE_NAMES)

    # AC-A2: random KFold R² (5×, seed 42) — delta context vs v3.0 incumbent.
    logger.info("Running 5-fold random KFold CV (seed=42)...")
    cv_metrics = cross_validate(X, y, n_splits=5)
    cv_r2 = float(np.mean(cv_metrics["r2"]))
    cv_r2_std = float(np.std(cv_metrics["r2"]))
    logger.info(
        "v3.1 random KFold R²: %.4f ± %.4f (v3.0 incumbent: %.4f ± %.4f)",
        cv_r2, cv_r2_std, V30_RANDOM_KFOLD_R2_MEAN, V30_RANDOM_KFOLD_R2_STD,
    )
    delta = cv_r2 - V30_RANDOM_KFOLD_R2_MEAN
    logger.info("v3.1 − v3.0 random KFold R² Δ = %+.4f", delta)

    if args.cv_only:
        logger.info("CV-only mode. Final model not trained.")
        sys.exit(0)

    # 80/20 hold-out split — same protocol as v3.0.
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE,
    )
    logger.info("Hold-out split: Train=%d, Test=%d", len(y_train), len(y_test))

    # Train
    model = train_model(X_train, y_train, X_test, y_test)
    metrics: dict[str, float] = evaluate(model, X_test, y_test)

    train_metrics = evaluate(model, X_train, y_train)
    metrics["r2_train"] = train_metrics["r2"]
    metrics["rmse_train"] = train_metrics["rmse"]
    metrics["mae_train"] = train_metrics["mae"]

    logger.info("=== v3.1 Hold-out Metrics ===")
    logger.info("R² (test):  %.4f", metrics["r2"])
    logger.info("RMSE (test): %.6f eV/atom", metrics["rmse"])
    logger.info("MAE (test):  %.6f eV/atom", metrics["mae"])
    logger.info("R² (train): %.4f", metrics["r2_train"])

    # CV summary
    metrics["cv_r2"] = round(cv_r2, 4)
    metrics["cv_r2_std"] = round(cv_r2_std, 4)
    metrics["cv_rmse"] = round(float(np.mean(cv_metrics["rmse"])), 6)
    metrics["cv_mae"] = round(float(np.mean(cv_metrics["mae"])), 6)

    # AC-A2: explicit delta vs v3.0 incumbent.
    metrics["v30_cv_r2_mean"] = V30_RANDOM_KFOLD_R2_MEAN
    metrics["v30_cv_r2_std"] = V30_RANDOM_KFOLD_R2_STD
    metrics["cv_r2_delta_vs_v30"] = round(delta, 4)

    # Feature importance
    importance = model.feature_importances_
    paired = sorted(
        zip(ENERGY_V31_FEATURE_NAMES, importance, strict=True),
        key=lambda x: -x[1],
    )
    all_metrics: dict[str, object] = metrics  # type: ignore[assignment]
    all_metrics["feature_importance"] = [
        {"name": name, "importance": round(float(imp), 4)}
        for name, imp in paired
    ]
    logger.info("Top 5 v3.1 features: %s", paired[:5])

    # Evidence label: this is the random-CV initial cut. GroupKFold is the
    # confirmatory number and lives in NFM-3989 (B) / NFM-3990 (C).
    rd2_label = "EXPLORATORY"
    rd3_verdict = {
        "scope": "NFM-3988-A: 12D trainer + random-CV initial cut only",
        "confirmatory_grouped_cv": "pending NFM-3989 (B)",
        "model_card": "pending NFM-3990 (C)",
        "ship_gate": "blocked until grouped R² ≥ 0.80 per NFM-3958 PREREG §6",
    }

    metadata = {
        "model_version": MODEL_VERSION,
        "n_samples": len(y),
        "n_train": len(y_train),
        "n_test": len(y_test),
        "n_features": X.shape[1],
        "random_state": RANDOM_STATE,
        "feature_names": ENERGY_V31_FEATURE_NAMES,
        "dropped_features": [
            "vec", "hr_valence_diff", "dg_en_radius_distance",
            "max_pair_en_diff", "en_variance", "volume_variance",
            "d_electron_variance", "bulk_modulus_variance",
        ],
        "dataset_source": "NFM-1540 PathB Star-xingyi (2,909 unique PBE compositions) — same as v3.0",
        "prereg_reference": "NFM-3958 §3-§4 (locked)",
        "rd2_label": rd2_label,
        "rd3_verdict": rd3_verdict,
        "ac_compliance": {
            "AC-A1": "PASS — runs end-to-end, joblib + metrics JSON produced",
            "AC-A2": "PASS — random KFold R² (5×, seed 42) reported alongside v3.0 incumbent",
            "AC-A3": "PASS — 12D feature list literal and locked (ENERGY_V31_FEATURE_NAMES)",
            "AC-A4": "PASS — hyperparameters identical to v3.0; only feature set changes",
        },
        **all_metrics,
    }

    # Save model artifact
    args.output_dir.mkdir(parents=True, exist_ok=True)
    import joblib

    model_path = args.output_dir / MODEL_FILENAME
    artifact = {
        "model": model,
        "version": MODEL_VERSION,
        "metrics": all_metrics,
        "feature_names": ENERGY_V31_FEATURE_NAMES,
    }
    joblib.dump(artifact, model_path)
    logger.info("Model saved to %s", model_path)

    # Save metrics
    metrics_path = args.output_dir / METRICS_FILENAME
    with open(metrics_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metrics saved to %s", metrics_path)

    logger.info(
        "v3.1 trainer complete. R²=%.4f on hold-out; random KFold R²=%.4f ± %.4f "
        "(v3.0: %.4f ± %.4f, Δ=%+.4f).",
        metrics["r2"], cv_r2, cv_r2_std,
        V30_RANDOM_KFOLD_R2_MEAN, V30_RANDOM_KFOLD_R2_STD, delta,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
