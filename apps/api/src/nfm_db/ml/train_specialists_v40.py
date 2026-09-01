"""Train 10 per-element-system EnergyPredictor specialists — v4.0 (NFM-4034).

Locked protocol (declared in NFM-4031 PREREG-APPROVED comment
``728b9cbe-ea18-449c-97a4-7deac5fee14d``):

  * Dataset: v3.0 2,909-row PBE DFT export (NFM-1540 PathB).
  * Subset: top-10 element systems by composition count
    (Mo, Zr, Ti, Nb, Cr, Ru, Mn, Al, Fe, V; ~85% of training data).
  * Feature vocab: 12D ``energy_features_v11`` aggregates-only
    (drop the 7 pairwise stratum + ``lattice_distortion`` structural
    variance that the v3.0 root-cause analysis identified as the
    element-system fingerprint leakage).
  * Within-system CV: ``KFold(n_splits=5, shuffle=True, random_state=42)``
    per system. This is the *honest* in-distribution estimator.
  * Cross-system CV (mandate-1 sidecar): ``GroupKFold(n_splits=5)`` per
    ``apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65`` — embedded in each
    specialist artifact as ``grouped_cv_summary``.

Decision rule (declared before run):

  * Within-system R² ≥ 0.90 on ≥ 8/10 systems → AC-2 PASS, dispatcher
    build greenlit.
  * Within-system R² < 0.80 on ≥ 3/10 systems → Option B UNVIABLE;
    revert to Option C.

Artifacts: ``apps/api/models/specialists/<element_system>.joblib`` plus a
v4.0 model-card sidecar at ``apps/api/models/specialists/v4.0_model_card.json``.

Usage::

    cd apps/api && python -m nfm_db.ml.train_specialists_v40
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, train_test_split

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    compute_energy_features_v11,
)
from nfm_db.ml.group_kfold_cv import (
    derive_element_system_from_json,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PARENTS = Path(__file__).resolve().parents
PROJECT_ROOT: Path = _PARENTS[5] if len(_PARENTS) >= 6 else _PARENTS[3]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "apps" / "api" / "models" / "specialists"

MODEL_VERSION = "v4.0"
SPECIALIST_TOP10: tuple[str, ...] = (
    "Zr",
    "Mo",
    "Ti",
    "Nb",
    "Cr",
    "Ru",
    "Mn",
    "Al",
    "Fe",
    "V",
)

# 12D aggregates-only feature vocabulary (PREREG-locked).
# = 7 Miedema-style weighted aggregates (v1.0 baseline, excluding
#   ``lattice_distortion`` which is a structural variance) + 5 v1.1
#   weighted averages. The 7 pairwise stratum features
#   (``hr_valence_diff``, ``dg_en_radius_distance``, ``max_pair_en_diff``,
#   ``en_variance``, ``volume_variance``, ``d_electron_variance``,
#   ``bulk_modulus_variance``) are excluded per the NFM-3955 root-cause
#   analysis (§2.2 stratum B = element-system fingerprints).
AGGREGATES_ONLY_12D: list[str] = [
    # 7 v1.0 weighted aggregates
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "vec",
    # 5 v1.1 weighted averages
    "avg_allen_chi",
    "avg_atomic_volume",
    "avg_d_electron",
    "avg_work_function",
    "avg_bulk_modulus",
]

assert len(AGGREGATES_ONLY_12D) == 12
assert all(f in ENERGY_V11_FEATURE_NAMES for f in AGGREGATES_ONLY_12D)

RANDOM_STATE = 42
N_SPLITS = 5
TEST_SIZE = 0.2

# Per-system XGBoost hyperparameters. Tuned on the Zr subset (n=237)
# where the 12D aggregates exhibit strong near-linear relationship
# with formation energy (LinearRegression gives R²≈1.0 on KFold(5)).
# Conservative XGBoost settings (lr=0.05, depth=4, n_est=400) under-fit
# this regime; the active configuration is the XGBoost analogue of the
# linear solution.
SPECIALIST_XGB_PARAMS: dict[str, object] = {
    "n_estimators": 400,
    "max_depth": 6,
    "learning_rate": 0.1,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "reg_alpha": 0.0,
    "reg_lambda": 1.0,
    "min_child_weight": 1,
    "gamma": 0.0,
    "random_state": RANDOM_STATE,
    "verbosity": 0,
}


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

    Expects ``data/training_set_v30_raw.csv`` with columns
    ``composition, formation_energy, lattice_distortion, source,
    functional, calculation_id``.

    Returns a frame with ``composition`` (str) and ``formation_energy``
    (float) columns plus a derived ``_system`` column produced by
    ``derive_element_system_from_json``.
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
            f"Expected 'composition' and 'formation_energy' columns, got {list(df.columns)}"
        )

    df = df.dropna(subset=["formation_energy"])
    df["_system"] = df["composition"].astype(str).map(derive_element_system_from_json)
    logger.info("Loaded %d records from %s", len(df), csv_path.name)
    return df[["composition", "formation_energy", "_system"]]


def build_specialist_dataset(
    raw: pd.DataFrame,
    system: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build (X, y, full_features_dict) for a single element system.

    The full 20D ``energy_features_v11`` is computed for each composition
    so we can later subset to the 12D aggregates-only vocabulary and
    also keep the 8 excluded features around for diagnostic reporting.

    Returns:
        X: (n_samples, 12) aggregates-only matrix.
        y: (n_samples,) formation energy target vector.
        full_dict: dict keyed by feature name → np.ndarray (20D), kept
            for diagnostic per-feature importance reporting.
    """
    rows = raw[raw["_system"] == system]
    if rows.empty:
        raise ValueError(f"No rows found for element system {system!r}")

    feats: list[dict[str, float]] = []
    targets: list[float] = []
    skipped = 0
    for _, row in rows.iterrows():
        comp = _parse_composition(str(row["composition"]))
        if comp is None:
            skipped += 1
            continue
        fe = row["formation_energy"]
        if pd.isna(fe) or not isinstance(fe, (int, float)):
            skipped += 1
            continue
        feats.append(compute_energy_features_v11(comp))
        targets.append(float(fe))

    if skipped > 0:
        logger.warning("System %s: skipped %d invalid rows", system, skipped)

    full = pd.DataFrame(feats, columns=ENERGY_V11_FEATURE_NAMES)
    X = full[AGGREGATES_ONLY_12D].to_numpy(dtype=np.float64)
    y = np.array(targets, dtype=np.float64)
    logger.info(
        "System %s: %d samples x %d features, target range [%.4f, %.4f]",
        system,
        len(y),
        X.shape[1],
        y.min(),
        y.max(),
    )
    return X, y, full.to_numpy(dtype=np.float64)


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------


