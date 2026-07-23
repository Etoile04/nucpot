#!/usr/bin/env python3
"""Train EnergyPredictor v1.0 — XGBoost regression for formation energy (NFM-1788).

Reads real alloy compositions and DFT-computed formation energies from
``data/dft-export/dft_export_batch_001_100_20260721.csv`` (and any other
DFT batch sharing the same schema), computes 8D physical features via
``feature_engineering.compute_all_features``, and uses the CSV's
``formation_energy`` column directly as the regression target.  Trains
an XGBoost regressor with 80/20 hold-out evaluation.

The target is read from the CSV rather than derived from any closed-form
expression of the input features, eliminating the prior algebraic
identity between ``mixing_enthalpy / 96.485 + T * config_entropy / 96485``
and the model's input column vector.

Acceptance criterion: R² > 0.90 on 80/20 split.

Usage:
    python -m nfm_db.ml.train_energy
    python -m nfm_db.ml.train_energy --output-dir /path/to/models
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from nfm_db.ml.energy_predictor import train_energy_predictor
from nfm_db.ml.feature_engineering import compute_all_features
from nfm_db.ml.prediction_service import PHYSICAL_FEATURE_NAMES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths — 4 parent hops to apps/api/ for models (same as prediction_service.py);
# 6 parent hops for the repository root (one extra to step out of apps/api/).
# ---------------------------------------------------------------------------

_MODELS_DIR = (
    Path(__file__)
    .resolve()
    .parent.parent.parent.parent
    / "models"
)
_PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent.parent.parent.parent.parent.parent
)
DATA_DIR = _PROJECT_ROOT / "data"
DEFAULT_TRAINING_SET = DATA_DIR / "dft-export" / "dft_export_batch_001_100_20260721.csv"
DEFAULT_MODELS_DIR = _MODELS_DIR

# Physical constants for target derivation
TEMPERATURE_K: float = 1000.0

MODEL_VERSION = "v1.0"


# ---------------------------------------------------------------------------
# Composition parsing
# ---------------------------------------------------------------------------


def _parse_composition(comp_str: str) -> dict[str, float]:
    """Parse a composition string like 'U-10Mo-5Nb' into a fraction dict.

    Handles hyphen-delimited (U-10Mo-5Nb),
    space-delimited (U 0.85 Mo 0.10 Nb 0.05),
    and JSON-object ("{""U"": 33.3, ""Zr"": 66.7}") formats.

    Returns:
        Dict mapping element symbols to atomic fractions summing to 1.0.
    """
    if pd.isna(comp_str) or not isinstance(comp_str, str):
        raise ValueError(f"Invalid composition: {comp_str!r}")

    # JSON-object format: "{""U"": 33.3, ""Zr"": 66.7}"
    if comp_str.startswith("{"):
        raw = json.loads(comp_str)
        total = sum(float(v) for v in raw.values())
        if total > 0:
            return {k: float(v) / total for k, v in raw.items()}

    # Hyphen-delimited format: U-10Mo-5Nb
    if "-" in comp_str:
        parts = re.findall(r"([A-Z][a-z]?)(\d+\.?\d*)", comp_str)
        if parts:
            comp = {elem: float(pct) / 100.0 for elem, pct in parts}
            total = sum(comp.values())
            if total > 0:
                return {k: v / total for k, v in comp.items()}

    # Space-delimited format: 'U 0.85 Mo 0.10 Nb 0.05'
    tokens = comp_str.split()
    if len(tokens) >= 4 and len(tokens) % 2 == 0:
        comp = {}
        for i in range(0, len(tokens), 2):
            elem, val = tokens[i], tokens[i + 1]
            comp[elem] = float(val)
        total = sum(comp.values())
        if total > 0:
            return {k: v / total for k, v in comp.items()}

    raise ValueError(f"Cannot parse composition: {comp_str!r}")


# ---------------------------------------------------------------------------
# Data loading — reads formation_energy from the DFT CSV.
# ---------------------------------------------------------------------------


def load_energy_training_data(
    csv_path: Path | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Load compositions + formation_energy from a DFT CSV.

    Reads ``data/dft-export/dft_export_batch_*.csv`` (or any conforming CSV
    with a ``composition`` JSON-object column and a ``formation_energy``
    float column), parses compositions, computes 8D physical features via
    ``compute_all_features()``, and uses ``formation_energy`` directly as
    the regression target.

    The training target is read from the CSV's ground-truth column rather
    than derived from any closed-form expression of the input features
    (the previous derivation was a closed-form algebraic identity of
    ``mixing_enthalpy / 96.485 + T * config_entropy / 96485`` where both
    ``mixing_enthalpy`` and ``config_entropy`` are members of
    ``PHYSICAL_FEATURE_NAMES``, which inflated R² without learning
    anything physically meaningful).

    Args:
        csv_path: Path to DFT CSV.  Defaults to
            ``data/dft-export/dft_export_batch_001_100_20260721.csv``.

    Returns:
        Tuple of (X, y) where X is (n_samples, 8) and y is (n_samples,)
        in eV/atom.
    """
    path = csv_path or DEFAULT_TRAINING_SET

    if not path.exists():
        raise FileNotFoundError(
            f"DFT training set not found at {path}. "
            f"Ensure data/dft-export/ is present in the repository."
        )

    df = pd.read_csv(path)
    logger.info("Loaded %d records from %s", len(df), path)

    X_list: list[list[float]] = []
    y_list: list[float] = []
    skipped = 0

    for _, row in df.iterrows():
        try:
            comp = _parse_composition(str(row["composition"]))
            features = compute_all_features(comp)

            target_raw = row.get("formation_energy")
            if pd.isna(target_raw):
                raise ValueError("missing formation_energy")
            target = float(target_raw)

            X_list.append([features[name] for name in PHYSICAL_FEATURE_NAMES])
            y_list.append(target)
        except Exception:
            skipped += 1

    if skipped > 0:
        logger.warning(
            "Skipped %d/%d records with parse errors", skipped, len(df)
        )

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.float64)

    # Replace NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

    logger.info(
        "Feature matrix: %d samples x %d features",
        X.shape[0], X.shape[1],
    )
    logger.info(
        "Target (formation_energy): mean=%.4f, std=%.4f, "
        "min=%.4f, max=%.4f eV/atom",
        y.mean(), y.std(), y.min(), y.max(),
    )

    return X, y


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------


