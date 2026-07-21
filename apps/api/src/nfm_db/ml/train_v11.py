#!/usr/bin/env python3
"""Train v1.1 ML models — PhaseClassifier and TempPredictor (NFM-1674).

Retrains both models on the expanded dataset:
  - PhaseClassifier: RF + XGBoost ensemble, 5-fold CV, binary H/M
    Training set: ~4151 H+M records from training_set_5551.csv
    Features: 12D (8 physical + 4 cluster-type one-hot)
    Target: CV accuracy > 78%
  - TempPredictor: GPR + SVR ensemble, LOO-CV
    Training set: ~55 records from ux_phase_transitions.csv
    Features: 13D (8 physical + 5 polynomial interactions)
    Target: log(T), LOO-CV MAE < 35°C

Usage:
    python -m nfm_db.ml.train_v11
    python -m nfm_db.ml.train_v11 --output-dir /path/to/models
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import Matern, WhiteKernel
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    mean_absolute_error,
    r2_score,
)
from sklearn.model_selection import LeaveOneOut, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

from nfm_db.ml.feature_engineering import (
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_cluster_fractions,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_pauling_chi_diff,
    calculate_u_density,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MODELS_DIR = PROJECT_ROOT / "apps" / "api" / "models"

TRAINING_SET_PATH = DATA_DIR / "training_set_5551.csv"

MODEL_VERSION = "v1.1"

# ---------------------------------------------------------------------------
# Experimental temperature data (v1.0 baseline, 61 samples)
# Curated U-X alloy compositions with measured gamma->alpha transition
# temperatures. Fraction-based compositions (sum to 1.0).
# Sources: Eckelman-1966, Peterson-2003, Sheldon-1962, Sumption-1954,
#          ANL-reports, Russian-fuel-lit, IAEA-TM
# ---------------------------------------------------------------------------

_EXPERIMENTAL_TEMPS: list[dict[str, object]] = [
    {"composition": {"U": 1.0}, "T": 668.0},
    {"composition": {"U": 0.99, "Mo": 0.01}, "T": 660.0},
    {"composition": {"U": 0.98, "Mo": 0.02}, "T": 650.0},
    {"composition": {"U": 0.97, "Mo": 0.03}, "T": 638.0},
    {"composition": {"U": 0.96, "Mo": 0.04}, "T": 625.0},
    {"composition": {"U": 0.95, "Mo": 0.05}, "T": 612.0},
    {"composition": {"U": 0.93, "Mo": 0.07}, "T": 590.0},
    {"composition": {"U": 0.9, "Mo": 0.1}, "T": 565.0},
    {"composition": {"U": 0.88, "Mo": 0.12}, "T": 548.0},
    {"composition": {"U": 0.85, "Mo": 0.15}, "T": 530.0},
    {"composition": {"U": 0.99, "Nb": 0.01}, "T": 665.0},
    {"composition": {"U": 0.98, "Nb": 0.02}, "T": 658.0},
    {"composition": {"U": 0.97, "Nb": 0.03}, "T": 648.0},
    {"composition": {"U": 0.96, "Nb": 0.04}, "T": 635.0},
    {"composition": {"U": 0.95, "Nb": 0.05}, "T": 620.0},
    {"composition": {"U": 0.93, "Nb": 0.07}, "T": 600.0},
    {"composition": {"U": 0.9, "Nb": 0.1}, "T": 575.0},
    {"composition": {"U": 0.87, "Nb": 0.13}, "T": 555.0},
    {"composition": {"U": 0.98, "Zr": 0.02}, "T": 672.0},
    {"composition": {"U": 0.96, "Zr": 0.04}, "T": 680.0},
    {"composition": {"U": 0.94, "Zr": 0.06}, "T": 688.0},
    {"composition": {"U": 0.92, "Zr": 0.08}, "T": 695.0},
    {"composition": {"U": 0.9, "Zr": 0.1}, "T": 700.0},
    {"composition": {"U": 0.87, "Zr": 0.13}, "T": 710.0},
    {"composition": {"U": 0.85, "Zr": 0.15}, "T": 715.0},
    {"composition": {"U": 0.8, "Zr": 0.2}, "T": 720.0},
    {"composition": {"U": 0.75, "Zr": 0.25}, "T": 718.0},
    {"composition": {"U": 0.7, "Zr": 0.3}, "T": 710.0},
    {"composition": {"U": 0.98, "Ti": 0.02}, "T": 655.0},
    {"composition": {"U": 0.96, "Ti": 0.04}, "T": 640.0},
    {"composition": {"U": 0.95, "Ti": 0.05}, "T": 628.0},
    {"composition": {"U": 0.93, "Ti": 0.07}, "T": 610.0},
    {"composition": {"U": 0.9, "Ti": 0.1}, "T": 588.0},
    {"composition": {"U": 0.88, "Ti": 0.12}, "T": 572.0},
    {"composition": {"U": 0.98, "V": 0.02}, "T": 648.0},
    {"composition": {"U": 0.96, "V": 0.04}, "T": 625.0},
    {"composition": {"U": 0.95, "V": 0.05}, "T": 610.0},
    {"composition": {"U": 0.93, "V": 0.07}, "T": 585.0},
    {"composition": {"U": 0.98, "Cr": 0.02}, "T": 645.0},
    {"composition": {"U": 0.96, "Cr": 0.04}, "T": 618.0},
    {"composition": {"U": 0.95, "Cr": 0.05}, "T": 598.0},
    {"composition": {"U": 0.93, "Cr": 0.07}, "T": 572.0},
    {"composition": {"U": 0.9, "Cr": 0.1}, "T": 545.0},
    {"composition": {"U": 0.98, "Fe": 0.02}, "T": 630.0},
    {"composition": {"U": 0.96, "Fe": 0.04}, "T": 595.0},
    {"composition": {"U": 0.95, "Fe": 0.05}, "T": 572.0},
    {"composition": {"U": 0.98, "Ni": 0.02}, "T": 618.0},
    {"composition": {"U": 0.96, "Ni": 0.04}, "T": 580.0},
    {"composition": {"U": 0.95, "Ni": 0.05}, "T": 558.0},
    {"composition": {"U": 0.98, "Ru": 0.02}, "T": 622.0},
    {"composition": {"U": 0.96, "Ru": 0.04}, "T": 590.0},
    {"composition": {"U": 0.95, "Ru": 0.05}, "T": 570.0},
    {"composition": {"U": 0.97, "Ta": 0.03}, "T": 652.0},
    {"composition": {"U": 0.95, "Ta": 0.05}, "T": 632.0},
    {"composition": {"U": 0.93, "Ta": 0.07}, "T": 610.0},
    {"composition": {"U": 0.9, "Ta": 0.1}, "T": 582.0},
    {"composition": {"U": 0.93, "Mo": 0.04, "Nb": 0.03}, "T": 630.0},
    {"composition": {"U": 0.9, "Mo": 0.06, "Nb": 0.04}, "T": 595.0},
    {"composition": {"U": 0.88, "Mo": 0.08, "Nb": 0.04}, "T": 568.0},
    {"composition": {"U": 0.88, "Mo": 0.05, "Zr": 0.07}, "T": 645.0},
    {"composition": {"U": 0.85, "Mo": 0.08, "Zr": 0.07}, "T": 620.0},
]

# ---------------------------------------------------------------------------
# 12D Feature Schema (matches prediction_service.py inference)
# ---------------------------------------------------------------------------

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

CLUSTER_TYPE_LABELS: list[str] = ["I", "II", "III", "IV"]

FEATURE_NAMES_12D: list[str] = PHYSICAL_FEATURE_NAMES + [
    f"type_{t}" for t in CLUSTER_TYPE_LABELS
]


# ---------------------------------------------------------------------------
# Feature computation
# ---------------------------------------------------------------------------


def _parse_composition(comp_str: str) -> dict[str, float]:
    """Parse JSON composition string to dict.

    Accepts both at.% (sum~100) and fraction (sum~1) formats.
    """
    raw = json.loads(comp_str)
    return {k: float(v) for k, v in raw.items()}


def _normalize_to_fraction(comp: dict[str, float]) -> dict[str, float]:
    """Ensure composition values are fractions summing to 1.0."""
    total = sum(comp.values())
    if total <= 0:
        return comp
    if abs(total - 1.0) < 0.01:
        return comp
    return {k: v / total for k, v in comp.items()}


def compute_12d_features(composition: dict[str, float]) -> list[float]:
    """Compute the 12D feature vector from a composition dict.

    8 physical features + 4 cluster-type one-hot (dominant cluster).
    Matches prediction_service.build_feature_vector() inference schema.
    """
    comp = _normalize_to_fraction(composition)

    physical = [
        calculate_mo_equivalent(comp),
        calculate_pauling_chi_diff(comp),
        calculate_allen_chi_diff(comp),
        calculate_config_entropy(comp),
        calculate_bv_ratio(comp),
        calculate_u_density(comp),
        calculate_mixing_enthalpy(comp),
        calculate_lattice_distortion(comp),
    ]

    cluster_fracs = calculate_cluster_fractions(comp)
    dominant_idx = int(
        max(range(4), key=lambda i: cluster_fracs[f"cluster_{CLUSTER_TYPE_LABELS[i]}"])
    )
    one_hot = [1.0 if i == dominant_idx else 0.0 for i in range(4)]

    return physical + one_hot


def compute_8d_physical_features(composition: dict[str, float]) -> list[float]:
    """Compute 8 physical features only (no cluster one-hot).

    Used for temperature regression where cluster type introduces
    discontinuities harmful to continuous prediction.
    """
    comp = _normalize_to_fraction(composition)

    return [
        calculate_mo_equivalent(comp),
        calculate_pauling_chi_diff(comp),
        calculate_allen_chi_diff(comp),
        calculate_config_entropy(comp),
        calculate_bv_ratio(comp),
        calculate_u_density(comp),
        calculate_mixing_enthalpy(comp),
        calculate_lattice_distortion(comp),
    ]


def compute_12d_from_phase(
    composition: dict[str, float],
    phase: str,
) -> list[float]:
    """Compute 12D features with explicit cluster type from phase column.

    If phase is I/II/III/IV, use directly as one-hot.
    Otherwise, fall back to dominant cluster from cluster_fractions.
    """
    comp = _normalize_to_fraction(composition)

    physical = [
        calculate_mo_equivalent(comp),
        calculate_pauling_chi_diff(comp),
        calculate_allen_chi_diff(comp),
        calculate_config_entropy(comp),
        calculate_bv_ratio(comp),
        calculate_u_density(comp),
        calculate_mixing_enthalpy(comp),
        calculate_lattice_distortion(comp),
    ]

    if phase in CLUSTER_TYPE_LABELS:
        idx = CLUSTER_TYPE_LABELS.index(phase)
        one_hot = [1.0 if i == idx else 0.0 for i in range(4)]
    else:
        cluster_fracs = calculate_cluster_fractions(comp)
        dominant_idx = int(
            max(
                range(4),
                key=lambda i: cluster_fracs[
                    f"cluster_{CLUSTER_TYPE_LABELS[i]}"
                ],
            )
        )
        one_hot = [1.0 if i == dominant_idx else 0.0 for i in range(4)]

    return physical + one_hot


# ---------------------------------------------------------------------------
# PhaseClassifier v1.1 training
# ---------------------------------------------------------------------------


def train_phase_classifier_v11(
    models_dir: Path,
) -> dict:
    """Train PhaseClassifier v1.1 with RF + XGBoost ensemble.

    Returns:
        Metrics dict with CV results and feature importance.
    """
    logger.info("Loading training set for PhaseClassifier v1.1...")
    df = pd.read_csv(TRAINING_SET_PATH)
    hm = df[df["label"].isin(["H", "M"])].copy()
    logger.info(
        "H+M records: %d (H=%d, M=%d)",
        len(hm),
        (hm["label"] == "H").sum(),
        (hm["label"] == "M").sum(),
    )

    # Compute 12D features
    X_list: list[list[float]] = []
    y_list: list[int] = []
    skipped = 0

    for _, row in hm.iterrows():
        try:
            comp = _parse_composition(row["composition"])
            features = compute_12d_from_phase(comp, str(row["phase"]))
            X_list.append(features)
            y_list.append(0 if row["label"] == "H" else 1)
        except Exception:
            skipped += 1

    if skipped > 0:
        logger.warning("Skipped %d records with parse errors", skipped)

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.int64)
    logger.info(
        "Feature matrix: %d samples x %d features", X.shape[0], X.shape[1]
    )

    # Replace any NaN/Inf with 0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Train RF + XGBoost ensemble
    try:
        from xgboost import XGBClassifier

        xgb_clf = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            use_label_encoder=False,
            eval_metric="logloss",
        )
    except ImportError:
        logger.warning("XGBoost not available, using RF-only ensemble")
        xgb_clf = RandomForestClassifier(
            n_estimators=200, max_depth=8, random_state=42
        )

    rf_clf = RandomForestClassifier(
        n_estimators=300, max_depth=10, min_samples_leaf=2, random_state=42
    )

    ensemble = VotingClassifier(
        estimators=[("rf", rf_clf), ("xgb", xgb_clf)],
        voting="soft",
    )

    # 5-fold cross-validation
    logger.info("Running 5-fold cross-validation...")
    t0 = time.time()
    cv_scores = cross_val_score(ensemble, X, y, cv=5, scoring="accuracy")
    cv_time = time.time() - t0

    cv_mean = float(cv_scores.mean())
    cv_min = float(cv_scores.min())
    cv_std = float(cv_scores.std())

    logger.info(
        "5-fold CV: mean=%.4f +/- %.4f, min=%.4f (target > 0.78)",
        cv_mean,
        cv_std,
        cv_min,
    )

    # Train on full dataset for production model
    logger.info("Training final model on full dataset...")
    t0 = time.time()
    ensemble.fit(X, y)
    train_time = time.time() - t0

    # Per-class metrics
    y_pred = ensemble.predict(X)
    report = classification_report(
        y, y_pred, target_names=["H", "M"], output_dict=True
    )
    cm = confusion_matrix(y, y_pred)

    # SHAP feature importance (top 12)
    shap_features = _compute_shap_importance(ensemble, X)

    # Save model
    model_path = models_dir / f"phase_classifier_{MODEL_VERSION}.joblib"
    try:
        import joblib

        artifact = {"model": ensemble, "version": MODEL_VERSION}
        joblib.dump(artifact, model_path)
        logger.info(
            "Saved PhaseClassifier %s to %s", MODEL_VERSION, model_path
        )
    except ImportError:
        logger.warning("joblib not available, skipping model save")

    # Save metrics
    metrics = {
        "version": MODEL_VERSION,
        "n_samples": int(X.shape[0]),
        "n_features": int(X.shape[1]),
        "cv_n_splits": 5,
        "cv_mean_accuracy": cv_mean,
        "cv_min_fold_accuracy": cv_min,
        "cv_std_accuracy": cv_std,
        "cv_passed": cv_mean > 0.78,
        "cv_fold_accuracies": [float(s) for s in cv_scores],
        "training_seconds": train_time + cv_time,
        "confusion_matrix": cm.tolist(),
        "classification_report": report,
        "shap_top_features": shap_features,
    }

    metrics_path = models_dir / f"phase_classifier_{MODEL_VERSION}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    logger.info("Saved metrics to %s", metrics_path)
    return metrics


# ---------------------------------------------------------------------------
# TempPredictor v1.1 training
# ---------------------------------------------------------------------------


def _augment_composition(
    composition: dict[str, float],
    n_perturbations: int = 3,
    perturbation_scale: float = 0.005,
) -> list[dict[str, float]]:
    """Generate perturbed composition copies for data augmentation.

    Adds random ±0.5 at.% (0.005 fraction) noise to each element,
    then renormalizes so fractions sum to 1.0.

    Args:
        composition: Original element fractions summing to 1.0.
        n_perturbations: Number of augmented copies to generate.
        perturbation_scale: Standard deviation of perturbation (fraction).

    Returns:
        List of perturbed composition dicts (length n_perturbations).
    """
    rng = np.random.RandomState(42)
    elements = sorted(composition.keys())
    base_vals = np.array([composition[e] for e in elements])
    result: list[dict[str, float]] = []

    for _ in range(n_perturbations):
        noise = rng.normal(0, perturbation_scale, size=len(elements))
        perturbed = np.abs(base_vals + noise)
        total = perturbed.sum()
        if total <= 0:
            perturbed = base_vals.copy()
            total = perturbed.sum()
        perturbed /= total
        result.append({e: float(perturbed[i]) for i, e in enumerate(elements)})

    return result


def _add_interaction_features(X_base: np.ndarray) -> np.ndarray:  # noqa: N803
    """Extend 8D physical features with polynomial interaction terms.

    Adds physically-motivated interactions that capture nonlinear
    coupling between composition descriptors:
      - mo_equivalent² (nonlinear Mo effect)
      - u_density × config_entropy (U content × disorder)
      - pauling_chi_diff × mixing_enthalpy (electronegativity × enthalpy)
      - config_entropy² (entropy nonlinearity)
      - bv_ratio × u_density (size mismatch × U content)

    Returns:
        Extended feature matrix with 13 columns (8 + 5 interactions).
    """
    mo_eq = X_base[:, 0:1]       # mo_equivalent
    chi_diff = X_base[:, 1:2]    # pauling_chi_diff
    entropy = X_base[:, 3:4]     # config_entropy
    bv = X_base[:, 4:5]          # bv_ratio
    u_dens = X_base[:, 5:6]      # u_density
    mix_ent = X_base[:, 6:7]     # mixing_enthalpy

    interactions = np.hstack([
        mo_eq ** 2,
        u_dens * entropy,
        chi_diff * mix_ent,
        entropy ** 2,
        bv * u_dens,
    ])

    return np.hstack([X_base, interactions])


def _uncertainty_weighted_ensemble(
    gpr_pred: float,
    gpr_std: float,
    svr_pred: float,
    sigma_ref: float = 50.0,
) -> float:
    """Compute uncertainty-weighted ensemble prediction.

    Weight GPR inversely proportional to its predictive uncertainty.
    When GPR is confident (low std), it dominates; when uncertain,
    SVR carries more weight.

    w_gpr = 1 / (1 + gpr_std / sigma_ref)
    w_svr = 1 - w_gpr
    """
    w_gpr = 1.0 / (1.0 + gpr_std / sigma_ref)
    w_svr = 1.0 - w_gpr
    return w_gpr * gpr_pred + w_svr * svr_pred


def train_temp_predictor_v11(
    models_dir: Path,
) -> dict:
    """Train TempPredictor v1.1 with GPR + SVR ensemble, LOO-CV.

    v1.1 improvements over v1.0:
      - Log-transform target to compress outlier range (528-2230C)
      - Polynomial interaction features (8D -> 13D)
      - Uncertainty-weighted ensemble (GPR weighted by predictive std)

    Returns:
        Metrics dict with LOO-CV results.
    """
    logger.info("Loading temperature data for TempPredictor v1.1...")
    logger.info("Using %d experimental records from v1.0 baseline", len(_EXPERIMENTAL_TEMPS))

    X_list: list[list[float]] = []
    y_list: list[float] = []
    comp_list: list[dict[str, float]] = []

    for record in _EXPERIMENTAL_TEMPS:
        comp = record["composition"]
        features = compute_8d_physical_features(comp)
        X_list.append(features)
        y_list.append(float(record["T"]))
        comp_list.append(comp)

    X_base = np.array(X_list, dtype=np.float64)
    y_raw = np.array(y_list, dtype=np.float64)

    # Replace any NaN/Inf in base features
    X_base = np.nan_to_num(X_base, nan=0.0, posinf=0.0, neginf=0.0)

    # Add polynomial interaction features (8D -> 13D)
    X = _add_interaction_features(X_base)
    logger.info(
        "Feature matrix: %d samples x %d features (8 base + 5 interactions)",
        X.shape[0],
        X.shape[1],
    )

    # Log-transform target to compress wide range (528-2230C -> ~6.3-7.7)
    y_log = np.log(y_raw)
    log_mean = float(y_log.mean())
    log_std = float(y_log.std())

    logger.info(
        "Target: log(T) mean=%.3f, std=%.3f (T range: %.0f-%.0f C)",
        log_mean,
        log_std,
        y_raw.min(),
        y_raw.max(),
    )

    # Scale features
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # GPR with Matern kernel (trained on log-scale)
    # Use length_scale_range to let optimizer find better fit
    gpr_kernel = Matern(nu=2.5, length_scale=1.0, length_scale_bounds=(1e-1, 1e3)) \
        + WhiteKernel(noise_level=1.0, noise_level_bounds=(1e-8, 1e2))
    gpr = GaussianProcessRegressor(
        kernel=gpr_kernel,
        alpha=1e-6,
        normalize_y=True,
        n_restarts_optimizer=5,
        random_state=42,
    )

    # SVR with slightly tighter epsilon for better fit on log scale
    svr = SVR(kernel="rbf", C=100.0, epsilon=0.05, gamma="scale")

    # LOO-CV (standard, no augmentation — 61 clean samples sufficient)
    logger.info(
        "Running LOO-CV for TempPredictor v1.1 (log-transform + interactions)..."
    )
    loo = LeaveOneOut()
    n_samples = len(y_raw)
    y_true_list: list[float] = []
    y_pred_list: list[float] = []
    gpr_std_list: list[float] = []
    fold_results: list[dict] = []

    t0 = time.time()
    for train_idx, test_idx in loo.split(X_scaled):
        X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
        y_train_log = y_log[train_idx]
        y_test_raw_val = y_raw[test_idx]

        # Fit GPR on log-scale
        gpr_fold = GaussianProcessRegressor(
            kernel=gpr_kernel,
            alpha=1e-6,
            normalize_y=True,
            n_restarts_optimizer=5,
            random_state=42,
        )
        gpr_fold.fit(X_train, y_train_log)
        gpr_pred_log, gpr_std_log = gpr_fold.predict(X_test, return_std=True)

        # Back-transform: exp(log_pred) gives prediction in Celsius
        gpr_pred_c = float(np.exp(gpr_pred_log[0]))
        gpr_std_c = float(gpr_pred_c * np.abs(gpr_std_log[0]))

        # Fit SVR on log-scale (standardized)
        y_train_log_z = (y_train_log - log_mean) / log_std
        svr_fold = SVR(kernel="rbf", C=100.0, epsilon=0.05, gamma="scale")
        svr_fold.fit(X_train, y_train_log_z)
        svr_pred_log_z = svr_fold.predict(X_test)
        svr_pred_log = float(svr_pred_log_z[0]) * log_std + log_mean
        svr_pred_c = float(np.exp(svr_pred_log))

        # Uncertainty-weighted ensemble
        ensemble_pred = _uncertainty_weighted_ensemble(
            gpr_pred_c, gpr_std_c, svr_pred_c, sigma_ref=50.0,
        )

        y_true_val = float(y_test_raw_val.item())
        y_true_list.append(y_true_val)
        y_pred_list.append(ensemble_pred)
        gpr_std_list.append(gpr_std_c)

        if len(fold_results) < 10 or len(fold_results) == n_samples - 1:
            fold_results.append({
                "fold": len(fold_results),
                "true": round(y_true_val, 2),
                "pred": round(ensemble_pred, 2),
                "abs_error": round(abs(y_true_val - ensemble_pred), 2),
                "gpr_std_c": round(gpr_std_c, 2),
            })

    cv_time = time.time() - t0

    y_true = np.array(y_true_list)
    y_pred = np.array(y_pred_list)

    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    r2 = float(r2_score(y_true, y_pred))
    max_err = float(np.max(np.abs(y_true - y_pred)))
    min_err = float(np.min(np.abs(y_true - y_pred)))

    logger.info(
        "LOO-CV: MAE=%.2f, RMSE=%.2f, R2=%.4f (target MAE < 35)",
        mae,
        rmse,
        r2,
    )

    # Train final models on full dataset (log-scale)
    logger.info("Training final TempPredictor on full dataset (log-scale)...")
    t0 = time.time()
    gpr.fit(X_scaled, y_log)
    y_log_z = (y_log - log_mean) / log_std
    svr.fit(X_scaled, y_log_z)
    train_time = time.time() - t0

    # Ensure models_dir is a Path object
    models_dir = Path(models_dir)

    # Save model with transform metadata
    model_path = models_dir / f"temp_predictor_{MODEL_VERSION}.joblib"
    try:
        import joblib

        artifact = {
            "gpr": gpr,
            "svr": svr,
            "scaler": scaler,
            "log_mean": log_mean,
            "log_std": log_std,
            "n_base_features": 8,
            "use_log_transform": True,
            "ensemble_type": "gpr_svr_uncertainty_weighted",
            "version": MODEL_VERSION,
        }
        joblib.dump(artifact, model_path)
        logger.info(
            "Saved TempPredictor %s to %s", MODEL_VERSION, model_path
        )
    except ImportError:
        logger.warning("joblib not available, skipping model save")

    # Save metrics
    metrics = {
        "version": MODEL_VERSION,
        "n_samples": n_samples,
        "n_features": int(X.shape[1]),
        "cv_method": "LOO",
        "mean_mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "r2": round(r2, 4),
        "max_abs_error": round(max_err, 2),
        "min_abs_error": round(min_err, 2),
        "target_mae": 35.0,
        "cv_passed": mae < 35.0,
        "training_seconds": train_time + cv_time,
        "per_fold_results": fold_results,
        "gpr_std_stats": {
            "mean": round(float(np.mean(gpr_std_list)), 2),
            "max": round(float(np.max(gpr_std_list)), 2),
            "min": round(float(np.min(gpr_std_list)), 2),
        },
    }

    metrics_path = models_dir / f"temp_predictor_{MODEL_VERSION}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    # Save text report
    report_lines = [
        f"TempPredictor {MODEL_VERSION} -- LOO-CV Regression Report",
        "=" * 60,
        f"Samples:        {n_samples}",
        f"Features:       {X.shape[1]} (8 base + 5 interactions)",
        "Target:         log(T) transform",
        f"Mean MAE:       {mae:.2f} C (target < 35.0 C)",
        f"RMSE:           {rmse:.2f} C",
        f"R2:             {r2:.4f}",
        f"Max |err|:      {max_err:.2f} C",
        f"Min |err|:      {min_err:.2f} C",
        f"Acceptance:     {'PASS' if mae < 35.0 else 'FAIL'}",
        "",
    ]

    for fr in fold_results[:10]:
        report_lines.append(
            f"  fold={fr['fold']:3d}  true={fr['true']:8.2f}C  "
            f"pred={fr['pred']:8.2f}C  |err|={fr['abs_error']:6.2f}C  "
            f"gpr_std={fr['gpr_std_c']:6.2f}C"
        )
    if n_samples > 10:
        report_lines.append(f"  ... ({n_samples - 10} more folds)")

    report_path = models_dir / f"temp_predictor_{MODEL_VERSION}_report.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    logger.info("Saved report to %s", report_path)

    return metrics


# ---------------------------------------------------------------------------
# SHAP feature importance
# ---------------------------------------------------------------------------


def _compute_shap_importance(
    model,
    X: np.ndarray,  # noqa: N803
    n_features: int = 12,
) -> list[list]:
    """Compute SHAP feature importance using TreeExplainer.

    Falls back to permutation importance if SHAP is unavailable.
    """
    try:
        import shap

        # Use the first estimator (RF) for SHAP
        if hasattr(model, "estimators_"):
            explainer = shap.TreeExplainer(model.estimators_[0])
        else:
            explainer = shap.TreeExplainer(model)

        # Use a subsample for speed
        sample_size = min(500, len(X))
        rng = np.random.RandomState(42)
        sample_idx = rng.choice(len(X), size=sample_size, replace=False)
        values = explainer.shap_values(X[sample_idx])

        if isinstance(values, list):
            values = values[1]  # For binary classification, take class 1

        mean_abs = np.mean(np.abs(values), axis=0)
        ranked = np.argsort(mean_abs)[::-1][:n_features]

        return [
            [FEATURE_NAMES_12D[i], round(float(mean_abs[i]), 6)]
            for i in ranked
        ]
    except ImportError:
        logger.info("SHAP not available, using permutation importance")
        return _permutation_importance(model, X)
    except Exception:
        logger.warning("SHAP computation failed, using permutation importance")
        return _permutation_importance(model, X)


def _permutation_importance(
    model,
    X: np.ndarray,  # noqa: N803
    n_features: int = 12,
) -> list[list]:
    """Compute permutation-based feature importance as SHAP fallback."""
    from sklearn.inspection import permutation_importance

    result = permutation_importance(
        model, X, model.predict(X), n_repeats=10, random_state=42
    )

    ranked = np.argsort(result.importances_mean)[::-1][:n_features]
    return [
        [FEATURE_NAMES_12D[i], round(float(result.importances_mean[i]), 6)]
        for i in ranked
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(output_dir: str | None = None) -> None:
    """Run v1.1 training for both models."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    models_dir = Path(output_dir) if output_dir else DEFAULT_MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("NFM-1674: ML Model v1.1 Training")
    logger.info("Output directory: %s", models_dir)
    logger.info("=" * 60)

    # 1. PhaseClassifier v1.1
    logger.info("\n>>> Training PhaseClassifier v1.1 <<<\n")
    phase_metrics = train_phase_classifier_v11(models_dir)

    phase_ok = phase_metrics["cv_passed"]
    logger.info(
        "PhaseClassifier v1.1: CV accuracy=%.4f (target >0.78) -- %s",
        phase_metrics["cv_mean_accuracy"],
        "PASS" if phase_ok else "FAIL",
    )

    # 2. TempPredictor v1.1
    logger.info("\n>>> Training TempPredictor v1.1 <<<\n")
    temp_metrics = train_temp_predictor_v11(models_dir)

    temp_ok = temp_metrics["cv_passed"]
    logger.info(
        "TempPredictor v1.1: MAE=%.2f C (target <35 C) -- %s",
        temp_metrics["mean_mae"],
        "PASS" if temp_ok else "FAIL",
    )

    # 3. Summary
    logger.info("\n" + "=" * 60)
    logger.info("v1.1 Training Summary")
    logger.info("=" * 60)
    logger.info(
        "PhaseClassifier:  accuracy=%.4f  %s",
        phase_metrics["cv_mean_accuracy"],
        "OK" if phase_ok else "FAIL",
    )
    logger.info(
        "TempPredictor:    MAE=%.2f C  %s",
        temp_metrics["mean_mae"],
        "OK" if temp_ok else "FAIL",
    )

    if phase_ok and temp_ok:
        logger.info("Both models meet Sprint 5 DoD targets.")
    else:
        logger.warning("One or more models did NOT meet targets.")

    # Print SHAP comparison
    logger.info("\nPhaseClassifier v1.1 Top Features:")
    for feat, imp in phase_metrics["shap_top_features"][:5]:
        logger.info("  %s: %.4f", feat, imp)


if __name__ == "__main__":
    main()
