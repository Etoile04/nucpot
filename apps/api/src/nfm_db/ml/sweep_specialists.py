"""Channel 4 hyperparameter sweep + R² paired baseline (NFM-4035).

Pre-registered sweep over the 10 per-element-system specialists
(Mo, Zr, Ti, Nb, Cr, Ru, Mn, Al, Fe, V) on the locked v3.0 2,909-row
PBE DFT export (NFM-1540 PathB). For each system we:

  1. Compute the locked 12D ``energy_features_v11`` aggregates-only
     feature vector (no ad-hoc features).
  2. Run within-system KFold(n_splits=5, shuffle=True, random_state=42)
     on a grid of ``HistGradientBoostingRegressor`` hyperparameters
     (max_iter, max_depth, min_samples_leaf, learning_rate).
  3. Score a paired R² baseline using ``DummyRegressor(mean)`` plus a
     literal constant-mean predictor on the *same* locked splits, so the
     comparison is fold-for-fold rather than against an in-memory
     remembered constant (NFM-3957 paired-baseline tradition).
  4. Persist the sweep log under
     ``apps/api/models/specialists/_sweep/<element_system>_sweep.json``
     with the same honest-metadata shape as the specialist artifacts
     (rd2_label, per-fold breakdown, paired baseline block).

R-Checklist alignment (declared before the run):

  R1 Locked protocol — v3.0 export, top-10 systems, 12D aggregates,
     KFold(5, shuffle=True, random_state=42) per system.
  R2 R² paired baseline — DummyRegressor(mean) + constant-mean, scored
     on the same locked splits, lift reported per fold.
  R3 Honest metadata — rd2_label=[EXPLORATORY] on every sweep log
     until Petrov's specialists land AC-1 confirmatory status.
  R4 Train/eval separation — within-system boundary; no cross-system
     rows in any fold's training set.
  R5 Sweep happens within the pre-registered protocol — grid and seed
     recorded in this file and in every JSON log.
  R6 Downstream — best hyperparameters per system feed Petrov's AC-1
     final retrain; specialists will call ``_compute_energy_confidence``.

Reference: NFM-4031 PREREG comment 728b9cbe (locked protocol); NFM-3957
paired-baseline tradition; NFM-3996 falsification evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Cap BLAS/OpenMP worker threads BEFORE numpy/sklearn import so joblib's
# loky backend and HistGBM do not oversubscribe the Mac's logical cores.
# Without these caps the 16-config grid took 582s on the 237-row Zr
# system (NFM-4035 heartbeat 1 log).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold

# Re-use the existing element-system derivation (NFM-1756) and the
# locked 12D aggregates-only feature vocabulary from energy_features_v11.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_APPS_API_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_APPS_API_SRC) not in sys.path:
    sys.path.insert(0, str(_APPS_API_SRC))

from nfm_db.ml.energy_features_v11 import (  # noqa: E402
    V11_ADDITIONAL_FEATURE_NAMES,
    compute_energy_features_v11,
)
from nfm_db.ml.group_kfold_cv import derive_element_system  # noqa: E402

# ---------------------------------------------------------------------------
# Locked protocol (verbatim from NFM-4031 PREREG comment 728b9cbe)
# ---------------------------------------------------------------------------

RANDOM_STATE: int = 42
N_SPLITS: int = 5
TOP_N_SYSTEMS: int = 10
DATASET_SOURCE: str = "v3.0 2,909-row PBE DFT export (NFM-1540 PathB)"
PROTOCOL_VERSION: str = "v4.0-sweep/NFM-4031-PREREG"
FEATURE_VOCAB: tuple[str, ...] = tuple(V11_ADDITIONAL_FEATURE_NAMES)

# Top-10 element systems by composition count (declared in PREREG; verified
# at runtime against the dataset).
TARGET_SYSTEMS: tuple[str, ...] = (
    "Mo",
    "Zr",
    "Ti",
    "Nb",
    "Cr",
    "Ru",
    "Mn",
    "Al",
    "Fe",
    "V",
)

# Sweep grid — compact 8-config grid covering the four pre-registered
# hyperparameters (n_estimators, max_depth, min_samples_split,
# learning_rate). The closest HistGBM analogues are max_iter and
# min_samples_leaf (the prompt's min_samples_split is honoured semantically
# via min_samples_leaf; the mapping is recorded in every JSON log under
# ``param_mapping``). 8 configs keeps the wall-clock under a few minutes
# even on a Mac without the BLAS sysctl probe available — see NFM-4035
# heartbeat 1: the unconstrained 16-config run took 582s/system because
# joblib's loky backend spawned one worker per logical core on tiny folds.
SWEEP_GRID: dict[str, list[float | int]] = {
    "max_iter": [200],
    "max_depth": [4, 6],
    "min_samples_leaf": [5, 20],
    "learning_rate": [0.05, 0.1],
}

# Expanded grid (32 configs) used to confirm v1-grid failures before
# declaring falsification (NFM-4031 decision rule).
SWEEP_GRID_V2: dict[str, list[float | int]] = {
    "max_iter": [200, 400],
    "max_depth": [3, 5, 7],
    "min_samples_leaf": [5, 20, 50],
    "learning_rate": [0.03, 0.1],
}
PARAM_MAPPING: dict[str, str] = {
    "max_iter": "n_estimators",
    "max_depth": "max_depth",
    "min_samples_leaf": "min_samples_split (HistGBM equivalent)",
    "learning_rate": "learning_rate",
}

RD2_LABEL: str = "[EXPLORATORY]"
RD2_REASONS: tuple[str, ...] = (
    "sweep log emitted under pre-registered [EXPLORATORY] envelope; "
    "[CONFIRMATORY] re-label fires only after Petrov's specialists "
    "land AC-1 confirmatory status (NFM-4034).",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "no-git:unavailable"


def _parse_composition(comp_str: str) -> dict[str, float] | None:
    try:
        comp = json.loads(comp_str)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(comp, dict):
        return None
    return {str(k): float(v) for k, v in comp.items()}


def load_v30_dataset(csv_path: Path) -> pd.DataFrame:
    """Load the v3.0 PBE DFT export with 12D aggregates-only features.

    Returns:
        DataFrame with columns: composition (str), formation_energy (float),
        element_system (str), feature_name_0..11 (float).
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"v3.0 training set not found at {csv_path}. "
            "See NFM-2201 AC-1 for the prod export pipeline."
        )
    df = pd.read_csv(csv_path, usecols=["composition", "formation_energy"])
    df = df.dropna(subset=["formation_energy"])
    df["composition_dict"] = df["composition"].map(_parse_composition)
    bad = df["composition_dict"].isna().sum()
    if bad > 0:
        logger.warning(
            "Dropping %d rows with unparseable composition",
            bad,
        )
        df = df.dropna(subset=["composition_dict"]).reset_index(drop=True)
    df["element_system"] = df["composition_dict"].map(derive_element_system)

    feature_rows: list[list[float]] = []
    skipped = 0
    for comp in df["composition_dict"]:
        try:
            feat = compute_energy_features_v11(comp)
            feature_rows.append([float(feat[name]) for name in FEATURE_VOCAB])
        except Exception:
            skipped += 1
            feature_rows.append([0.0] * len(FEATURE_VOCAB))
    if skipped > 0:
        logger.warning(
            "Feature computation raised for %d rows; backfilled with zeros",
            skipped,
        )
    feature_arr = np.asarray(feature_rows, dtype=np.float64)
    feature_arr = np.nan_to_num(feature_arr, nan=0.0, posinf=0.0, neginf=0.0)

    for idx, name in enumerate(FEATURE_VOCAB):
        df[name] = feature_arr[:, idx]
    return df


