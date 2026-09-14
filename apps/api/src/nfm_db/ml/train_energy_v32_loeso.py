"""Confirmatory leave-one-element-system-out (LOESO) ablation — Channel 4 v3.2 (NFM-4853).

Locked pre-registration: **NFM-4860** (canonical record), approved verbatim by NDE
in NFM-4853 comment 39968777 (2026-09-14T01:47:02Z). This harness implements that
protocol with no deviations; any fold failure or protocol deviation is a STOP
condition (document + fresh NDE ruling), never a silent workaround.

Two arms, one splitter:
  - Arm A (primary): v3.0 20D vocabulary verbatim (ENERGY_V11_FEATURE_NAMES).
  - Arm B (diagnostic comparator only): v3.1 12D aggregates-only vocabulary
    verbatim (ENERGY_V31_FEATURE_NAMES). Answers H2 only — never a ship candidate.
  - Splitter: sklearn LeaveOneGroupOut over the 68 element systems (one fold per
    system; each system's rows scored by a model that never saw that system).
  - Fresh XGBRegressor per fold, XGB_PARAMS verbatim from train_energy_v30.py:149-161,
    seed 42, no hyperparameter search, single deterministic run per arm.

Readouts (NFM-4860):
  - per-system n_test / R² / MAE / RMSE (+ constant-train-mean no-transfer floor
    under identical splits);
  - pooled R² + MAE over all 2,909 LOESO-scored samples (primary statistic);
  - near-vacuous guard: held-out-system target std < 0.010 eV/atom → flagged,
    reported descriptively, excluded from bucket counts (Zr/Ru lesson);
  - bucket counts only for n_test ≥ 20 (three-band rule ≥0.60 / 0.30–0.60 / <0.30);
  - diagnostics (arm A): gain + SHAP importance of the pairwise stratum
    (vs the 54% prior from the v4.0 root-cause doc) and Spearman ρ between
    per-system LOESO R² and within-system target std.

Decision rule (pre-specified on pooled LOESO R², arm A):
    ≥ 0.60        → v3.2 becomes a dispatch *candidate* (runtime promotion needs
                    its own follow-up prereg + NDE review)
    0.30 – <0.60  → route to NDE: (ii) dispatch-restricted pilot (needs NDE+CTO
                    supersession of the AC-3 ABORT) vs data acquisition
    < 0.30        → falsified; no further feature-axis surgery on this dataset
H2 (Δ pooled R² = A − B > 0) is evaluated regardless of H1 and routes v3.3
feature design only, never dispatch.

Runtime boundary: this module is research-side only. It writes a metrics sidecar;
it does not touch ENERGY_PREDICTOR_VERSION, prediction_service, or any shipped
artifact. v3.0's permanent [EXPLORATORY] status is untouched.

Usage:
    cd apps/api && python -m nfm_db.ml.train_energy_v32_loeso
    cd apps/api && python -m nfm_db.ml.train_energy_v32_loeso --data-dir /path/to/data
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import spearmanr
from sklearn.model_selection import LeaveOneGroupOut

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
)
from nfm_db.ml.energy_features_v31 import (
    ENERGY_V31_FEATURE_NAMES,
    V31_DROPPED_FEATURE_NAMES,
    compute_energy_features_v31,
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

RUN_TAG = "v3.2-loeso-NFM-4853"
PREREG_LOCK = (
    "NFM-4860 (canonical prereg record), locked verbatim; "
    "[PREREG-APPROVED] NFM-4853 comment 39968777 2026-09-14T01:47:02Z (NDE)"
)
SIDECAR_FILENAME = "energy_predictor_v3.2_loeso_metrics.json"

EXPECTED_N_GROUPS = 68  # NFM-4860 / brief §2 lineage: sizes 2–237, median 17.5

# Locked guard thresholds (NFM-4860)
NEAR_VACUOUS_STD = 0.010  # eV/atom — Zr/Ru near-vacuous-range lesson
BUCKET_MIN_N = 20
BAND_HIGH = 0.60
BAND_LOW = 0.30

# Pairwise-stratum diagnostics (arm A). The v4.0 root-cause prior: 54% of
# impurity gain in dg_en_radius_distance (0.3635) + max_pair_en_diff (0.1798).
PAIRWISE_TOP2 = ("dg_en_radius_distance", "max_pair_en_diff")
PAIRWISE_STRATUM_8 = tuple(sorted(V31_DROPPED_FEATURE_NAMES))


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _safe_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """R² with a zero-variance guard (constant y_true → 0.0, no NaN)."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    if ss_tot == 0.0:
        return 0.0
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    return 1.0 - ss_res / ss_tot


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))


