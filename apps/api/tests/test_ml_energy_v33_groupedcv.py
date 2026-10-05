"""Unit tests for the v3.3 GroupKFold(5) confirmatory run (NFM-5305).

Locked-prereg test contract (NFM-5305, [PREREG-SUBMITTED] comment
40d2e0b5-1bd8-4eda-ae12-1736c01a9d1a; [PREREG-APPROVED] NDE comment
d6e1b268-8b4d-4856-8178-24dca1f28e8b):
  1. split integrity — no element system appears in both train and test of a
     fold; every sample is tested exactly once; the 5 folds partition the
     2,909-sample corpus;
  2. locked vocabularies — Arm A is the frozen 20D list verbatim, Arm B is
     exactly Arm A plus the 4 prereg-locked pairwise additions in order;
  3. calculator semantics — the 4 additions mirror the v1.1 pairwise helper
     conventions (U-inclusive pairs, <2 covered elements -> 0.0, x_i*x_j
     weights), including the Mn/Sn bulk-modulus table gap;
  4. decision rule — the 4-branch rule and its strict threshold boundaries;
  5. RD-3 trip-wire — pooled or per-fold R^2 above 0.95 is flagged;
  6. sidecar schema — the committed sidecar validates, embeds the prereg
     verbatim, and carries a DISPATCH-CANDIDATE-or-better-consistent decision;
  7. protocol locks — XGB_PARAMS imported verbatim from train_energy_v30,
     seed 42, n_splits 5, guard constants inherited from v3.2.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xgboost as xgb

from nfm_db.ml.energy_features_v11 import (
    ENERGY_V11_FEATURE_NAMES,
    _get_lookups,
    compute_energy_features_v11,
)
from nfm_db.ml.energy_features_v33 import (
    ENERGY_V33_FEATURE_NAMES,
    V33_ADDITION_FEATURE_NAMES,
    compute_avg_pair_en_diff,
    compute_energy_features_v33,
    compute_max_pair_bulk_modulus_diff,
    compute_max_pair_work_function_diff,
    compute_min_pair_en_diff,
)
from nfm_db.ml.group_kfold_cv import build_group_labels
from nfm_db.ml.train_energy_v30 import RANDOM_STATE, XGB_PARAMS, build_dataset, load_v30_data
from nfm_db.ml.train_energy_v30_grouped_cv import derive_kept_compositions
from nfm_db.ml.train_energy_v33_groupedcv import (
    ADOPTION_DELTA_MIN,
    BUCKET_MIN_N,
    DISPATCH_BAND_MIN,
    EXPECTED_N_GROUPS,
    EXPECTED_N_SAMPLES,
    KR_ML3_GATE,
    METRICS_FILENAME,
    N_SPLITS,
    NEAR_VACUOUS_STD,
    PREREG_VERBATIM,
    RD3_TRIPWIRE,
    build_arm_b_matrix,
    evaluate_decision_rule,
    rd3_violations,
    run_groupedcv_arm,
    validate_sidecar_payload,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _PROJECT_ROOT / "data"
_MODELS_DIR = _PROJECT_ROOT / "apps" / "api" / "models"
_SIDECAR = _MODELS_DIR / METRICS_FILENAME

requires_real_data = pytest.mark.skipif(
    not (_DATA_DIR / "training_set_v30_raw.csv").exists(),
    reason="v3.0 training CSV not present in this checkout",
)


@pytest.fixture(scope="module")
def real_dataset():
    raw = load_v30_data(_DATA_DIR)
    X_a, y = build_dataset(raw)
    kept = derive_kept_compositions(raw)
    groups = build_group_labels(kept)
    return X_a, y, kept, groups


# ---------------------------------------------------------------------------
# 1. Split integrity
# ---------------------------------------------------------------------------


@requires_real_data
def test_split_integrity_no_leakage_and_partition(real_dataset) -> None:
    """No system in train+test of one fold; each sample tested exactly once."""
    _, y, _, groups = real_dataset
    from sklearn.model_selection import GroupKFold

    gkf = GroupKFold(n_splits=N_SPLITS)
    seen: set[int] = set()
    for train_idx, test_idx in gkf.split(np.zeros((len(y), 1)), y, groups=groups):
        train_groups = {groups[i] for i in train_idx}
        test_groups = {groups[i] for i in test_idx}
        assert not (train_groups & test_groups)
        for i in test_idx:
            assert i not in seen
            seen.add(int(i))
    assert len(seen) == len(y) == EXPECTED_N_SAMPLES
    assert len({g for g in groups}) == EXPECTED_N_GROUPS


@requires_real_data
def test_groupedcv_arm_split_guards(real_dataset) -> None:
    """run_groupedcv_arm returns a full OOF partition (synthetic fast X)."""
    X_a, y, _, groups = real_dataset
    X = X_a[:, :3]  # cheap stand-in: guards + partition, not metrics
    folds, oof_pred, oof_baseline = run_groupedcv_arm(X, y, groups)
    assert len(folds) == N_SPLITS
    assert sum(f.n_test for f in folds) == EXPECTED_N_SAMPLES
    assert np.isfinite(oof_pred).all()
    assert np.isfinite(oof_baseline).all()
    for fold in folds:
        assert fold.n_train + fold.n_test == EXPECTED_N_SAMPLES


# ---------------------------------------------------------------------------
# 2. Locked vocabularies
# ---------------------------------------------------------------------------


def test_locked_vocabularies() -> None:
    """Arm A = frozen 20D verbatim; Arm B = Arm A + 4 additions in order."""
    assert len(ENERGY_V11_FEATURE_NAMES) == 20
    assert len(ENERGY_V33_FEATURE_NAMES) == 24
    assert ENERGY_V33_FEATURE_NAMES[:20] == list(ENERGY_V11_FEATURE_NAMES)
    assert ENERGY_V33_FEATURE_NAMES[20:] == [
        "min_pair_en_diff",
        "avg_pair_en_diff",
        "max_pair_bulk_modulus_diff",
        "max_pair_work_function_diff",
    ]
    assert ENERGY_V33_FEATURE_NAMES[20:] == V33_ADDITION_FEATURE_NAMES


# ---------------------------------------------------------------------------
# 3. Calculator semantics (v1.1 pairwise helper conventions)
# ---------------------------------------------------------------------------


def test_calculators_binary_single_pair_collapses() -> None:
    """U-Zr binary: one pair -> min = avg = max = |chi_U - chi_Zr|."""
    allen_chi = _get_lookups()[0]
    expected = abs(allen_chi["U"] - allen_chi["Zr"])
    comp = {"U": 0.9, "Zr": 0.1}
    assert compute_min_pair_en_diff(comp) == pytest.approx(expected)
    assert compute_avg_pair_en_diff(comp) == pytest.approx(expected)
    feats = compute_energy_features_v33(comp)
    assert feats["min_pair_en_diff"] == pytest.approx(expected)
    assert feats["avg_pair_en_diff"] == pytest.approx(expected)
    assert list(feats.keys()) == ENERGY_V33_FEATURE_NAMES


def test_calculators_ternary_hand_computed() -> None:
    """U-Mo-Nb: hand-computed min/weighted-avg over the 3 unordered pairs."""
    allen_chi = _get_lookups()[0]
    comp = {"U": 0.85, "Mo": 0.10, "Nb": 0.05}
    d_um = abs(allen_chi["U"] - allen_chi["Mo"])
    d_un = abs(allen_chi["U"] - allen_chi["Nb"])
    d_mn = abs(allen_chi["Mo"] - allen_chi["Nb"])
    feats = compute_energy_features_v33(comp)
    assert feats["min_pair_en_diff"] == pytest.approx(min(d_um, d_un, d_mn))
    weight_sum = 0.85 * 0.10 + 0.85 * 0.05 + 0.10 * 0.05
    weighted = (
        0.85 * 0.10 * d_um + 0.85 * 0.05 * d_un + 0.10 * 0.05 * d_mn
    ) / weight_sum
    assert feats["avg_pair_en_diff"] == pytest.approx(weighted)


def test_calculator_bulk_modulus_table_gap() -> None:
    """Mn/Sn lack bulk-modulus entries: U-Mn binary -> 0.0 (<2 covered)."""
    bulk_modulus = _get_lookups()[3]
    assert "Mn" not in bulk_modulus
    assert "Sn" not in bulk_modulus
    assert compute_max_pair_bulk_modulus_diff({"U": 0.95, "Mn": 0.05}) == 0.0
    # ternary U-Mn-Zr still spans the U-Zr pair
    assert compute_max_pair_bulk_modulus_diff({"U": 0.9, "Mn": 0.05, "Zr": 0.05}) == (
        pytest.approx(abs(bulk_modulus["U"] - bulk_modulus["Zr"]))
    )


def test_calculator_single_element_and_u_included() -> None:
    """Single-element compositions and pure U yield 0.0 for all four."""
    for comp in ({"U": 1.0}, {"Mo": 1.0}):
        feats = compute_energy_features_v33(comp)
        for name in V33_ADDITION_FEATURE_NAMES:
            assert feats[name] == 0.0
    # U participates in work-function pairs
    from nfm_db.ml.energy_features_v11 import _WORK_FUNCTION

    comp = {"U": 0.9, "Zr": 0.1}
    assert compute_max_pair_work_function_diff(comp) == pytest.approx(
        abs(_WORK_FUNCTION["U"] - _WORK_FUNCTION["Zr"]),
    )


def test_calculators_deterministic_and_finite() -> None:
    comp = {"U": 0.8, "Mo": 0.1, "Nb": 0.05, "Zr": 0.05}
    first = compute_energy_features_v33(comp)
    second = compute_energy_features_v33(comp)
    assert first == second
    assert all(np.isfinite(v) for v in first.values())


@requires_real_data
def test_arm_b_matrix_alignment(real_dataset) -> None:
    """Arm B rows align 1:1 with build_dataset rows; base 20D identical."""
    X_a, _, kept, _ = real_dataset
    X_b = build_arm_b_matrix(kept)
    assert X_b.shape == (EXPECTED_N_SAMPLES, 24)
    assert np.array_equal(X_b[:, :20], X_a)
    # base columns recomputed through v33 must equal the v3.0 pipeline
    row0 = compute_energy_features_v11(kept[0])
    for j, name in enumerate(ENERGY_V11_FEATURE_NAMES):
        assert X_b[0, j] == pytest.approx(row0[name])


# ---------------------------------------------------------------------------
# 4. Decision rule
# ---------------------------------------------------------------------------


def test_decision_rule_branches() -> None:
    assert evaluate_decision_rule(0.88, 0.89).branch == "CONTROL-CLEARS"
    assert evaluate_decision_rule(0.88, 0.89).winner_arm == "B"
    assert evaluate_decision_rule(0.88, 0.884).winner_arm == "A"
    assert evaluate_decision_rule(0.80, 0.87).branch == "H1-PROMOTE"
    assert evaluate_decision_rule(0.80, 0.87).runtime_swap is True
    d = evaluate_decision_rule(0.70, 0.65)
    assert d.branch == "DISPATCH-CANDIDATE"
    assert d.winner_arm == "A"
    assert d.runtime_swap is False
    assert evaluate_decision_rule(0.70, 0.55).branch == "FAIL"


def test_decision_rule_strict_boundaries() -> None:
    """Thresholds are strict: 0.85 exactly does not clear, 0.60 exactly
    is not a dispatch candidate, delta exactly +0.010 adopts."""
    assert evaluate_decision_rule(KR_ML3_GATE, 0.50).branch != "CONTROL-CLEARS"
    assert (
        evaluate_decision_rule(0.50, KR_ML3_GATE).branch != "H1-PROMOTE"
    )
    assert (
        evaluate_decision_rule(0.50, DISPATCH_BAND_MIN).branch == "FAIL"
    )
    assert (
        evaluate_decision_rule(0.99, 0.99 + ADOPTION_DELTA_MIN).winner_arm
        == "B"
    )


# ---------------------------------------------------------------------------
# 5. RD-3 trip-wire
# ---------------------------------------------------------------------------


def test_rd3_tripwire_flags_implausible_statistics() -> None:
    from nfm_db.ml.train_energy_v33_groupedcv import ArmResult, FoldResult

    def mk_arm(pooled: float, fold_r2: list[float]) -> ArmResult:
        folds = [
            FoldResult(i, 100, 10, 2, r2, 0.1, 0.1) for i, r2 in enumerate(fold_r2)
        ]
        return ArmResult(
            arm="A",
            label="synthetic",
            feature_names=["f"],
            folds=folds,
            n_samples=50,
            n_systems=5,
            pooled_r2=pooled,
            pooled_mae=0.1,
            pooled_rmse=0.1,
            pooled_baseline_r2=0.0,
            pooled_baseline_mae=0.2,
            r2_mean=0.5,
            r2_std=0.1,
            per_system=[],
            bucket_counts={"eligible_systems": 0, "ge_0.60": 0, "0.30-0.60": 0, "lt_0.30": 0},
            n_near_vacuous=0,
            near_vacuous_systems=[],
            spearman_r2_vs_target_std_all=None,
            spearman_r2_vs_target_std_eligible=None,
        )

    clean = mk_arm(0.72, [0.7, 0.3, 0.35, -0.5, 0.75])
    assert rd3_violations(clean, clean) == []
    hot_pooled = mk_arm(RD3_TRIPWIRE + 0.01, [0.5, 0.5, 0.5, 0.5, 0.5])
    assert len(rd3_violations(hot_pooled, clean)) == 1
    hot_fold = mk_arm(0.70, [0.5, RD3_TRIPWIRE + 0.005, 0.5, 0.5, 0.5])
    assert len(rd3_violations(clean, hot_fold)) == 1


# ---------------------------------------------------------------------------
# 6. Sidecar schema
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _SIDECAR.exists(), reason="sidecar not yet committed")
def test_committed_sidecar_schema_and_figures() -> None:
    import json

    with open(_SIDECAR) as f:
        payload = json.load(f)
    validate_sidecar_payload(payload)
    assert payload["run_tag"] == "v3.3-groupedcv-NFM-5305"
    assert payload["confirmatory"] is True
    assert payload["prereg_body_verbatim"] == PREREG_VERBATIM
    assert payload["prereg_body_verbatim"].startswith("[PREREG-SUBMITTED]")
    # pinned figures of record (read back from this run's sidecar)
    assert payload["arms"]["A"]["pooled_r2"] == 0.720376
    assert payload["arms"]["B"]["pooled_r2"] == 0.70619
    assert payload["delta_pooled_r2_ab"] == -0.014186
    assert payload["decision"]["branch"] == "DISPATCH-CANDIDATE"
    assert payload["decision"]["runtime_swap"] is False
    # decision consistency with the pinned numbers
    rederived = evaluate_decision_rule(0.720376, 0.70619)
    assert rederived.branch == payload["decision"]["branch"]
    assert rederived.winner_arm == payload["decision"]["winner_arm"]


def _synthetic_payload(**overrides) -> dict:
    """Minimal validator-passing payload built from the locked vocabularies."""
    per_system = [{"system": f"S{i}", "n_test": 42} for i in range(67)]
    per_system.append({"system": "S67", "n_test": 95})  # 67*42 + 95 = 2,909
    per_fold = [
        {"fold_index": i, "n_test": n}
        for i, n in enumerate((581, 583, 581, 582, 582))
    ]
    base_arm = {
        "per_system": per_system,
        "per_fold": per_fold,
        "bucket_counts": {
            "eligible_systems": 0,
            "ge_0.60": 0,
            "0.30-0.60": 0,
            "lt_0.30": 0,
        },
    }
    payload: dict = {
        "run_tag": "synthetic",
        "preregistration": "synthetic",
        "prereg_body_verbatim": PREREG_VERBATIM,
        "confirmatory": True,
        "n_samples": EXPECTED_N_SAMPLES,
        "n_groups": EXPECTED_N_GROUPS,
        "protocol_locked": {},
        "endpoints": {},
        "arms": {
            "A": {**base_arm, "feature_names": list(ENERGY_V11_FEATURE_NAMES)},
            "B": {**base_arm, "feature_names": list(ENERGY_V33_FEATURE_NAMES)},
        },
        "delta_pooled_r2_ab": 0.0,
        "decision": {
            "branch": "FAIL",
            "winner_arm": "A",
            "winner_rule": "synthetic",
            "runtime_swap": False,
            "routing": "synthetic",
        },
    }
    return {**payload, **overrides}


def test_sidecar_validator_accepts_wellformed_payload() -> None:
    validate_sidecar_payload(_synthetic_payload())


def test_sidecar_validator_rejects_broken_payloads() -> None:
    with pytest.raises(ValueError, match="n_samples"):
        validate_sidecar_payload(_synthetic_payload(n_samples=123))
    with pytest.raises(ValueError, match="n_groups"):
        validate_sidecar_payload(_synthetic_payload(n_groups=67))
    with pytest.raises(ValueError, match="PREREG"):
        validate_sidecar_payload(
            _synthetic_payload(prereg_body_verbatim="not the prereg"),
        )
    wrong_vocab = _synthetic_payload()
    wrong_vocab["arms"] = {
        **wrong_vocab["arms"],
        "A": {**wrong_vocab["arms"]["A"], "feature_names": ["bogus"]},
    }
    with pytest.raises(ValueError, match="vocabulary"):
        validate_sidecar_payload(wrong_vocab)


# ---------------------------------------------------------------------------
# 7. Protocol locks
# ---------------------------------------------------------------------------


def test_protocol_locks() -> None:
    assert N_SPLITS == 5
    assert RANDOM_STATE == 42
    assert XGB_PARAMS["n_estimators"] == 800
    assert XGB_PARAMS["max_depth"] == 5
    assert XGB_PARAMS["random_state"] == 42
    assert NEAR_VACUOUS_STD == 0.010
    assert BUCKET_MIN_N == 20
    assert EXPECTED_N_SAMPLES == 2909
    assert EXPECTED_N_GROUPS == 68
    assert KR_ML3_GATE == 0.85
    assert RD3_TRIPWIRE == 0.95
    # the module trains fresh per-fold models from the imported params
    model = xgb.XGBRegressor(**{**XGB_PARAMS, "random_state": RANDOM_STATE})
    assert model.random_state == 42
    assert model.n_estimators == 800
