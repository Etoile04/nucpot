#!/usr/bin/env python3
"""[CONFIRMATORY] EnergyPredictor v3.3 GroupKFold(5) promotion run (NFM-5305).

Executes the locked NFM-5305 PREREG verbatim: two-arm GroupKFold(5) by
element_system on the frozen NFM-1540 PathB corpus (2,909 compositions, 68
systems), XGB_PARAMS imported verbatim from train_energy_v30, seed 42, no
hyperparameter search, single deterministic run per arm.

- Arm A (control): the v3.0 20D vocabulary via build_dataset (the v3.0
  pipeline itself, not a re-implementation).
- Arm B (treatment): 24D = Arm A + the 4 locked pairwise additions
  (energy_features_v33.ENERGY_V33_FEATURE_NAMES).

Endpoints (roles fixed pre-results): PRIMARY = pooled R^2 over concatenated
out-of-fold predictions (KR-ML-3 gate > 0.85); SECONDARY = mean-of-fold R^2
plus-minus std (feeds the NFM-3959 Mandate 2 runtime confidence clamp);
DESCRIPTIVE = delta pooled R^2 (B - A).

The 4-branch decision rule (prereg section 6) is evaluated in code on the
first read of pooled R^2. RD-3 trip-wire: any pooled or per-fold R^2 > 0.95
stops the run BEFORE any output is written or any result posted.

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v33_groupedcv
    cd apps/api && python -m nfm_db.ml.train_energy_v33_groupedcv \
        --data-dir /path/to/data --output-dir /path/to/models
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold

from nfm_db.ml.energy_features_v11 import ENERGY_V11_FEATURE_NAMES
from nfm_db.ml.energy_features_v33 import (
    ENERGY_V33_FEATURE_NAMES,
    compute_energy_features_v33,
)
from nfm_db.ml.group_kfold_cv import build_group_labels
from nfm_db.ml.train_energy_v30 import (
    DATA_DIR,
    DEFAULT_MODELS_DIR,
    RANDOM_STATE,
    XGB_PARAMS,
    build_dataset,
    load_v30_data,
)
from nfm_db.ml.train_energy_v30_grouped_cv import derive_kept_compositions

logger = logging.getLogger(__name__)

RUN_TAG = "v3.3-groupedcv-NFM-5305"
MODEL_VERSION = "v3.3"
MODEL_FILENAME = "energy_predictor_v33.joblib"
METRICS_FILENAME = "energy_predictor_v3.3_groupedcv_metrics.json"

N_SPLITS = 5
NEAR_VACUOUS_STD = 0.010
BUCKET_MIN_N = 20
EXPECTED_N_SAMPLES = 2909
EXPECTED_N_GROUPS = 68

KR_ML3_GATE = 0.85
ADOPTION_DELTA_MIN = 0.010
DISPATCH_BAND_MIN = 0.60
RD3_TRIPWIRE = 0.95

PREREG_SUBMITTED_LINE = (
    "[PREREG-SUBMITTED] NFM-5305 comment 40d2e0b5-1bd8-4eda-ae12-1736c01a9d1a "
    "(canonical record, locked verbatim)"
)
PREREG_APPROVED_LINE = (
    "[PREREG-APPROVED] NFM-5305 comment d6e1b268-8b4d-4856-8178-24dca1f28e8b "
    "2026-10-05T01:33:00.374Z (NDE)"
)

# The prereg body exactly as posted on NFM-5305 (byte-identical to comment
# 40d2e0b5-1bd8-4eda-ae12-1736c01a9d1a). Its unicode is intentional verbatim
# content and must never be normalized or "fixed".
PREREG_VERBATIM = """[PREREG-SUBMITTED] v3.3 pairwise-stratum extension — EnergyPredictor runtime-promotion retry for KR-ML-3 (RD-1)