def r2_band(r2: float) -> str:
    """Three-band rule label (NFM-4860)."""
    if r2 >= BAND_HIGH:
        return "ge_0.60"
    if r2 >= BAND_LOW:
        return "0.30-0.60"
    return "lt_0.30"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemResult:
    """One LOESO fold = one held-out element system."""

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
    """Pooled LOESO readout for one arm."""

    arm: str
    label: str
    feature_names: tuple[str, ...]
    n_samples: int
    n_systems: int
    pooled_r2: float
    pooled_mae: float
    pooled_rmse: float
    pooled_baseline_r2: float
    pooled_baseline_mae: float
    per_system: tuple[SystemResult, ...]
    bucket_counts: dict[str, int]
    n_near_vacuous: int
    near_vacuous_systems: tuple[str, ...]
    spearman_r2_vs_target_std_all: float | None
    spearman_r2_vs_target_std_eligible: float | None
    pairwise_gain_share_top2: float | None
    pairwise_gain_share_8: float | None
    pairwise_shap_share_top2: float | None
    pairwise_shap_share_8: float | None

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["per_system"] = [asdict(s) for s in self.per_system]
        d["feature_names"] = list(self.feature_names)
        d["near_vacuous_systems"] = list(self.near_vacuous_systems)
        return d


# ---------------------------------------------------------------------------
# Arm-B matrix over the same kept compositions as arm A
# ---------------------------------------------------------------------------


def build_arm_b_matrix(kept: list[dict[str, float]]) -> np.ndarray:
    """12D aggregates-only matrix for the same rows arm A kept.

    ``derive_kept_compositions`` replicates train_energy_v30.build_dataset's
    filter, so iterating it reproduces the exact row order of arm A's X, y.
    """
    feats = [compute_energy_features_v31(comp) for comp in kept]
    return pd.DataFrame(feats, columns=ENERGY_V31_FEATURE_NAMES).to_numpy(dtype=float)


# ---------------------------------------------------------------------------
# LOESO core
# ---------------------------------------------------------------------------


def _gain_shares(
    model: xgb.XGBRegressor,
    feature_names: tuple[str, ...],
) -> tuple[float, float]:
    """Gain-importance shares of the pairwise stratum for one fold model."""
    gain = model.get_booster().get_score(importance_type="gain")
    total = sum(gain.values())
    if total <= 0.0:
        return 0.0, 0.0
    gain_by_name = {
        feature_names[int(k.lstrip("f"))]: float(v) for k, v in gain.items()
    }
    top2 = sum(gain_by_name.get(n, 0.0) for n in PAIRWISE_TOP2)
    stratum8 = sum(gain_by_name.get(n, 0.0) for n in PAIRWISE_STRATUM_8)
    return top2 / total, stratum8 / total


def _shap_shares(
    model: xgb.XGBRegressor,
    X_te: np.ndarray,
    feature_names: tuple[str, ...],
) -> tuple[float, float]:
    """Mean|SHAP| shares of the pairwise stratum for one fold model."""
    import shap  # deferred: only arm A pays the import + explain cost

    sv = shap.TreeExplainer(model).shap_values(X_te)
    mean_abs = np.abs(np.asarray(sv)).mean(axis=0)
    total = float(mean_abs.sum())
    if total <= 0.0:
        return 0.0, 0.0
    name_to_idx = {name: i for i, name in enumerate(feature_names)}
    top2 = float(mean_abs[[name_to_idx[n] for n in PAIRWISE_TOP2]].sum())
    stratum8 = float(mean_abs[[name_to_idx[n] for n in PAIRWISE_STRATUM_8]].sum())
    return top2 / total, stratum8 / total