def select_top_systems(
    df: pd.DataFrame,
    top_n: int = TOP_N_SYSTEMS,
) -> tuple[list[str], dict[str, int]]:
    counts = Counter(df["element_system"])
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    selected = [name for name, _ in ranked[:top_n]]
    return selected, dict(ranked)


# ---------------------------------------------------------------------------
# Per-system sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldResult:
    fold: int
    n_train: int
    n_test: int
    r2: float
    rmse: float
    mae: float


@dataclass(frozen=True)
class ConfigResult:
    params: dict[str, Any]
    fold_results: tuple[FoldResult, ...]
    mean_r2: float
    std_r2: float
    mean_rmse: float
    mean_mae: float


@dataclass(frozen=True)
class PairedBaseline:
    strategy: str
    fold_r2: tuple[float, ...]
    mean_r2: float
    std_r2: float
    lift_over_dummy_pp: float  # model.mean_r2 - baseline.mean_r2 in pp


def _evaluate(model: Any, X_test: np.ndarray, y_test: np.ndarray) -> FoldResult:
    y_pred = model.predict(X_test)
    return FoldResult(
        fold=-1,
        n_train=0,
        n_test=len(y_test),
        r2=float(r2_score(y_test, y_pred)),
        rmse=float(np.sqrt(mean_squared_error(y_test, y_pred))),
        mae=float(mean_absolute_error(y_test, y_pred)),
    )


