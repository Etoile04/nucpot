#!/usr/bin/env python3
"""Train EnergyPredictor v1.1 — expanded 20D feature set (NFM-1802).

Extends the v1.0 8D Miedema-style baseline with 12 new features
(element-resolved electronic structure + pairwise interactions).

Targets the same 1512 DFT records as the v1.0 baseline (12 export
batches + 2 supplementary + dft_incremental_200.csv), random_state=42.

AC R² goal ≥ 0.80 (relaxed from ≥ 0.90 per CPO disposition); plus a
hard floor at v1.0's 0.8293 — otherwise v1.0 remains default.

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v11
    cd apps/api && python -m nfm_db.ml.train_energy_v11 --data-dir /path/to/data
"""

from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

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

MODEL_VERSION = "v1.1"
MODEL_FILENAME = f"energy_predictor_{MODEL_VERSION.replace('.', '')}.joblib"
METRICS_FILENAME = f"energy_predictor_{MODEL_VERSION}_metrics.json"

RANDOM_STATE = 42
TEST_SIZE = 0.2


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _parse_composition(comp_str: str) -> dict[str, float] | None:
    """Parse a composition JSON string into a dict.

    Handles both ``{{"U": 0.9, "Mo": 0.1}}`` (fraction)
    and ``{{"U": 90, "Mo": 10}}`` (at.%).
    """
    try:
        comp = ast.literal_eval(comp_str)
        if isinstance(comp, dict):
            return {k: float(v) for k, v in comp.items()}
        return None
    except (ValueError, SyntaxError):
        return None


def load_dft_data(data_dir: Path) -> pd.DataFrame:
    """Load all DFT records into a single DataFrame.

    Combines the 12 dft-export batch CSVs (dft-export/), the 2 supplementary
    CSVs (dft-export/supplementary/), and dft_incremental_200.csv. Returns
    rows with valid composition and formation_energy.

    Total expected: 12*100 + 100 + 12 + 200 = 1512 records (v1.0 baseline).
    """
    frames: list[pd.DataFrame] = []

    batch_dir = data_dir / "dft-export"
    if batch_dir.exists():
        # Main batches: dft_export_batch_*.csv (12 files, 1200 records)
        for csv_path in sorted(batch_dir.glob("dft_export_batch_*.csv")):
            try:
                df = pd.read_csv(csv_path)
                if "formation_energy" in df.columns and "composition" in df.columns:
                    frames.append(df[["composition", "formation_energy"]])
                    logger.info("Loaded %d rows from %s", len(df), csv_path.name)
            except Exception:
                logger.exception("Failed to load %s", csv_path)

        # Supplementary batches: supplementary_dft_batch_*.csv (2 files, 112 records)
        supp_dir = batch_dir / "supplementary"
        if supp_dir.exists():
            for csv_path in sorted(supp_dir.glob("supplementary_dft_batch_*.csv")):
                try:
                    df = pd.read_csv(csv_path)
                    if "formation_energy" in df.columns and "composition" in df.columns:
                        frames.append(df[["composition", "formation_energy"]])
                        logger.info("Loaded %d rows from %s", len(df), csv_path.name)
                except Exception:
                    logger.exception("Failed to load %s", csv_path)

    inc_path = data_dir / "dft_incremental_200.csv"
    if inc_path.exists():
        try:
            df = pd.read_csv(inc_path)
            if "formation_energy" in df.columns and "composition" in df.columns:
                frames.append(df[["composition", "formation_energy"]])
                logger.info("Loaded %d rows from dft_incremental_200.csv", len(df))
        except Exception:
            logger.exception("Failed to load incremental data")

    if not frames:
        raise RuntimeError(
            f"No DFT data found in {data_dir}. "
            "Expected dft-export/ batch CSVs and/or dft_incremental_200.csv"
        )

    combined = pd.concat(frames, ignore_index=True)
    logger.info("Total raw DFT records: %d", len(combined))
    return combined


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

    Uses hyperparameters tuned via 5-fold CV on 1400 DFT samples.
    Best config (20D hp2): CV R²=0.8296 ± 0.0265.
    """
    model = xgb.XGBRegressor(
        n_estimators=500,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.6,
        colsample_bytree=0.6,
        reg_alpha=1.0,
        reg_lambda=8.0,
        min_child_weight=15,
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
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Train EnergyPredictor v1.1")
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Load data
    raw = load_dft_data(args.data_dir)
    X, y = build_dataset(raw)

    # 80/20 split (same random_state as v1.0)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
    )
    logger.info(
        "Train: %d samples, Test: %d samples",
        len(y_train), len(y_test),
    )

    # Train
    model = train_model(X_train, y_train, X_test, y_test)
    metrics = evaluate(model, X_test, y_test)

    # Also compute train metrics for overfitting check
    train_metrics = evaluate(model, X_train, y_train)
    metrics["r2_train"] = train_metrics["r2"]
    metrics["rmse_train"] = train_metrics["rmse"]
    metrics["mae_train"] = train_metrics["mae"]

    logger.info("=== v1.1 Hold-out Metrics ===")
    logger.info("R^2 (test):  %.4f  [target >= 0.90]", metrics["r2"])
    logger.info("RMSE (test): %.6f eV/atom", metrics["rmse"])
    logger.info("MAE (test):  %.6f eV/atom", metrics["mae"])
    logger.info("R^2 (train): %.4f", metrics["r2_train"])

    # Feature importance (top 10)
    importance = model.feature_importances_
    paired = sorted(
        zip(ENERGY_V11_FEATURE_NAMES, importance),
        key=lambda x: -x[1],
    )
    metrics["feature_importance"] = [
        {"name": name, "importance": round(float(imp), 4)}
        for name, imp in paired
    ]
    logger.info("Top 5 features: %s", paired[:5])

    metadata = {
        "model_version": MODEL_VERSION,
        "n_samples": len(y),
        "n_train": len(y_train),
        "n_test": len(y_test),
        "n_features": X.shape[1],
        "random_state": RANDOM_STATE,
        "feature_names": ENERGY_V11_FEATURE_NAMES,
        **metrics,
    }

    # Save model artifact
    args.output_dir.mkdir(parents=True, exist_ok=True)
    import joblib

    model_path = args.output_dir / MODEL_FILENAME
    artifact = {"model": model, "version": MODEL_VERSION, "metrics": metrics, "feature_names": ENERGY_V11_FEATURE_NAMES}
    joblib.dump(artifact, model_path)
    logger.info("Model saved to %s", model_path)

    # Save metrics
    metrics_path = args.output_dir / METRICS_FILENAME
    with open(metrics_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metrics saved to %s", metrics_path)

    logger.info("Model training complete. R^2=%.4f on hold-out test set.", metrics["r2"])
    # CPO disposition (NFM-1802): AC relaxed to R^2 >= 0.80 with hard floor at
    # v1.0's 0.8293. Current 1512 samples yield R^2 ~ 0.83 with 20D features;
    # v1.1 will become default only when this beats the v1.0 baseline.
    logger.info(
        "AC: R^2 >= 0.80 (relaxed per CPO disposition); hard floor = v1.0 R^2=0.8293. "
        "Current 1512 samples yield R^2 ~ %.4f with 20D features.",
        metrics["r2"],
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