def run_loeso_arm(
    X: np.ndarray,
    y: np.ndarray,
    groups: list[str],
    *,
    arm: str,
    label: str,
    feature_names: tuple[str, ...],
    random_state: int = RANDOM_STATE,
    xgb_params: dict[str, object] | None = None,
    collect_shap: bool = False,
) -> ArmResult:
    """Run the locked LOESO protocol on one arm.

    ``xgb_params`` exists for unit tests of harness determinism on synthetic
    data only; the confirmatory run always passes ``None`` → XGB_PARAMS verbatim.
    """
    params = dict(XGB_PARAMS) if xgb_params is None else dict(xgb_params)
    logo = LeaveOneGroupOut()

    per_system: list[SystemResult] = []
    pooled_true: list[np.ndarray] = []
    pooled_pred: list[np.ndarray] = []
    pooled_base: list[np.ndarray] = []
    gain_top2: list[float] = []
    gain_8: list[float] = []
    shap_top2: list[float] = []
    shap_8: list[float] = []

    for fold_idx, (train_idx, test_idx) in enumerate(
        logo.split(X, y, groups=groups)
    ):
        system = groups[test_idx[0]]
        # --- split integrity (STOP on violation, per NFM-4860 guard) ---
        if any(groups[i] == system for i in train_idx):
            raise RuntimeError(
                f"Split integrity violated in fold {fold_idx}: system '{system}' "
                "leaked into its own training fold. STOP — document, do not patch."
            )
        if set(groups[i] for i in test_idx) != {system}:
            raise RuntimeError(
                f"Fold {fold_idx} test block is not exactly system '{system}'. STOP."
            )

        X_tr, X_te = X[train_idx], X[test_idx]
        y_tr, y_te = y[train_idx], y[test_idx]

        model = xgb.XGBRegressor(**{**params, "random_state": random_state})
        model.fit(X_tr, y_tr, verbose=False)
        pred = model.predict(X_te)

        baseline_pred = np.full_like(y_te, y_tr.mean(), dtype=float)

        target_std = float(np.std(y_te, ddof=0))
        near_vacuous = target_std < NEAR_VACUOUS_STD
        r2 = _safe_r2(y_te, pred)
        row = SystemResult(
            system=system,
            n_test=len(test_idx),
            target_std=round(target_std, 6),
            near_vacuous=near_vacuous,
            r2=round(r2, 6),
            mae=round(_mae(y_te, pred), 6),
            rmse=round(_rmse(y_te, pred), 6),
            baseline_r2=round(_safe_r2(y_te, baseline_pred), 6),
            baseline_mae=round(_mae(y_te, baseline_pred), 6),
            bucket_eligible=(len(test_idx) >= BUCKET_MIN_N) and not near_vacuous,
            r2_band=r2_band(r2),
        )
        per_system.append(row)

        pooled_true.append(y_te)
        pooled_pred.append(pred)
        pooled_base.append(baseline_pred)

        g_top2, g_8 = _gain_shares(model, feature_names)
        gain_top2.append(g_top2)
        gain_8.append(g_8)
        if collect_shap:
            s_top2, s_8 = _shap_shares(model, X_te, feature_names)
            shap_top2.append(s_top2)
            shap_8.append(s_8)

        logger.info(
            "[%s] fold %d/%d system=%-24s n=%3d std=%.4f R²=%+.4f "
            "base=%+.4f %s",
            arm,
            fold_idx + 1,
            len(set(groups)),
            system,
            len(test_idx),
            target_std,
            r2,
            row.baseline_r2,
            "NEAR-VACUOUS" if near_vacuous else "",
        )

    y_all = np.concatenate(pooled_true)
    p_all = np.concatenate(pooled_pred)
    b_all = np.concatenate(pooled_base)

    eligible = [s for s in per_system if s.bucket_eligible]
    buckets: dict[str, int] = {"eligible_systems": len(eligible)}
    for band in ("ge_0.60", "0.30-0.60", "lt_0.30"):
        buckets[band] = sum(1 for s in eligible if s.r2_band == band)

    near_vac = [s.system for s in per_system if s.near_vacuous]

    def _spearman(rows: list[SystemResult]) -> float | None:
        if len(rows) < 3:
            return None
        rho = spearmanr([s.r2 for s in rows], [s.target_std for s in rows]).statistic
        return None if np.isnan(rho) else float(round(rho, 4))

    return ArmResult(
        arm=arm,
        label=label,
        feature_names=tuple(feature_names),
        n_samples=len(y),
        n_systems=len(per_system),
        pooled_r2=round(_safe_r2(y_all, p_all), 6),
        pooled_mae=round(_mae(y_all, p_all), 6),
        pooled_rmse=round(_rmse(y_all, p_all), 6),
        pooled_baseline_r2=round(_safe_r2(y_all, b_all), 6),
        pooled_baseline_mae=round(_mae(y_all, b_all), 6),
        per_system=tuple(per_system),
        bucket_counts=buckets,
        n_near_vacuous=len(near_vac),
        near_vacuous_systems=tuple(near_vac),
        spearman_r2_vs_target_std_all=_spearman(per_system),
        spearman_r2_vs_target_std_eligible=_spearman(eligible),
        pairwise_gain_share_top2=round(float(np.mean(gain_top2)), 6) if gain_top2 else None,
        pairwise_gain_share_8=round(float(np.mean(gain_8)), 6) if gain_8 else None,
        pairwise_shap_share_top2=round(float(np.mean(shap_top2)), 6) if shap_top2 else None,
        pairwise_shap_share_8=round(float(np.mean(shap_8)), 6) if shap_8 else None,
    )