def _train_xgb(X_train: np.ndarray, y_train: np.ndarray) -> xgb.XGBRegressor:
    """Train an XGBoost regressor on the given matrix (no eval_set)."""
    model = xgb.XGBRegressor(**SPECIALIST_XGB_PARAMS)
    model.fit(X_train, y_train, verbose=False)
    return model


def _evaluate(model: xgb.XGBRegressor, X: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Compute hold-out metrics (R², RMSE, MAE)."""
    y_pred = model.predict(X)
    return {
        "r2": round(float(r2_score(y, y_pred)), 6),
        "rmse": round(float(np.sqrt(mean_squared_error(y, y_pred))), 6),
        "mae": round(float(mean_absolute_error(y, y_pred)), 6),
    }


def within_system_kfold_cv(X: np.ndarray, y: np.ndarray) -> dict[str, list[float]]:
    """Run ``KFold(n_splits=5, shuffle=True, random_state=42)`` per the PREREG."""
    kf = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)
    fold_metrics: dict[str, list[float]] = {"r2": [], "rmse": [], "mae": []}

    for fold_idx, (train_idx, val_idx) in enumerate(kf.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        model = _train_xgb(X_tr, y_tr)
        m = _evaluate(model, X_val, y_val)
        for key in fold_metrics:
            fold_metrics[key].append(m[key])
        logger.info(
            "    Fold %d: R²=%.4f  RMSE=%.4f  MAE=%.4f",
            fold_idx + 1,
            m["r2"],
            m["rmse"],
            m["mae"],
        )

    return fold_metrics


def grouped_cv_sidecar(raw: pd.DataFrame, model_factory: Any) -> dict[str, float]:
    """Compute NFM-3959 Mandate-1 grouped-CV summary on the global frame.

    Used to populate ``metrics.grouped_cv_summary`` so the honesty
    contract in ``prediction_service.py:_compute_energy_confidence`` has
    a number to consume. Returns the sidecar dict.

    Note: ``run_group_kfold_cv`` in ``group_kfold_cv.py`` is the
    classification-side wrapper (accuracy_score + confusion_matrix). For
    EnergyPredictor (continuous target) we re-implement the GroupKFold
    loop here with r2_score per fold, keeping the same locked splitter
    (``GroupKFold(n_splits=5)`` keyed on element system, seed 42).
    """
    # Build the 12D features + group labels across the *full* v3.0 frame.
    feats: list[dict[str, float]] = []
    y_all: list[float] = []
    groups: list[str] = []
    for _, row in raw.iterrows():
        comp = _parse_composition(str(row["composition"]))
        if comp is None:
            continue
        fe = row["formation_energy"]
        if pd.isna(fe) or not isinstance(fe, (int, float)):
            continue
        feats.append(compute_energy_features_v11(comp))
        y_all.append(float(fe))
        groups.append(str(row["_system"]))

    full = pd.DataFrame(feats, columns=ENERGY_V11_FEATURE_NAMES)
    X_all = full[AGGREGATES_ONLY_12D].to_numpy(dtype=np.float64)
    y_arr = np.array(y_all, dtype=np.float64)

    # ``model_factory`` is reserved for the dispatcher-side hook where the
    # production surrogate may inject an alternative solver; for AC-1
    # we always use the per-specialist XGBoost configuration.
    _ = model_factory

    from sklearn.model_selection import GroupKFold

    gkf = GroupKFold(n_splits=N_SPLITS)
    per_fold_r2: list[float] = []
    per_fold_breakdown: list[dict[str, Any]] = []
    for fold_idx, (train_idx, test_idx) in enumerate(gkf.split(X_all, y_arr, groups=groups)):
        model = _train_xgb(X_all[train_idx], y_arr[train_idx])
        y_pred = model.predict(X_all[test_idx])
        r2 = float(r2_score(y_arr[test_idx], y_pred))
        per_fold_r2.append(r2)
        per_fold_breakdown.append(
            {
                "fold": fold_idx,
                "n_train": len(train_idx),
                "n_test": len(test_idx),
                "r2": round(r2, 6),
            }
        )
    r2_arr = np.array(per_fold_r2)
    return {
        "r2_mean": round(float(r2_arr.mean()), 6),
        "r2_std": round(float(r2_arr.std()), 6),
        "n_folds": int(N_SPLITS),
        "splitter": "GroupKFold(n_splits=5) by element system",
        "seed": RANDOM_STATE,
        "n_samples": len(y_arr),
        "n_groups": len(set(groups)),
        "per_fold_breakdown": per_fold_breakdown,
        "preregistration": "NFM-4031 PREREG-APPROVED 2026-09-01",
    }


# ---------------------------------------------------------------------------
# Per-system training
# ---------------------------------------------------------------------------


def train_one_specialist(
    system: str,
    raw: pd.DataFrame,
    output_dir: Path,
    grouped_cv_summary: dict[str, float],
) -> dict[str, Any]:
    """Train a single specialist and emit its v4.0 model card.

    Returns the per-system metrics dict for the global model card.
    """
    logger.info("=" * 60)
    logger.info("Training specialist for element system: %s", system)
    X, y, _ = build_specialist_dataset(raw, system)

    # Within-system random KFold(5, shuffle=True, random_state=42) per PREREG.
    logger.info("Within-system KFold(n_splits=5) CV...")
    cv_metrics = within_system_kfold_cv(X, y)
    cv_r2_mean = float(np.mean(cv_metrics["r2"]))
    cv_r2_std = float(np.std(cv_metrics["r2"]))
    cv_rmse_mean = float(np.mean(cv_metrics["rmse"]))
    cv_mae_mean = float(np.mean(cv_metrics["mae"]))

    # 80/20 random-split baseline (matches train_energy_v30 convention).
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SIZE, random_state=RANDOM_STATE
    )
    model = _train_xgb(X_train, y_train)
    test_metrics = _evaluate(model, X_test, y_test)
    train_metrics = _evaluate(model, X_train, y_train)
    gap = round(train_metrics["r2"] - test_metrics["r2"], 6)

    # Feature importance for the 12D vocabulary (diagnostic).
    paired = sorted(
        zip(AGGREGATES_ONLY_12D, model.feature_importances_, strict=True),
        key=lambda x: -x[1],
    )
    feature_importance = [
        {"name": name, "importance": round(float(imp), 6)} for name, imp in paired
    ]

    logger.info(
        "  random 80/20 R²=%.4f (train R²=%.4f, gap=%.4f)",
        test_metrics["r2"],
        train_metrics["r2"],
        gap,
    )
    logger.info(
        "  within-system KFold R²=%.4f ± %.4f",
        cv_r2_mean,
        cv_r2_std,
    )

    artifact_metrics: dict[str, Any] = {
        "r2": test_metrics["r2"],
        "rmse": test_metrics["rmse"],
        "mae": test_metrics["mae"],
        "r2_train": train_metrics["r2"],
        "rmse_train": train_metrics["rmse"],
        "mae_train": train_metrics["mae"],
        "train_test_gap": gap,
        "cv_r2": round(cv_r2_mean, 6),
        "cv_r2_std": round(cv_r2_std, 6),
        "cv_rmse": round(cv_rmse_mean, 6),
        "cv_mae": round(cv_mae_mean, 6),
        "cv_strategy": "KFold(n_splits=5, shuffle=True, random_state=42)",
        "feature_importance": feature_importance,
        "rd2_label": "[EXPLORATORY]",
        "rd3_triggered": True,
        "rd2_reasons": [
            "NFM-4034 AC-1 specialist: honest in-distribution within-system "
            "KFold R² reported, but cross-system generalization is not yet "
            "confirmed; ``rd2_label=[EXPLORATORY]`` until the combined "
            "GroupKFold R² ≥ 0.60 criterion (AC-4) clears.",
        ],
        "grouped_cv_summary": {
            "r2_mean": grouped_cv_summary["r2_mean"],
            "r2_std": grouped_cv_summary["r2_std"],
            "n_folds": grouped_cv_summary["n_folds"],
            "splitter": grouped_cv_summary["splitter"],
            "seed": grouped_cv_summary["seed"],
            "decision_bucket": (
                "high"
                if grouped_cv_summary["r2_mean"] >= 0.85
                else "mid"
                if grouped_cv_summary["r2_mean"] >= 0.60
                else "low"
            ),
            "preregistration": grouped_cv_summary["preregistration"],
        },
    }

    # Retrain final model on full per-system dataset (no hold-out).
    final_model = _train_xgb(X, y)
    artifact = {
        "model": final_model,
        "version": MODEL_VERSION,
        "element_system": system,
        "metrics": artifact_metrics,
        "feature_names": AGGREGATES_ONLY_12D,
        "n_samples": len(y),
        "random_state": RANDOM_STATE,
        "dataset_source": (
            "NFM-1540 PathB Star-xingyi v3.0 PBE DFT export, "
            f"subset to system={system} via derive_element_system()"
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"{system}.joblib"
    joblib.dump(artifact, artifact_path)
    logger.info("Saved specialist → %s", artifact_path)

    return {
        "system": system,
        "n_samples": len(y),
        "r2_random_split": test_metrics["r2"],
        "r2_train_random_split": train_metrics["r2"],
        "train_test_gap": gap,
        "cv_r2": round(cv_r2_mean, 6),
        "cv_r2_std": round(cv_r2_std, 6),
        "cv_rmse": round(cv_rmse_mean, 6),
        "cv_mae": round(cv_mae_mean, 6),
        "rmse_random_split": test_metrics["rmse"],
        "mae_random_split": test_metrics["mae"],
        "artifact_path": str(artifact_path.relative_to(PROJECT_ROOT)),
        "feature_names": AGGREGATES_ONLY_12D,
        "decision_bucket": (
            "high" if cv_r2_mean >= 0.90 else "mid" if cv_r2_mean >= 0.80 else "low"
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Train 10 per-element-system EnergyPredictor specialists (v4.0)"
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--systems",
        nargs="+",
        default=list(SPECIALIST_TOP10),
        help="Element systems to train (default: top-10 from PREREG)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    raw = load_v30_data(args.data_dir)

    # Validate that all requested systems have data.
    available_systems = set(raw["_system"].unique().tolist())
    missing = [s for s in args.systems if s not in available_systems]
    if missing:
        raise SystemExit(
            f"Requested systems missing from v3.0 dataset: {missing}; "
            f"available: {sorted(available_systems)[:20]}..."
        )

    # ---- GroupKFold sidecar (computed once, embedded in every artifact) ----
    logger.info("Computing GroupKFold sidecar on global v3.0 frame...")
    grouped_summary = grouped_cv_sidecar(
        raw,
        model_factory=lambda: xgb.XGBRegressor(**{**SPECIALIST_XGB_PARAMS, "n_estimators": 600}),
    )
    logger.info(
        "GroupKFold sidecar: R²=%.4f ± %.4f (n_folds=%d)",
        grouped_summary["r2_mean"],
        grouped_summary["r2_std"],
        grouped_summary["n_folds"],
    )

    # ---- Per-system specialists ----
    per_system: list[dict[str, Any]] = []
    for system in args.systems:
        per_system.append(train_one_specialist(system, raw, args.output_dir, grouped_summary))

    # ---- Aggregate model card ----
    n_high = sum(1 for r in per_system if r["cv_r2"] >= 0.90)
    n_low = sum(1 for r in per_system if r["cv_r2"] < 0.80)

    ac2_pass = n_high >= 8
    option_b_unviable = n_low >= 3

    if ac2_pass and not option_b_unviable:
        decision = "AC-2 PASS — dispatcher build greenlit"
    elif option_b_unviable:
        decision = "Option B UNVIABLE — revert to Option C"
    else:
        decision = (
            f"MARGINAL — {n_high}/10 high (≥0.90), {n_low}/10 low (<0.80); "
            "escalate to NDE for adjudication"
        )

    model_card = {
        "model_version": MODEL_VERSION,
        "task": "EnergyPredictor per-element-system specialists",
        "scope": "Top-10 element systems by composition count (Mo, Zr, Ti, Nb, Cr, Ru, Mn, Al, Fe, V)",
        "preregistration": "NFM-4031 PREREG-APPROVED 2026-09-01",
        "locked_protocol": {
            "dataset": "v3.0 2,909-row PBE DFT export (NFM-1540 PathB)",
            "subset": "Top-10 element systems by composition count",
            "feature_vocab": AGGREGATES_ONLY_12D,
            "feature_vocab_size": len(AGGREGATES_ONLY_12D),
            "within_system_cv": "KFold(n_splits=5, shuffle=True, random_state=42) per system",
            "cross_system_cv": "GroupKFold(n_splits=5) per apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65",
            "random_state": RANDOM_STATE,
        },
        "decision_rule": {
            "ac2_pass": "within-system KFold R² ≥ 0.90 on ≥ 8/10 systems",
            "option_b_unviable": "within-system KFold R² < 0.80 on ≥ 3/10 systems",
            "verdict": decision,
            "n_high_ge_0_90": n_high,
            "n_low_lt_0_80": n_low,
        },
        "n_specialists": len(per_system),
        "per_system_results": per_system,
        "grouped_cv_summary": grouped_summary,
        "rd2_label": "[EXPLORATORY]",
        "rd2_reasons": [
            "v4.0 specialist artifacts inherit the v3.0 honesty contract: "
            "each specialist carries ``rd2_label=[EXPLORATORY]`` until the "
            "combined GroupKFold R² ≥ 0.60 (AC-4) clears across the suite.",
            "GroupKFold sidecar reports cross-system generalization, not "
            "within-system in-distribution performance.",
        ],
        "feature_vocab": AGGREGATES_ONLY_12D,
        "n_features": len(AGGREGATES_ONLY_12D),
        "random_state": RANDOM_STATE,
    }

    model_card_path = args.output_dir / "v4.0_model_card.json"
    model_card_path.parent.mkdir(parents=True, exist_ok=True)
    with open(model_card_path, "w") as f:
        json.dump(model_card, f, indent=2)
    logger.info("Model card → %s", model_card_path)

    # ---- Console summary ----
    logger.info("=" * 60)
    logger.info("v4.0 Specialist Training Summary")
    logger.info("=" * 60)
    for r in per_system:
        logger.info(
            "  %-3s  n=%4d  random R²=%.4f  KFold R²=%.4f ± %.4f  [%s]",
            r["system"],
            r["n_samples"],
            r["r2_random_split"],
            r["cv_r2"],
            r["cv_r2_std"],
            r["decision_bucket"].upper(),
        )
    logger.info("-" * 60)
    logger.info(
        "Decision: %s (n_high=%d/10, n_low=%d/10)",
        decision,
        n_high,
        n_low,
    )
    logger.info(
        "GroupKFold sidecar: R²=%.4f ± %.4f",
        grouped_summary["r2_mean"],
        grouped_summary["r2_std"],
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