def train_and_save(models_dir: Path) -> dict:
    """Full training pipeline: load data, train model, evaluate, save.

    Returns:
        Metrics dict with R², RMSE, MAE and acceptance status.
    """
    logger.info("=" * 60)
    logger.info("NFM-1788: EnergyPredictor v1.0 Training")
    logger.info("Output directory: %s", models_dir)
    logger.info("=" * 60)

    # Load training data from CSV
    logger.info("\n>>> Loading training data from CSV <<<\n")
    X, y = load_energy_training_data()

    # Train XGBoost regressor
    logger.info("\n>>> Training XGBoost regressor <<<\n")
    t0 = time.time()
    result = train_energy_predictor(X, y)
    train_time = time.time() - t0

    model = result["model"]
    scaler = result["scaler"]
    metrics = result["metrics"]

    r2 = metrics["r2"]
    rmse = metrics["rmse"]
    mae = metrics["mae"]

    logger.info(
        "Training complete: R2=%.4f, RMSE=%.4f eV/atom, "
        "MAE=%.4f eV/atom (%.1fs)",
        r2, rmse, mae, train_time,
    )

    # Save model artifact
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "energy_predictor_v01.joblib"

    try:
        import joblib

        artifact = {
            "model": model,
            "scaler": scaler,
            "target_mean": result["target_mean"],
            "target_std": result["target_std"],
            "metrics": metrics,
            "version": MODEL_VERSION,
        }
        joblib.dump(artifact, model_path)
        logger.info("Saved model to %s", model_path)
    except ImportError:
        logger.warning("joblib not available, skipping model save")

    # Save metrics
    full_metrics = {
        "version": MODEL_VERSION,
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "split": "80/20 hold-out",
        "r2": r2,
        "rmse": rmse,
        "mae": mae,
        "target_r2": 0.90,
        "acceptance_passed": r2 > 0.90,
        "training_seconds": round(train_time, 2),
        "model_path": str(model_path),
    }

    metrics_path = models_dir / "energy_predictor_v1.0_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(full_metrics, f, indent=2, default=str)
    logger.info("Saved metrics to %s", metrics_path)

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("EnergyPredictor v1.0 Results")
    logger.info("=" * 60)
    logger.info("R2:   %.4f (target > 0.90)", r2)
    logger.info("RMSE: %.4f eV/atom", rmse)
    logger.info("MAE:  %.4f eV/atom", mae)
    logger.info("Acceptance: %s", "PASS" if r2 > 0.90 else "FAIL")

    return full_metrics


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(output_dir: str | None = None) -> None:
    """Entry point for training script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    models_dir = Path(output_dir) if output_dir else DEFAULT_MODELS_DIR
    train_and_save(models_dir)


if __name__ == "__main__":
    main()