# ---------------------------------------------------------------------------
# Decision rule (pre-specified — the harness labels, humans adjudicate)
# ---------------------------------------------------------------------------


def decision_band(pooled_r2_arm_a: float) -> tuple[str, str]:
    """Return (band, routing) for the pre-specified H1 decision rule."""
    if pooled_r2_arm_a >= BAND_HIGH:
        return (
            "pass_dispatch_candidate",
            "H1 PASS: pooled LOESO R² ≥ 0.60 — v3.2 becomes a dispatch candidate. "
            "Any runtime promotion requires its own follow-up prereg + NDE review.",
        )
    if pooled_r2_arm_a >= BAND_LOW:
        return (
            "mid_band_route_to_nde",
            "0.30 ≤ pooled < 0.60 — v3.x falsified for cross-system generalization; "
            "NDE routes between (ii) dispatch-restricted pilot (needs NDE+CTO "
            "supersession of AC-3 ABORT) and data acquisition.",
        )
    return (
        "falsified",
        "pooled < 0.30 — falsified; no further feature-axis surgery on this "
        "dataset; escalate the coverage shortfall to NDE/CTO.",
    )


# ---------------------------------------------------------------------------
# Sidecar schema validation (shared with unit tests)
# ---------------------------------------------------------------------------

_PER_SYSTEM_KEYS = frozenset(SystemResult.__dataclass_fields__)