Direction source: NFM-5235 §Planned item 3 (NDE gate on the v3.2 dispatch-candidate follow-up PREREG). The v3.2 LOESO H1 PASS (pooled R² 0.794606, NFM-4853 / PR #1364) licensed the dispatch-candidate band only; H2 Δ+0.032126 (pairwise stratum transfers) routes pairwise-stratum feature work as the natural KR-ML-3 retry thread. This comment is the canonical pre-registration record; on approval it will be locked verbatim into the run sidecar, as NFM-4860 was for v3.2.

## 1. Objective

Test whether a 4-feature pairwise-stratum extension of the locked 20D energy vocabulary lifts EnergyPredictor pooled grouped-CV R² above the KR-ML-3 bar (> 0.85) under a leakage-safe protocol — and, only if the decision rule below licenses it, promote the winning artifact to runtime, replacing the v3.0 [EXPLORATORY] core (ENERGY_PREDICTOR_VERSION in ml/model_version.py, dispatch in ml/prediction_service.py) while inheriting the NFM-3959 honesty mandates.

## 2. Data (locked)

NFM-1540 PathB: 2,909 unique PBE compositions, 68 element systems (grouping key = sorted non-U solute set; group sizes 2–237, median 17.5). Identical to the v3.0/v3.1/v3.2 corpus. No new data, no augmentation, no filtering, no sample re-admission.

## 3. Feature sets (locked)

Arm A (control) — the v3.0/v3.2 20D vocabulary, verbatim (ENERGY_V11_FEATURE_NAMES): mo_equivalent, allen_chi_diff, config_entropy, bv_ratio, u_density, mixing_enthalpy, lattice_distortion, vec, avg_allen_chi, avg_atomic_volume, avg_d_electron, avg_work_function, avg_bulk_modulus, hr_valence_diff, dg_en_radius_distance, max_pair_en_diff, en_variance, volume_variance, d_electron_variance, bulk_modulus_variance. Purpose: protocol-matched baseline — v3.0's grouped-CV sidecar carries mean-of-fold only (0.3111 ± 0.4777); no pooled statistic exists for this protocol, and the promotion gate is defined on pooled.

Arm B (treatment) — Arm A plus exactly 4 locked pairwise additions (24D), each a pure function of composition using only in-repo element tables (Allen χ, bulk modulus, work function; feature_engineering + energy_features_v11 — zero NDE-table dependency):
1. min_pair_en_diff — min pairwise |Δ Allen χ|: chemical-similarity floor, complement of the existing max_pair_en_diff (which saturates at the U–solute extreme and carries little within-system signal);
2. avg_pair_en_diff — composition-weighted mean pairwise |Δ Allen χ| incl. solute–solute pairs: mean pair dissimilarity, complementing config entropy;
3. max_pair_bulk_modulus_diff — max pairwise |Δ bulk modulus|: elastic-mismatch extreme; the elastic table enters the pairwise stratum for the first time (today only as weighted variance);
4. max_pair_work_function_diff — max pairwise |Δ work function|: electronic chemical-potential mismatch extreme (φ today enters only as an average).

No post-hoc feature selection: all 4 remain in Arm B regardless of measured importance. energy_features_v11.py stays frozen (NFM-5060 sentinel-safe); calculators live in a new ml/energy_features_v33.py exposing ENERGY_V33_FEATURE_NAMES (24D).

## 4. Model + evaluation protocol (locked)

- Estimator: XGBRegressor with XGB_PARAMS IMPORTED verbatim from train_energy_v30.py:149-161 (n_estimators 800, max_depth 5, lr 0.02, subsample 0.7, colsample_bytree 0.7, reg_alpha 1.5, reg_lambda 10.0, min_child_weight 10, gamma 0.1), random_state 42, fresh model per fold, NO hyperparameter search.
- CV: GroupKFold(n_splits=5) grouped by element_system, seed 42 — identical to the v3.0 grouped-CV protocol (NFM-3953, [PREREG-APPROVED] 2026-08-31T22:13Z), i.e. directly comparable to the runtime honesty-clamp lineage. Single deterministic run per arm; no seed sweeps.
- Guards inherited from v3.2 LOESO: near-vacuous flag at held-out-system target std < 0.010 eV/atom; per-system bucket census restricted to n_test ≥ 20; constant-train-mean floor reported; Spearman ρ(per-system R², target std) reported as diagnostic.
- Estimated cost: 10 fits total (2 arms × 5 folds) + one full-data fit per promoted artifact — well inside v3.2's ~100 s / 136-fit envelope.

## 5. Endpoints (roles fixed before any result)

- PRIMARY (KR-ML-3 gate): pooled R² — coefficient of determination over the concatenated out-of-fold predictions of all 2,909 samples.
- SECONDARY (fixed role — disclosure + runtime clamp input): mean-of-fold R² ± std. This is the number that feeds grouped_cv_summary.r2_mean; per NFM-3959 Mandate 2 the promoted artifact's confidence remains clamped to min(r2_mean, r2_random, 1.0). Promotion does NOT bypass or relabel the honesty clamp, and the endpoint roles may not be swapped after results are seen.
- DESCRIPTIVE: Δ pooled R² (B − A) — pairwise-stratum extension transfer (v3.2 H2 analogue; routes any v3.4 feature proposal).

## 6. Acceptance criteria + decision rule (pre-committed, first read of pooled R²)

1. pooled(A) > 0.85 → CONTROL-CLEARS: the incumbent 20D vocabulary already meets KR-ML-3 under the promotion protocol; promote the 20D artifact (v3.2 vocabulary re-validated under GroupKFold); adopt the v3.3 additions only if (B − A) ≥ +0.010.
2. else pooled(B) > 0.85 AND (B − A) ≥ +0.010 → H1 PROMOTE: promote the Arm-B (24D) artifact; KR-ML-3 → Green on the primary endpoint.
3. else pooled(B) > 0.60 → DISPATCH-CANDIDATE: no runtime promotion; v3.3 recorded alongside v3.2 as dispatch candidate; KR-ML-3 stays Red honestly; NDE picks the next direction (data acquisition vs v3.4 features vs closing the thread).
4. else → FAIL: the pairwise-stratum extension is falsified under the promotion protocol; no promotion; no further feature surgery on NFM-1540 PathB without new data.

In every branch the promoted/recorded artifact is the winning arm re-trained on the full locked dataset, carrying its grouped-CV summary. Runtime swap stays inside ml/ (version constant + artifact wiring); production wrapping routes through the LE handoff flow.

RD-3 trip-wire: any reported statistic > 0.95 (pooled or per-fold R²) → STOP; leakage investigation completes BEFORE any result is posted; implausibly good results are never silently accepted. This run is confirmatory ([CONFIRMATORY] labeling); any deviation from this protocol voids the pre-registration and requires a fresh one (NFM-4031 / NFM-4853 lesson — never re-lock a falsified or executed protocol verbatim).

## 7. Work products (on approval)

ml/train_energy_v33_groupedcv.py · ml/energy_features_v33.py · models/energy_predictor_v33.joblib · models/energy_predictor_v3.3_groupedcv_metrics.json (schema mirroring the v3.2 sidecar: protocol_locked, arms, per_fold, per_system, guards, decision) · tests/test_ml_energy_v33_groupedcv.py. On a PROMOTE/CONTROL-CLEARS outcome only: runtime swap inside ml/ + LE handoff package re-validated (pre-staged inputs from NFM-5246 §P3: predict_binding_energy_from_composition(composition: dict) -> {mean: float, std: float} over the existing predict_energy_from_composition surface (prediction_service.py:1196); Pydantic I/O schema; pytest-green; latency ≤ 2× the 3.2 ms / 75-composition baseline).

## 8. Baselines of record (read back from sidecars this run)

- v3.2 LOESO pooled, 20D (Arm A analogue): R² 0.794606, MAE 0.14434, RMSE 0.281202 — dispatch-candidate band.
- v3.2 LOESO pooled, 12D: R² 0.76248 → H2 Δ +0.032126; constant-mean floor −0.098771 (both arms).
- v3.2 census: 17 bucket-eligible systems, 0 at ≥ 0.60, 2 in 0.30–0.60 (best Ru-Ti 0.460487), 41/68 near-vacuous; ρ(R², target std) 0.877 (pooled skill is between-system variance capture).
- v3.0 GroupKFold(5) — THIS protocol, mean-of-fold: R² 0.3111 ± 0.4777, MAE 0.195945, RMSE 0.303767.
- v3.1 GroupKFold(5) (falsified Option B): mean-of-fold R² 0.2598 ± 0.5075.

Awaiting NDE [PREREG-APPROVED] before any training. A documented decline is a resolved loop — it closes the KR-ML-3 retry thread with the ruling recorded on this issue.
"""


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldResult:
    """One GroupKFold fold's held-out metrics."""

    fold_index: int
    n_train: int
    n_test: int
    n_test_groups: int
    r2: float
    rmse: float
    mae: float


@dataclass(frozen=True)
class SystemResult:
    """One element system's out-of-fold metrics."""

    system: str
    n_test: int
    target_std: float
    near_vacuous: bool
    r2: float
    mae: float
    rmse: float
    baseline_r2: float
    baseline_mae: float
    bucket_eligible: bool
    r2_band: str


@dataclass(frozen=True)
class ArmResult:
    """One arm's full protocol output."""

    arm: str
    label: str
    feature_names: list[str]
    folds: list[FoldResult]
    n_samples: int
    n_systems: int
    pooled_r2: float
    pooled_mae: float
    pooled_rmse: float
    pooled_baseline_r2: float
    pooled_baseline_mae: float
    r2_mean: float
    r2_std: float
    per_system: list[SystemResult]
    bucket_counts: dict[str, int]
    n_near_vacuous: int
    near_vacuous_systems: list[str]
    spearman_r2_vs_target_std_all: float | None
    spearman_r2_vs_target_std_eligible: float | None


@dataclass(frozen=True)
class Decision:
    """Prereg section 6 branch, evaluated on the first read of pooled R^2."""

    branch: str
    winner_arm: str
    winner_rule: str
    runtime_swap: bool
    routing: str


# ---------------------------------------------------------------------------
# Metric helpers (mirroring train_energy_v32_loeso conventions)
# ---------------------------------------------------------------------------


def _safe_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R^2 guarded against zero-variance targets (returns 0.0)."""
    if len(y_true) < 2 or float(np.std(y_true)) == 0.0:
        return 0.0
    return float(r2_score(y_true, y_pred))


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(mean_absolute_error(y_true, y_pred))


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))


def r2_band(r2: float) -> str:
    """Three-band rule: >= 0.60 / 0.30-0.60 / < 0.30."""
    if r2 >= 0.60:
        return "ge_0.60"
    if r2 >= 0.30:
        return "0.30-0.60"
    return "lt_0.30"


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rho guarded against degenerate inputs (returns None)."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rho = float(spearmanr(xs, ys).statistic)
    return None if math.isnan(rho) else round(rho, 4)


# ---------------------------------------------------------------------------
# Protocol execution
# ---------------------------------------------------------------------------


def build_arm_b_matrix(kept: list[dict[str, float]]) -> np.ndarray:
    """Compute the 24D Arm B matrix over the kept compositions."""
    rows = [compute_energy_features_v33(comp) for comp in kept]
    return pd.DataFrame(rows, columns=ENERGY_V33_FEATURE_NAMES).to_numpy(
        dtype=float,
    )


def run_groupedcv_arm(
    X: np.ndarray,
    y: np.ndarray,
    groups: list[str],
) -> tuple[list[FoldResult], np.ndarray, np.ndarray]:
    """Run the locked GroupKFold(5) loop with split-integrity STOP guards.

    Returns (folds, oof_pred, oof_baseline) where the baselines are the
    per-fold constant train-mean predictions (the prereg's constant-train-mean
    floor).
    """
    gkf = GroupKFold(n_splits=N_SPLITS)
    folds: list[FoldResult] = []
    oof_pred = np.zeros(len(y), dtype=float)
    oof_baseline = np.zeros(len(y), dtype=float)
    tested_mask = np.zeros(len(y), dtype=bool)

    for fold_idx, (train_idx, test_idx) in enumerate(
        gkf.split(X, y, groups=groups),
    ):
        train_groups = {groups[i] for i in train_idx}
        test_groups = {groups[i] for i in test_idx}
        overlap = train_groups & test_groups
        if overlap:
            raise RuntimeError(
                f"Split integrity violated (fold {fold_idx}): element systems "
                f"in both train and test: {sorted(overlap)}"
            )
        if tested_mask[test_idx].any():
            raise RuntimeError(
                f"Split integrity violated (fold {fold_idx}): a sample was "
                "assigned to more than one test fold"
            )
        tested_mask[test_idx] = True

        X_tr, X_val = X[train_idx], X[test_idx]
        y_tr, y_val = y[train_idx], y[test_idx]
        model = xgb.XGBRegressor(**{**XGB_PARAMS, "random_state": RANDOM_STATE})
        model.fit(X_tr, y_tr, verbose=False)
        fold_pred = model.predict(X_val)
        oof_pred[test_idx] = fold_pred
        fold_train_mean = float(np.mean(y_tr))
        oof_baseline[test_idx] = fold_train_mean

        fold = FoldResult(
            fold_index=fold_idx,
            n_train=len(train_idx),
            n_test=len(test_idx),
            n_test_groups=len(test_groups),
            r2=round(_safe_r2(y_val, fold_pred), 6),
            rmse=round(_rmse(y_val, fold_pred), 6),
            mae=round(_mae(y_val, fold_pred), 6),
        )
        folds.append(fold)
        logger.info(
            "Fold %d/%d: R2=%.6f RMSE=%.6f MAE=%.6f (n_train=%d n_test=%d "
            "test_systems=%d)",
            fold_idx + 1,
            N_SPLITS,
            fold.r2,
            fold.rmse,
            fold.mae,
            fold.n_train,
            fold.n_test,
            fold.n_test_groups,
        )

    if not bool(tested_mask.all()):
        missing = int((~tested_mask).sum())
        raise RuntimeError(
            f"Split integrity violated: {missing} samples never assigned to "
            "a test fold"
        )
    return folds, oof_pred, oof_baseline


def _system_metrics(
    system: str,
    y_s: np.ndarray,
    pred_s: np.ndarray,
    baseline_s: np.ndarray,
) -> SystemResult:
    """Per-system out-of-fold record with the inherited v3.2 guards."""
    target_std = float(np.std(y_s))
    near_vacuous = target_std < NEAR_VACUOUS_STD
    r2 = _safe_r2(y_s, pred_s)
    return SystemResult(
        system=system,
        n_test=len(y_s),
        target_std=round(target_std, 6),
        near_vacuous=near_vacuous,
        r2=round(r2, 6),
        mae=round(_mae(y_s, pred_s), 6),
        rmse=round(_rmse(y_s, pred_s), 6),
        baseline_r2=round(_safe_r2(y_s, baseline_s), 6),
        baseline_mae=round(_mae(y_s, baseline_s), 6),
        bucket_eligible=(not near_vacuous) and len(y_s) >= BUCKET_MIN_N,
        r2_band=r2_band(r2),
    )


def summarize_arm(
    arm: str,
    label: str,
    feature_names: list[str],
    folds: list[FoldResult],
    oof_pred: np.ndarray,
    oof_baseline: np.ndarray,
    y: np.ndarray,
    groups: list[str],
) -> ArmResult:
    """Assemble one arm's ArmResult from fold outputs."""
    per_system = [
        _system_metrics(
            system,
            y[[i for i, g in enumerate(groups) if g == system]],
            oof_pred[[i for i, g in enumerate(groups) if g == system]],
            oof_baseline[[i for i, g in enumerate(groups) if g == system]],
        )
        for system in sorted(set(groups))
    ]
    eligible = [s for s in per_system if s.bucket_eligible]
    bucket_counts = {
        "eligible_systems": len(eligible),
        "ge_0.60": sum(1 for s in eligible if s.r2_band == "ge_0.60"),
        "0.30-0.60": sum(1 for s in eligible if s.r2_band == "0.30-0.60"),
        "lt_0.30": sum(1 for s in eligible if s.r2_band == "lt_0.30"),
    }
    fold_r2s = np.array([f.r2 for f in folds], dtype=float)
    near_vacuous_systems = [s.system for s in per_system if s.near_vacuous]
    all_stds = [s.target_std for s in per_system]
    eligible_stds = [s.target_std for s in eligible]
    return ArmResult(
        arm=arm,
        label=label,
        feature_names=feature_names,
        folds=folds,
        n_samples=len(y),
        n_systems=len(per_system),
        pooled_r2=round(_safe_r2(y, oof_pred), 6),
        pooled_mae=round(_mae(y, oof_pred), 6),
        pooled_rmse=round(_rmse(y, oof_pred), 6),
        pooled_baseline_r2=round(_safe_r2(y, oof_baseline), 6),
        pooled_baseline_mae=round(_mae(y, oof_baseline), 6),
        r2_mean=round(float(fold_r2s.mean()), 4),
        r2_std=round(float(fold_r2s.std()), 4),
        per_system=per_system,
        bucket_counts=bucket_counts,
        n_near_vacuous=len(near_vacuous_systems),
        near_vacuous_systems=near_vacuous_systems,
        spearman_r2_vs_target_std_all=_spearman(
            [s.r2 for s in per_system],
            all_stds,
        ),
        spearman_r2_vs_target_std_eligible=_spearman(
            [s.r2 for s in eligible],
            eligible_stds,
        ),
    )


def run_arm(
    arm: str,
    label: str,
    feature_names: list[str],
    X: np.ndarray,
    y: np.ndarray,
    groups: list[str],
) -> ArmResult:
    """Run one arm end-to-end under the locked protocol."""
    logger.info("Arm %s (%dD): starting GroupKFold(%d) by element_system", arm, X.shape[1], N_SPLITS)
    folds, oof_pred, oof_baseline = run_groupedcv_arm(X, y, groups)
    result = summarize_arm(
        arm,
        label,
        feature_names,
        folds,
        oof_pred,
        oof_baseline,
        y,
        groups,
    )
    logger.info(
        "Arm %s: pooled R2=%.6f (baseline %.6f), mean-of-fold R2=%.4f +/- %.4f, "
        "MAE=%.6f, RMSE=%.6f",
        arm,
        result.pooled_r2,
        result.pooled_baseline_r2,
        result.r2_mean,
        result.r2_std,
        result.pooled_mae,
        result.pooled_rmse,
    )
    return result


# ---------------------------------------------------------------------------
# Decision rule (prereg section 6) + RD-3 trip-wire
# ---------------------------------------------------------------------------


def evaluate_decision_rule(pooled_a: float, pooled_b: float) -> Decision:
    """Evaluate the pre-committed 4-branch rule on pooled R^2.

    Branches 3-4 record the higher-pooled arm as the winner (tie goes to A,
    the smaller incumbent vocabulary); no runtime swap is licensed there.
    """
    delta = pooled_b - pooled_a
    if pooled_a > KR_ML3_GATE:
        winner = "B" if delta >= ADOPTION_DELTA_MIN else "A"
        return Decision(
            branch="CONTROL-CLEARS",
            winner_arm=winner,
            winner_rule=(
                "pooled(A) > 0.85; adopt the v3.3 additions iff (B - A) >= "
                "+0.010"
            ),
            runtime_swap=True,
            routing=(
                "20D incumbent re-validated under the promotion protocol; "
                "runtime swap inside ml/ + LE handoff for production wrapping"
            ),
        )
    if pooled_b > KR_ML3_GATE and delta >= ADOPTION_DELTA_MIN:
        return Decision(
            branch="H1-PROMOTE",
            winner_arm="B",
            winner_rule="pooled(B) > 0.85 AND (B - A) >= +0.010",
            runtime_swap=True,
            routing=(
                "24D artifact promoted; KR-ML-3 Green on the primary "
                "endpoint; runtime swap inside ml/ + LE handoff for "
                "production wrapping"
            ),
        )
    winner = "B" if pooled_b > pooled_a else "A"
    if pooled_b > DISPATCH_BAND_MIN:
        return Decision(
            branch="DISPATCH-CANDIDATE",
            winner_arm=winner,
            winner_rule="higher pooled R^2 wins (tie -> A); no promotion",
            runtime_swap=False,
            routing=(
                "no runtime promotion; v3.3 recorded alongside v3.2 as "
                "dispatch candidate; KR-ML-3 stays Red honestly; NDE picks "
                "the next direction (data acquisition vs v3.4 features vs "
                "closing the thread)"
            ),
        )
    return Decision(
        branch="FAIL",
        winner_arm=winner,
        winner_rule="higher pooled R^2 wins (tie -> A); no promotion",
        runtime_swap=False,
        routing=(
            "pairwise-stratum extension falsified under the promotion "
            "protocol; no promotion; no further feature surgery on "
            "NFM-1540 PathB without new data"
        ),
    )


def rd3_violations(arm_a: ArmResult, arm_b: ArmResult) -> list[str]:
    """RD-3 trip-wire: any pooled or per-fold R^2 above 0.95."""
    violations: list[str] = []
    for name, arm in (("A", arm_a), ("B", arm_b)):
        if arm.pooled_r2 > RD3_TRIPWIRE:
            violations.append(f"arm {name} pooled R2 {arm.pooled_r2}")
        for fold in arm.folds:
            if fold.r2 > RD3_TRIPWIRE:
                violations.append(
                    f"arm {name} fold {fold.fold_index} R2 {fold.r2}"
                )
    return violations


# ---------------------------------------------------------------------------
# Sidecar payload
# ---------------------------------------------------------------------------


def _arm_to_dict(arm: ArmResult) -> dict[str, object]:
    return {
        "arm": arm.arm,
        "label": arm.label,
        "feature_names": arm.feature_names,
        "n_samples": arm.n_samples,
        "n_systems": arm.n_systems,
        "pooled_r2": arm.pooled_r2,
        "pooled_mae": arm.pooled_mae,
        "pooled_rmse": arm.pooled_rmse,
        "pooled_baseline_r2": arm.pooled_baseline_r2,
        "pooled_baseline_mae": arm.pooled_baseline_mae,
        "r2_mean": arm.r2_mean,
        "r2_std": arm.r2_std,
        "per_fold": [asdict(f) for f in arm.folds],
        "per_system": [asdict(s) for s in arm.per_system],
        "bucket_counts": dict(arm.bucket_counts),
        "n_near_vacuous": arm.n_near_vacuous,
        "near_vacuous_systems": list(arm.near_vacuous_systems),
        "spearman_r2_vs_target_std_all": arm.spearman_r2_vs_target_std_all,
        "spearman_r2_vs_target_std_eligible": (
            arm.spearman_r2_vs_target_std_eligible
        ),
    }


def build_payload(arm_a: ArmResult, arm_b: ArmResult, decision: Decision) -> dict:
    """Build the v3.3 sidecar payload (schema mirroring the v3.2 sidecar)."""
    group_sizes: dict[str, int] = {}
    for g in arm_a.per_system:
        group_sizes[g.system] = g.n_test
    sizes = list(group_sizes.values())
    return {
        "run_tag": RUN_TAG,
        "preregistration": f"{PREREG_SUBMITTED_LINE}; {PREREG_APPROVED_LINE}",
        "prereg_body_verbatim": PREREG_VERBATIM,
        "confirmatory": True,
        "n_samples": arm_a.n_samples,
        "n_groups": arm_a.n_systems,
        "random_state": RANDOM_STATE,
        "protocol_locked": {
            "splitter": "GroupKFold(n_splits=5)",
            "grouping_key": "element_system (sorted non-U solute set)",
            "fresh_model_per_fold": True,
            "hyperparameter_search": False,
            "single_deterministic_run_per_arm": True,
            "xgb_params_locked_to": (
                "XGB_PARAMS in train_energy_v30.py:149-161 (imported verbatim)"
            ),
            "near_vacuous_guard": "held-out-system target std < 0.010 eV/atom",
            "bucket_min_n": BUCKET_MIN_N,
            "dataset": "NFM-1540 PathB (2,909 unique PBE compositions)",
            "preregistration": (
                "NFM-5305 comment 40d2e0b5 (canonical locked record); "
                "[PREREG-APPROVED] comment d6e1b268 2026-10-05T01:33:00.374Z "
                "(NDE)"
            ),
        },
        "endpoints": {
            "primary": (
                "pooled R^2 over concatenated out-of-fold predictions (all "
                "2,909 samples) - KR-ML-3 gate"
            ),
            "secondary": (
                "mean-of-fold R^2 +/- std - feeds grouped_cv_summary.r2_mean; "
                "NFM-3959 Mandate 2 clamp min(r2_mean, r2_random, 1.0)"
            ),
            "descriptive": "delta pooled R^2 (B - A)",
        },
        "group_size_stats": {
            "n_groups": len(sizes),
            "min": min(sizes),
            "max": max(sizes),
            "median": float(np.median(sizes)),
        },
        "arms": {
            "A": _arm_to_dict(arm_a),
            "B": _arm_to_dict(arm_b),
        },
        "delta_pooled_r2_ab": round(arm_b.pooled_r2 - arm_a.pooled_r2, 6),
        "decision": asdict(decision),
    }


def validate_sidecar_payload(payload: dict) -> None:
    """STOP-guard the sidecar schema before it is written to disk."""
    required = {
        "run_tag",
        "preregistration",
        "prereg_body_verbatim",
        "confirmatory",
        "n_samples",
        "n_groups",
        "protocol_locked",
        "endpoints",
        "arms",
        "delta_pooled_r2_ab",
        "decision",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"sidecar missing keys: {sorted(missing)}")
    if payload["n_samples"] != EXPECTED_N_SAMPLES:
        raise ValueError(f"n_samples {payload['n_samples']} != {EXPECTED_N_SAMPLES}")
    if payload["n_groups"] != EXPECTED_N_GROUPS:
        raise ValueError(f"n_groups {payload['n_groups']} != {EXPECTED_N_GROUPS}")
    if not str(payload["prereg_body_verbatim"]).startswith("[PREREG-SUBMITTED]"):
        raise ValueError("prereg_body_verbatim does not start with [PREREG-SUBMITTED]")
    arms = payload["arms"]
    if arms["A"]["feature_names"] != ENERGY_V11_FEATURE_NAMES:
        raise ValueError("arm A vocabulary is not the locked 20D list")
    if arms["B"]["feature_names"] != ENERGY_V33_FEATURE_NAMES:
        raise ValueError("arm B vocabulary is not the locked 24D list")
    for name, expected_features in (("A", 20), ("B", 24)):
        arm = arms[name]
        if len(arm["per_system"]) != EXPECTED_N_GROUPS:  # type: ignore[index]
            raise ValueError(f"arm {name}: per_system census != 68 systems")
        if sum(s["n_test"] for s in arm["per_system"]) != EXPECTED_N_SAMPLES:  # type: ignore[index]
            raise ValueError(f"arm {name}: per_system n_test sum != 2,909")
        if len(arm["per_fold"]) != N_SPLITS:  # type: ignore[index]
            raise ValueError(f"arm {name}: expected {N_SPLITS} folds")
        if sum(f["n_test"] for f in arm["per_fold"]) != EXPECTED_N_SAMPLES:  # type: ignore[index]
            raise ValueError(f"arm {name}: fold n_test sum != 2,909")
        buckets = arm["bucket_counts"]  # type: ignore[index]
        eligible = buckets["eligible_systems"]
        if eligible != buckets["ge_0.60"] + buckets["0.30-0.60"] + buckets["lt_0.30"]:  # type: ignore[index]
            raise ValueError(f"arm {name}: bucket census does not sum")
        if len(arm["feature_names"]) != expected_features:  # type: ignore[index]
            raise ValueError(f"arm {name}: expected {expected_features} features")
    branch = payload["decision"]["branch"]
    if branch not in {"CONTROL-CLEARS", "H1-PROMOTE", "DISPATCH-CANDIDATE", "FAIL"}:
        raise ValueError(f"unknown decision branch: {branch}")


# ---------------------------------------------------------------------------
# Full-data artifact
# ---------------------------------------------------------------------------


def fit_full_data(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    winner: ArmResult,
    decision: Decision,
) -> dict[str, object]:
    """Fit the winning arm on the full locked dataset (every branch)."""
    model = xgb.XGBRegressor(**{**XGB_PARAMS, "random_state": RANDOM_STATE})
    model.fit(X, y, verbose=False)
    r2_train_full = round(_safe_r2(y, model.predict(X)), 6)
    return {
        "model": model,
        "version": MODEL_VERSION,
        "metrics": {
            "run_tag": RUN_TAG,
            "decision_branch": decision.branch,
            "runtime_swap_licensed": decision.runtime_swap,
            "n_samples": len(y),
            "n_features": X.shape[1],
            "random_state": RANDOM_STATE,
            "r2_train_full": r2_train_full,
            "grouped_cv_summary": {
                "protocol": (
                    "GroupKFold(5) by element_system, seed 42, NFM-5305 "
                    "[PREREG-APPROVED] d6e1b268"
                ),
                "r2_mean": winner.r2_mean,
                "r2_std": winner.r2_std,
                "pooled_r2": winner.pooled_r2,
                "pooled_mae": winner.pooled_mae,
                "pooled_rmse": winner.pooled_rmse,
                "pooled_baseline_r2": winner.pooled_baseline_r2,
            },
        },
        "feature_names": feature_names,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "[CONFIRMATORY] NFM-5305 v3.3 GroupKFold(5) promotion run "
            "(locked prereg; RD-3 trip-wire enforced)"
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_MODELS_DIR)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    raw = load_v30_data(args.data_dir)
    kept = derive_kept_compositions(raw)
    X_a, y = build_dataset(raw)
    if len(kept) != len(y):
        raise RuntimeError(
            f"Row alignment failed: derive_kept_compositions kept {len(kept)} "
            f"rows but build_dataset kept {len(y)}"
        )
    if len(y) != EXPECTED_N_SAMPLES:
        raise RuntimeError(
            f"Expected {EXPECTED_N_SAMPLES} samples, got {len(y)}"
        )
    groups = build_group_labels(kept)
    n_groups = len(set(groups))
    if n_groups != EXPECTED_N_GROUPS:
        raise RuntimeError(
            f"Expected {EXPECTED_N_GROUPS} element systems, got {n_groups}"
        )
    logger.info(
        "Locked corpus verified: %d samples, %d element systems",
        len(y),
        n_groups,
    )

    arm_a = run_arm(
        "A",
        "v3.0 20D vocabulary verbatim (protocol-matched control)",
        list(ENERGY_V11_FEATURE_NAMES),
        X_a,
        y,
        groups,
    )
    X_b = build_arm_b_matrix(kept)
    arm_b = run_arm(
        "B",
        "v3.3 24D = Arm A + 4 locked pairwise additions (treatment)",
        list(ENERGY_V33_FEATURE_NAMES),
        X_b,
        y,
        groups,
    )

    violations = rd3_violations(arm_a, arm_b)
    if violations:
        for violation in violations:
            logger.error("RD-3 TRIP-WIRE: %s", violation)
        logger.error(
            "RD-3 STOP: statistic > 0.95 - leakage investigation must "
            "complete BEFORE any result is posted. No sidecar or artifact "
            "written."
        )
        return 2

    decision = evaluate_decision_rule(arm_a.pooled_r2, arm_b.pooled_r2)
    payload = build_payload(arm_a, arm_b, decision)
    validate_sidecar_payload(payload)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / METRICS_FILENAME
    with open(metrics_path, "w") as f:
        json.dump(payload, f, indent=2)
    logger.info("Sidecar saved to %s", metrics_path)

    winner = arm_b if decision.winner_arm == "B" else arm_a
    X_w = X_b if decision.winner_arm == "B" else X_a
    artifact = fit_full_data(X_w, y, winner.feature_names, winner, decision)
    model_path = args.output_dir / MODEL_FILENAME
    joblib.dump(artifact, model_path)
    logger.info("Winning-arm artifact (%sD) saved to %s", X_w.shape[1], model_path)

    logger.info(
        "DECISION %s | winner=%s | pooled A=%.6f B=%.6f delta=%.6f | "
        "mean-of-fold A=%.4f+-%.4f B=%.4f+-%.4f | runtime_swap=%s",
        decision.branch,
        decision.winner_arm,
        arm_a.pooled_r2,
        arm_b.pooled_r2,
        arm_b.pooled_r2 - arm_a.pooled_r2,
        arm_a.r2_mean,
        arm_a.r2_std,
        arm_b.r2_mean,
        arm_b.r2_std,
        decision.runtime_swap,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
