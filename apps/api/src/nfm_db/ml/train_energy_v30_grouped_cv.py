"""Confirmatory grouped-CV re-evaluation of EnergyPredictor v3.0 (NFM-3953).

Single-variable isolation experiment. Only the CV splitter changes vs.
``train_energy_v30.py``: random ``KFold(shuffle=True)`` → ``GroupKFold(n_splits=5)``
grouped by element system, the same protocol PhaseClassifier v2.0 is held to
(NFM-1756 / train_v20.py:172-183). The dataset, feature set, hyperparameters,
and seed are all locked, so the delta is attributable to the splitter alone.

Reported figures (per NDE PREREG-APPROVED 2026-08-31T22:13Z addendum):
  - headline grouped R² = mean ± std across the 5 grouped folds
  - per-fold R², RMSE, MAE for transparency
  - element-system heterogeneity stats (n_groups, group-size distribution)

Decision rule (declared in PREREG-SUBMITTED, NFM-3953):
    Grouped R² ≥ 0.93 → keep v3.0 as prod default, republish metrics card
    0.85 ≤ Grouped R² < 0.93 → keep v3.0, grouped figure replaces random-split number
    Grouped R² < 0.85 → demote 0.9858 headline, open RD-3 anomaly review, notify LE

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v30_grouped_cv
    cd apps/api && python -m nfm_db.ml.train_energy_v30_grouped_cv --data-dir /path/to/data
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import GroupKFold

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
)
from nfm_db.ml.group_kfold_cv import build_group_labels
from nfm_db.ml.train_energy_v30 import (
    DATA_DIR,
    DEFAULT_MODELS_DIR,
    RANDOM_STATE,
    XGB_PARAMS,
    _parse_composition,
    build_dataset,
    evaluate,
    load_v30_data,
)

logger = logging.getLogger(__name__)

N_SPLITS = 5
RUN_TAG = "v3.0-groupedcv-NFM-3953"
DECISION_HIGH = 0.93
DECISION_MID = 0.85
INCUMBENT_RANDOM_KFOLD_CV_R2 = 0.9678  # from energy_predictor_v3.0_metrics.json: cv_r2


@dataclass(frozen=True)
class FoldResult:
    """Per-fold grouped-CV metrics."""

    fold_index: int
    n_train: int
    n_test: int
    n_test_groups: int
    r2: float
    rmse: float
    mae: float


@dataclass(frozen=True)
class GroupedCVSummary:
    """Headline grouped-CV result + provenance."""

    run_tag: str
    n_samples: int
    n_features: int
    n_groups: int
    n_splits: int
    random_state: int
    feature_names: tuple[str, ...]
    per_fold: tuple[FoldResult, ...]
    r2_mean: float
    r2_std: float
    rmse_mean: float
    mae_mean: float
    incumbent_random_kfold_r2: float

    def decision_bucket(self) -> str:
        if self.r2_mean >= DECISION_HIGH:
            return "high"
        if self.r2_mean >= DECISION_MID:
            return "mid"
        return "low"

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["decision_bucket"] = self.decision_bucket()
        d["delta_vs_random_kfold"] = round(self.r2_mean - self.incumbent_random_kfold_r2, 4)
        return d


def derive_kept_compositions(
    raw: pd.DataFrame,
) -> list[dict[str, float]]:
    """Re-derive the list of composition dicts that build_dataset kept.

    build_dataset drops rows where ``_parse_composition`` returns None or
    ``formation_energy`` is NaN/non-numeric. We replicate that filter so the
    group labels align 1:1 with the X, y returned by build_dataset.
    """
    kept: list[dict[str, float]] = []
    for _, row in raw.iterrows():
        comp = _parse_composition(str(row["composition"]))
        if comp is None:
            continue
        fe = row["formation_energy"]
        if pd.isna(fe) or not isinstance(fe, (int, float)):
            continue
        kept.append(comp)
    return kept


def run_grouped_cv(
    X: np.ndarray,
    y: np.ndarray,
    groups: list[str],
    *,
    n_splits: int = N_SPLITS,
    random_state: int = RANDOM_STATE,
) -> list[FoldResult]:
    """Run GroupKFold(n_splits) on the locked v3.0 training pipeline."""
    gkf = GroupKFold(n_splits=n_splits)
    folds: list[FoldResult] = []

    for fold_idx, (train_idx, test_idx) in enumerate(gkf.split(X, y, groups=groups)):
        X_tr, X_val = X[train_idx], X[test_idx]
        y_tr, y_val = y[train_idx], y[test_idx]
        test_groups = [groups[i] for i in test_idx]

        model = xgb.XGBRegressor(**{**XGB_PARAMS, "random_state": random_state})
        model.fit(X_tr, y_tr, verbose=False)

        metrics = evaluate(model, X_val, y_val)
        folds.append(
            FoldResult(
                fold_index=fold_idx,
                n_train=len(train_idx),
                n_test=len(test_idx),
                n_test_groups=len(set(test_groups)),
                r2=metrics["r2"],
                rmse=metrics["rmse"],
                mae=metrics["mae"],
            )
        )
        logger.info(
            "Fold %d: R²=%.4f, RMSE=%.6f, MAE=%.6f (n_test=%d, n_test_groups=%d)",
            fold_idx + 1,
            metrics["r2"],
            metrics["rmse"],
            metrics["mae"],
            len(test_idx),
            len(set(test_groups)),
        )

    return folds


def group_size_stats(groups: list[str]) -> dict[str, object]:
    """Element-system heterogeneity stats for transparency."""
    counts = Counter(groups)
    sizes = sorted(counts.values(), reverse=True)
    return {
        "n_unique_groups": len(counts),
        "min_group_size": int(sizes[-1]) if sizes else 0,
        "max_group_size": int(sizes[0]) if sizes else 0,
        "median_group_size": float(np.median(sizes)) if sizes else 0.0,
        "groups_with_fewer_than_n_split_members": int(sum(1 for s in sizes if s < N_SPLITS)),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="EnergyPredictor v3.0 grouped-CV confirmatory run (NFM-3953)",
    )
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
        help="Output directory for grouped-CV metrics sidecar JSON",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # ---- Data: identical to train_energy_v30.py (single-variable isolation) ----
    raw = load_v30_data(args.data_dir)
    X, y = build_dataset(raw)
    kept_comps = derive_kept_compositions(raw)
    groups = build_group_labels(kept_comps)

    if len(groups) != len(y):
        raise RuntimeError(
            f"Group/composition length mismatch: groups={len(groups)}, y={len(y)}. "
            "Investigate before reporting."
        )

    n_groups = len(set(groups))
    logger.info("=== EnergyPredictor v3.0 Grouped-CV (NFM-3953) ===")
    logger.info(
        "Dataset: %d samples, %d features, %d element-system groups",
        len(y),
        X.shape[1],
        n_groups,
    )
    logger.info("Splitter: GroupKFold(n_splits=%d), seed=%d", N_SPLITS, RANDOM_STATE)
    logger.info("Hyperparameters: locked to XGB_PARAMS in train_energy_v30.py")

    if n_groups < N_SPLITS:
        logger.warning(
            "Fewer element-system groups (%d) than splits (%d). GroupKFold will "
            "reduce n_splits internally; the effective split count is reported below.",
            n_groups,
            N_SPLITS,
        )

    heterogeneity = group_size_stats(groups)
    logger.info("Group heterogeneity: %s", heterogeneity)

    folds = run_grouped_cv(X, y, groups, n_splits=N_SPLITS, random_state=RANDOM_STATE)

    r2_array = np.array([f.r2 for f in folds])
    rmse_array = np.array([f.rmse for f in folds])
    mae_array = np.array([f.mae for f in folds])

    summary = GroupedCVSummary(
        run_tag=RUN_TAG,
        n_samples=len(y),
        n_features=int(X.shape[1]),
        n_groups=n_groups,
        n_splits=len(folds),
        random_state=RANDOM_STATE,
        feature_names=tuple(ENERGY_V11_FEATURE_NAMES),
        per_fold=tuple(folds),
        r2_mean=float(round(r2_array.mean(), 4)),
        r2_std=float(round(r2_array.std(ddof=0), 4)),
        rmse_mean=float(round(rmse_array.mean(), 6)),
        mae_mean=float(round(mae_array.mean(), 6)),
        incumbent_random_kfold_r2=INCUMBENT_RANDOM_KFOLD_CV_R2,
    )

    logger.info("=== Grouped-CV Headline ===")
    logger.info(
        "Grouped R²: %.4f ± %.4f (mean ± std across %d folds)",
        summary.r2_mean,
        summary.r2_std,
        summary.n_splits,
    )
    logger.info("Grouped RMSE: %.6f", summary.rmse_mean)
    logger.info("Grouped MAE:  %.6f", summary.mae_mean)
    logger.info(
        "Delta vs incumbent random-KFold R² (cv_r2=%.4f): %+.4f",
        summary.incumbent_random_kfold_r2,
        summary.r2_mean - summary.incumbent_random_kfold_r2,
    )
    bucket = summary.decision_bucket()
    if bucket == "high":
        logger.info(
            "Decision bucket: HIGH (>= %.2f). v3.0 generalizes across element systems.",
            DECISION_HIGH,
        )
    elif bucket == "mid":
        logger.info(
            "Decision bucket: MID (%.2f – %.2f). v3.0 stays prod default; grouped figure replaces random-split number.",
            DECISION_MID,
            DECISION_HIGH,
        )
    else:
        logger.warning(
            "Decision bucket: LOW (< %.2f). Random split was materially optimistic. "
            "Demote 0.9858 headline, open RD-3 anomaly review, notify Lead Engineer.",
            DECISION_MID,
        )

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / "energy_predictor_v3.0_groupedcv_metrics.json"
    payload = summary.to_dict()
    payload["group_heterogeneity"] = heterogeneity
    payload["protocol_locked"] = {
        "splitter": "GroupKFold",
        "n_splits_declared": N_SPLITS,
        "n_splits_effective": len(folds),
        "grouping_key": "element_system (sorted non-U elements)",
        "feature_set": "energy_features_v11 (20D, locked)",
        "xgb_params_locked_to": "XGB_PARAMS in train_energy_v30.py:149-161",
        "seed": RANDOM_STATE,
        "single_variable_delta": "splitter only (random KFold -> GroupKFold)",
        "preregistration": "NFM-3953 PREREG-APPROVED 2026-08-31T22:13Z",
    }
    sidecar_path.write_text(json.dumps(payload, indent=2))
    logger.info("Sidecar metrics written to %s", sidecar_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