def validate_sidecar_payload(payload: dict[str, object]) -> list[str]:
    """Validate the v3.2 sidecar against the NFM-4860 schema.

    Returns a list of violations (empty = valid). Raises nothing so callers
    (main: STOP; tests: assert) decide how to fail.
    """
    violations: list[str] = []
    for key in ("run_tag", "preregistration", "n_samples", "n_groups", "arms"):
        if key not in payload:
            violations.append(f"missing top-level key '{key}'")
    arms = payload.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"A", "B"}:
        return [*violations, "'arms' must contain exactly keys 'A' and 'B'"]

    expected_feats = {
        "A": list(ENERGY_V11_FEATURE_NAMES),
        "B": list(ENERGY_V31_FEATURE_NAMES),
    }
    for arm_id, expect_len in (("A", 20), ("B", 12)):
        arm = arms[arm_id]  # type: ignore[index]
        for key in (
            "pooled_r2",
            "pooled_mae",
            "pooled_baseline_r2",
            "bucket_counts",
            "per_system",
            "feature_names",
        ):
            if key not in arm:
                violations.append(f"arm {arm_id}: missing '{key}'")
        names = arm.get("feature_names")
        if isinstance(names, list):
            if len(names) != expect_len:
                violations.append(
                    f"arm {arm_id}: expected {expect_len} features, got {len(names)}"
                )
            if names != expected_feats[arm_id]:
                violations.append(f"arm {arm_id}: feature_names not the locked vocab")
        rows = arm.get("per_system")
        if isinstance(rows, list) and rows:
            for i, row in enumerate(rows):
                missing = _PER_SYSTEM_KEYS - set(row)
                if missing:
                    violations.append(
                        f"arm {arm_id} per_system[{i}]: missing {sorted(missing)}"
                    )
                    break

    band = payload.get("decision_band_arm_a")
    pooled = arms["A"].get("pooled_r2") if isinstance(arms["A"], dict) else None  # type: ignore[index]
    if isinstance(pooled, (int, float)) and band in (
        "pass_dispatch_candidate",
        "mid_band_route_to_nde",
        "falsified",
    ):
        expected, _ = decision_band(float(pooled))
        if band != expected:
            violations.append(
                f"decision_band_arm_a '{band}' inconsistent with pooled_r2 {pooled}"
            )
    return violations


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_payload(arm_a: ArmResult, arm_b: ArmResult) -> dict[str, object]:
    band, routing = decision_band(arm_a.pooled_r2)
    return {
        "run_tag": RUN_TAG,
        "preregistration": PREREG_LOCK,
        "n_samples": arm_a.n_samples,
        "n_groups": arm_a.n_systems,
        "random_state": RANDOM_STATE,
        "protocol_locked": {
            "splitter": "LeaveOneGroupOut (one fold per element system)",
            "grouping_key": "element_system (sorted non-U solute set)",
            "fresh_model_per_fold": True,
            "hyperparameter_search": False,
            "single_deterministic_run_per_arm": True,
            "xgb_params_locked_to": "XGB_PARAMS in train_energy_v30.py:149-161",
            "near_vacuous_guard": f"target std < {NEAR_VACUOUS_STD} eV/atom",
            "bucket_min_n": BUCKET_MIN_N,
            "dataset": "NFM-1540 PathB (2,909 unique PBE compositions)",
        },
        "arms": {"A": arm_a.to_dict(), "B": arm_b.to_dict()},
        "h2_delta_pooled_r2_ab": round(arm_a.pooled_r2 - arm_b.pooled_r2, 6),
        "h2_verdict": "delta_gt_0" if arm_a.pooled_r2 > arm_b.pooled_r2 else "delta_le_0",
        "decision_band_arm_a": band,
        "decision_routing": routing,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="EnergyPredictor v3.2 LOESO ablation (NFM-4853 / NFM-4860)",
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
        help="Output directory for the LOESO metrics sidecar JSON",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    raw = load_v30_data(args.data_dir)
    X_a, y = build_dataset(raw)
    kept = derive_kept_compositions(raw)
    groups = build_group_labels(kept)
    X_b = build_arm_b_matrix(kept)

    if not (len(groups) == len(y) == len(X_a) == len(X_b)):
        raise RuntimeError(
            f"Alignment failure: y={len(y)}, X_a={len(X_a)}, X_b={len(X_b)}, "
            f"groups={len(groups)}. STOP — investigate before reporting."
        )
    n_groups = len(set(groups))
    if n_groups != EXPECTED_N_GROUPS:
        raise RuntimeError(
            f"Protocol deviation: expected {EXPECTED_N_GROUPS} element systems, "
            f"got {n_groups}. STOP — fresh NDE ruling required (NFM-4860 guard)."
        )

    logger.info("=== EnergyPredictor v3.2 LOESO (NFM-4853 / NFM-4860) ===")
    logger.info(
        "Dataset: %d samples | arm A %dD | arm B %dD | %d element systems",
        len(y), X_a.shape[1], X_b.shape[1], n_groups,
    )

    arm_a = run_loeso_arm(
        X_a, y, groups,
        arm="A",
        label="v3.0 20D vocabulary verbatim (primary)",
        feature_names=tuple(ENERGY_V11_FEATURE_NAMES),
        collect_shap=True,
    )
    arm_b = run_loeso_arm(
        X_b, y, groups,
        arm="B",
        label="v3.1 12D aggregates-only verbatim (diagnostic comparator, H2 only)",
        feature_names=tuple(ENERGY_V31_FEATURE_NAMES),
        collect_shap=False,
    )

    payload = build_payload(arm_a, arm_b)
    violations = validate_sidecar_payload(payload)
    if violations:
        raise RuntimeError(f"Sidecar schema violations: {violations}. STOP.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = args.output_dir / SIDECAR_FILENAME
    sidecar_path.write_text(json.dumps(payload, indent=2))
    logger.info("Sidecar metrics written to %s", sidecar_path)

    logger.info("=== Headline (pre-specified decision rule on pooled LOESO R² arm A) ===")
    logger.info("Pooled LOESO R²  arm A (20D): %.6f", arm_a.pooled_r2)
    logger.info("Pooled LOESO R²  arm B (12D): %.6f", arm_b.pooled_r2)
    logger.info("No-transfer floor (train-mean) pooled R²: A %.6f | B %.6f",
                arm_a.pooled_baseline_r2, arm_b.pooled_baseline_r2)
    logger.info("H2 Δ pooled R² (A−B): %+.6f → %s",
                payload["h2_delta_pooled_r2_ab"], payload["h2_verdict"])
    logger.info("Bucket counts (n≥%d, non-vacuous) A: %s", BUCKET_MIN_N, arm_a.bucket_counts)
    logger.info("Near-vacuous systems flagged: %d %s", arm_a.n_near_vacuous,
                list(arm_a.near_vacuous_systems))
    logger.info("Spearman ρ(R², within-system std) A: all=%s eligible=%s",
                arm_a.spearman_r2_vs_target_std_all,
                arm_a.spearman_r2_vs_target_std_eligible)
    logger.info("Pairwise stratum (arm A, mean over folds): gain top2=%.4f stratum8=%.4f "
                "| SHAP top2=%.4f stratum8=%.4f (prior: 54%% top2 gain)",
                arm_a.pairwise_gain_share_top2 or 0.0,
                arm_a.pairwise_gain_share_8 or 0.0,
                arm_a.pairwise_shap_share_top2 or 0.0,
                arm_a.pairwise_shap_share_8 or 0.0)
    logger.info("Decision: %s — %s", payload["decision_band_arm_a"],
                payload["decision_routing"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