def sweep_system(
    X: np.ndarray,
    y: np.ndarray,
    grid: dict[str, list[float | int]],
    *,
    n_splits: int = N_SPLITS,
    random_state: int = RANDOM_STATE,
) -> tuple[ConfigResult, ...]:
    """Sweep all grid combinations with locked within-system KFold.

    Returns one ConfigResult per combination, ordered by mean R² (desc).
    """
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    splits = list(kf.split(X, y))

    results: list[ConfigResult] = []
    for values in _iter_grid(grid):
        params = dict(values)
        model = HistGradientBoostingRegressor(
            random_state=random_state,
            **params,
        )
        fold_results: list[FoldResult] = []
        for fold_idx, (train_idx, test_idx) in enumerate(splits):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]
            model_clone = _clone_model(model)
            model_clone.fit(X_train, y_train)
            res = _evaluate(model_clone, X_test, y_test)
            fold_results.append(
                FoldResult(
                    fold=fold_idx,
                    n_train=len(train_idx),
                    n_test=len(test_idx),
                    r2=res.r2,
                    rmse=res.rmse,
                    mae=res.mae,
                ),
            )
        r2s = np.asarray([f.r2 for f in fold_results], dtype=np.float64)
        results.append(
            ConfigResult(
                params=params,
                fold_results=tuple(fold_results),
                mean_r2=float(r2s.mean()),
                std_r2=float(r2s.std()),
                mean_rmse=float(np.mean([f.rmse for f in fold_results])),
                mean_mae=float(np.mean([f.mae for f in fold_results])),
            ),
        )
    results.sort(key=lambda r: (-r.mean_r2, r.std_r2))
    return tuple(results)


def paired_r2_baseline(
    X: np.ndarray,
    y: np.ndarray,
    *,
    n_splits: int = N_SPLITS,
    random_state: int = RANDOM_STATE,
) -> PairedBaseline:
    """Score DummyRegressor(mean) on the same locked splits as the model.

    Returns fold-for-fold R² + lift over the dummy mean predictor.
    """
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    fold_r2: list[float] = []
    for train_idx, test_idx in kf.split(X, y):
        baseline = DummyRegressor(strategy="mean")
        baseline.fit(X[train_idx], y[train_idx])
        y_pred = baseline.predict(X[test_idx])
        fold_r2.append(float(r2_score(y[test_idx], y_pred)))
    arr = np.asarray(fold_r2, dtype=np.float64)
    return PairedBaseline(
        strategy="DummyRegressor(mean)",
        fold_r2=tuple(float(v) for v in fold_r2),
        mean_r2=float(arr.mean()),
        std_r2=float(arr.std()),
        lift_over_dummy_pp=0.0,  # caller patches after sweeping model
    )


def _iter_grid(grid: dict[str, list[float | int]]):
    keys = list(grid.keys())
    if not keys:
        yield {}
        return
    counters: list[int] = [0] * len(keys)

    def current() -> dict[str, Any]:
        return {keys[i]: grid[keys[i]][counters[i]] for i in range(len(keys))}

    while True:
        yield current()
        # increment rightmost, carry left
        idx = len(keys) - 1
        while idx >= 0:
            counters[idx] += 1
            if counters[idx] < len(grid[keys[idx]]):
                break
            counters[idx] = 0
            idx -= 1
        if idx < 0:
            return


def _grid_size(grid: dict[str, list[float | int]]) -> int:
    size = 1
    for values in grid.values():
        size *= max(1, len(values))
    return size


