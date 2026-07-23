#!/usr/bin/env python3
"""Train EnergyPredictor v1.1 — XGBoost regression for formation energy (NFM-1806).

Reads real alloy compositions and DFT-computed formation energies from
``data/dft-export/dft_export_batch_*.csv``, computes 22D expanded features
via ``feature_engineering.compute_energy_v11_features``, and uses the
CSV's ``formation_energy`` column as the regression target.

v1.1 expands the v1.0 8D Miedema-style aggregate descriptors with
per-element weighted electronic structure descriptors and pairwise
interaction terms (d-band filling, charge transfer, lattice relaxation).

Acceptance criterion: R^2 > 0.90 on 80/20 hold-out.

Usage:
    python -m nfm_db.ml.train_energy
    python -m nfm_db.ml.train_energy --output-dir /path/to/models
"""

from __future__ import annotations

import glob
import json
import logging
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from nfm_db.ml.energy_predictor import train_energy_predictor
from nfm_db.ml.feature_engineering import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_v11_features,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
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
DFT_EXPORT_DIR = DATA_DIR / "dft-export"
DEFAULT_MODELS_DIR = _MODELS_DIR

MODEL_VERSION = "v1.1"

# ---------------------------------------------------------------------------
# Composition parsing
# ---------------------------------------------------------------------------


def _parse_composition(comp_str: str) -> dict[str, float]:
    """Parse a composition string into a fraction dict.

    Handles JSON-object (\"{\"\"U\"\": 33.3, \"\"Zr\"\": 66.7}\""),
    hyphen-delimited (U-10Mo-5Nb), and
    space-delimited (U 0.85 Mo 0.10 Nb 0.05) formats.

    Returns:
        Dict mapping element symbols to atomic fractions summing to 1.0.
    """
    if pd.isna(comp_str) or not isinstance(comp_str, str):
        raise ValueError(f"Invalid composition: {comp_str!r}")

    # JSON-object format
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

    # Space-delimited format
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
# Data loading
# ---------------------------------------------------------------------------


def load_energy_training_data(
    csv_paths: list[Path] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Load compositions + formation_energy from DFT CSVs.

    Reads all ``data/dft-export/dft_export_batch_*.csv`` files (or a
    custom list), parses compositions, computes 22D features via
    ``compute_energy_v11_features()``, and uses ``formation_energy`` as
    the regression target.

    Args:
        csv_paths: List of DFT CSV paths.  Defaults to all batch files
            in data/dft-export/.

    Returns:
        Tuple of (X, y) where X is (n_samples, 22) and y is (n_samples,)
        in eV/atom.
    """
    if csv_paths is None:
        pattern = str(DFT_EXPORT_DIR / "dft_export_batch_*.csv")
        csv_paths = sorted(Path(p) for p in glob.glob(pattern))

    if not csv_paths:
        raise FileNotFoundError(
            f"No DFT CSVs found in {DFT_EXPORT_DIR}. "
            f"Ensure data/dft-export/ is present in the repository."
        )

    logger.info("Loading %d DFT CSV files...", len(csv_paths))

    X_list: list[list[float]] = []
    y_list: list[float] = []
    skipped = 0

    for path in csv_paths:
        df = pd.read_csv(path)
        logger.info("  %s: %d records", path.name, len(df))

        for _, row in df.iterrows():
            try:
                comp = _parse_composition(str(row["composition"]))
                features = compute_energy_v11_features(comp)

                target_raw = row.get("formation_energy")
                if pd.isna(target_raw):
                    raise ValueError("missing formation_energy")
                target = float(target_raw)

                X_list.append([features[name] for name in ENERGY_V11_FEATURE_NAMES])
                y_list.append(target)
            except Exception:
                skipped += 1

    if skipped > 0:
        logger.warning("Skipped %d records with parse errors", skipped)

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
        Metrics dict with R^2, RMSE, MAE and acceptance status.
    """
    logger.info("=" * 60)
    logger.info("NFM-1806: EnergyPredictor v1.1 Training")
    logger.info("Output directory: %s", models_dir)
    logger.info("=" * 60)

    # Load training data from CSV
    logger.info("")
    logger.info(">>> Loading training data from DFT CSVs <<")
    logger.info("")
    X, y = load_energy_training_data()

    # Train XGBoost regressor
    logger.info("")
    logger.info(">>> Training XGBoost regressor (22D features) <<")
    logger.info("")
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
    model_path = models_dir / "energy_predictor_v11.joblib"

    try:
        import joblib

        artifact = {
            "model": model,
            "scaler": scaler,
            "target_mean": result["target_mean"],
            "target_std": result["target_std"],
            "metrics": metrics,
            "feature_names": result["feature_names"],
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
        "feature_names": list(ENERGY_V11_FEATURE_NAMES),
    }

    metrics_path = models_dir / "energy_predictor_v1.1_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(full_metrics, f, indent=2, default=str)
    logger.info("Saved metrics to %s", metrics_path)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("EnergyPredictor v1.1 Results")
    logger.info("=" * 60)
    logger.info("R2:   %.4f (target > 0.90)", r2)
    logger.info("RMSE: %.4f eV/atom", rmse)
    logger.info("MAE:  %.4f eV/atom", mae)
    logger.info("Features: %d", X.shape[1])
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