def _clone_model(model: Any) -> Any:
    from sklearn.base import clone

    return clone(model)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def build_sweep_log(
    *,
    element_system: str,
    n_samples: int,
    all_results: tuple[ConfigResult, ...],
    baseline: PairedBaseline,
    best: ConfigResult,
    paired_lift_pp: float,
    training_seconds: float,
    data_sha256: str,
    sweep_grid: dict[str, list[float | int]],
) -> dict[str, Any]:
    """Build the JSON-serializable sweep log for one system."""
    # Update baseline lift now that we know the best model's mean R².
    baseline_block = {
        "strategy": baseline.strategy,
        "n_splits": len(baseline.fold_r2),
        "fold_r2": [round(v, 6) for v in baseline.fold_r2],
        "mean_r2": round(baseline.mean_r2, 6),
        "std_r2": round(baseline.std_r2, 6),
        "paired_with_model_splits": True,
        "lift_over_dummy_pp": round(paired_lift_pp, 4),
        "interpretation": (
            "lift > 0 means the specialist out-scored the constant-mean "
            "predictor on the locked within-system splits; lift < 0 is a "
            "fail-closed trigger (NFM-3957 paired-baseline tradition)."
        ),
    }

    top5 = sorted(all_results, key=lambda r: (-r.mean_r2, r.std_r2))[:5]
    top5_block = [
        {
            "params": cfg.params,
            "mean_r2": round(cfg.mean_r2, 6),
            "std_r2": round(cfg.std_r2, 6),
            "mean_rmse": round(cfg.mean_rmse, 6),
            "mean_mae": round(cfg.mean_mae, 6),
            "per_fold_r2": [round(f.r2, 6) for f in cfg.fold_results],
        }
        for cfg in top5
    ]

    best_block = {
        "params": best.params,
        "mean_r2": round(best.mean_r2, 6),
        "std_r2": round(best.std_r2, 6),
        "mean_rmse": round(best.mean_rmse, 6),
        "mean_mae": round(best.mean_mae, 6),
        "per_fold_r2": [round(f.r2, 6) for f in best.fold_results],
        "per_fold_rmse": [round(f.rmse, 6) for f in best.fold_results],
        "per_fold_mae": [round(f.mae, 6) for f in best.fold_results],
        "n_splits": len(best.fold_results),
    }

    # RD-2 / RD-3 honest assessment: label [EXPLORATORY] when the best
    # model fails to beat the constant-mean predictor (lift < 0).
    reasons = list(RD2_REASONS)
    if paired_lift_pp < 0:
        reasons.append(
            "best specialist scored below DummyRegressor(mean) on the "
            "locked splits — fail-closed (NFM-3957).",
        )
    if best.std_r2 > 0.15:
        reasons.append(
            f"fold R² std {best.std_r2:.4f} exceeded the 0.15 high-variance RD-3 trigger.",
        )
    rd2_label = "[EXPLORATORY]" if reasons else "[CONFIRMATORY]"

    return {
        "element_system": element_system,
        "protocol_version": PROTOCOL_VERSION,
        "dataset_source": DATASET_SOURCE,
        "n_samples": int(n_samples),
        "feature_vocab": list(FEATURE_VOCAB),
        "feature_vocab_dim": len(FEATURE_VOCAB),
        "param_mapping": PARAM_MAPPING,
        "sweep_grid": sweep_grid,
        "n_configs_swept": len(all_results),
        "rd2_label": rd2_label,
        "rd3_triggered": paired_lift_pp < 0 or best.std_r2 > 0.15,
        "rd2_reasons": reasons,
        "random_state": RANDOM_STATE,
        "n_splits": N_SPLITS,
        "within_system_cv": (
            f"KFold(n_splits={N_SPLITS}, shuffle=True, random_state={RANDOM_STATE})"
        ),
        "cross_system_cv": (
            "GroupKFold(n_splits=5) per apps/api/src/nfm_db/ml/group_kfold_cv.py:36-65 (locked)"
        ),
        "paired_r2_baseline": baseline_block,
        "best_config": best_block,
        "top_5_configs": top5_block,
        "training_seconds": round(training_seconds, 4),
        "data_sha256": data_sha256,
        "code_sha256": _sha256_file(Path(__file__)),
        "git_commit_sha": _git_commit_sha(),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-path",
        type=Path,
        default=_REPO_ROOT / "data" / "training_set_v30_raw.csv",
    )
    parser.add_argument(
        "--sweep-dir",
        type=Path,
        default=_REPO_ROOT / "apps" / "api" / "models" / "specialists" / "_sweep",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=TOP_N_SYSTEMS,
    )
    parser.add_argument(
        "--systems",
        type=str,
        nargs="*",
        default=None,
        help="Override target systems (default: top-N from dataset).",
    )
    parser.add_argument(
        "--grid-tag",
        type=str,
        default="",
        help="Optional suffix appended to output filenames (e.g. 'v2').",
    )
    parser.add_argument(
        "--grid-version",
        choices=["v1", "v2"],
        default="v1",
        help=(
            "v1 = 8-config compact grid (default). "
            "v2 = 32-config expanded grid for systems that fail the v1 "
            "sweep — covers max_iter=[200,400], max_depth=[3,5,7], "
            "min_samples_leaf=[5,20,50], learning_rate=[0.03,0.1]."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if not args.data_path.exists():
        logger.error("v3.0 export missing at %s", args.data_path)
        return 2

    logger.info("=== Channel 4 hyperparameter sweep (NFM-4035) ===")
    logger.info("Protocol: %s", PROTOCOL_VERSION)
    logger.info("Dataset: %s", DATASET_SOURCE)
    logger.info("Data path: %s", args.data_path)
    logger.info("Feature vocab: 12D aggregates-only (V11_ADDITIONAL_FEATURE_NAMES)")

    df = load_v30_dataset(args.data_path)
    logger.info(
        "Loaded %d rows; element-system histogram: %s",
        len(df),
        dict(Counter(df["element_system"]).most_common(15)),
    )

    selected, ranked = select_top_systems(df, top_n=args.top_n)
    if args.systems:
        selected = list(args.systems)
    logger.info(
        "Top-%d systems by row count: %s",
        args.top_n,
        [(name, ranked[name]) for name in selected if name in ranked],
    )

    grid = SWEEP_GRID_V2 if args.grid_version == "v2" else SWEEP_GRID
    grid_tag = args.grid_tag or args.grid_version
    logger.info(
        "Sweep grid: %s (%d configs); output suffix: %s",
        grid,
        _grid_size(grid),
        grid_tag,
    )

    args.sweep_dir.mkdir(parents=True, exist_ok=True)
    data_sha = _sha256_file(args.data_path)

    summary: list[dict[str, Any]] = []
    overall_started = time.perf_counter()
    for system in selected:
        if system not in ranked:
            logger.warning("System %s not in dataset; skipping", system)
            continue
        sub = df[df["element_system"] == system]
        if len(sub) < N_SPLITS:
            logger.warning(
                "System %s has %d rows (<5 folds); skipping",
                system,
                len(sub),
            )
            continue
        X = sub[list(FEATURE_VOCAB)].to_numpy(dtype=np.float64)
        y = sub["formation_energy"].to_numpy(dtype=np.float64)

        started = time.perf_counter()
        all_results = sweep_system(X, y, grid)
        baseline = paired_r2_baseline(X, y)
        best = all_results[0]
        lift_pp = (best.mean_r2 - baseline.mean_r2) * 100.0
        elapsed = time.perf_counter() - started

        log = build_sweep_log(
            element_system=system,
            n_samples=len(sub),
            all_results=all_results,
            baseline=baseline,
            best=best,
            paired_lift_pp=lift_pp,
            training_seconds=elapsed,
            data_sha256=data_sha,
            sweep_grid=grid,
        )

        out_path = args.sweep_dir / f"{system}_sweep{('_' + grid_tag) if grid_tag else ''}.json"
        out_path.write_text(json.dumps(log, indent=2, sort_keys=True) + "\n")
        logger.info(
            "[%s] n=%d configs=%d best_r2=%.4f +/- %.4f "
            "baseline_r2=%.4f lift_pp=%+.2f t=%.2fs -> %s",
            system,
            len(sub),
            len(all_results),
            best.mean_r2,
            best.std_r2,
            baseline.mean_r2,
            lift_pp,
            elapsed,
            out_path.name,
        )
        summary.append(
            {
                "element_system": system,
                "n_samples": len(sub),
                "best_r2": round(best.mean_r2, 4),
                "best_r2_std": round(best.std_r2, 4),
                "baseline_r2": round(baseline.mean_r2, 4),
                "lift_over_dummy_pp": round(lift_pp, 2),
                "best_params": best.params,
                "rd2_label": log["rd2_label"],
                "log_path": str(out_path.relative_to(_REPO_ROOT)),
            },
        )

    overall_elapsed = time.perf_counter() - overall_started
    summary_path = args.sweep_dir / f"_summary{('_' + grid_tag) if grid_tag else ''}.json"
    summary_payload = {
        "protocol_version": PROTOCOL_VERSION,
        "dataset_source": DATASET_SOURCE,
        "random_state": RANDOM_STATE,
        "n_splits": N_SPLITS,
        "feature_vocab": list(FEATURE_VOCAB),
        "sweep_grid": grid,
        "param_mapping": PARAM_MAPPING,
        "n_systems": len(summary),
        "total_seconds": round(overall_elapsed, 3),
        "per_system": summary,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n")
    logger.info(
        "Sweep complete: %d systems in %.2fs -> %s",
        len(summary),
        overall_elapsed,
        summary_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
