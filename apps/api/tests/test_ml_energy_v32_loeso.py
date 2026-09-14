"""Unit tests for the v3.2 LOESO harness (NFM-4853 / NFM-4860).

Locked-prereg test contract (NFM-4860, [PREREG-APPROVED] NFM-4853 c.39968777):
  1. split integrity — no held-out element system appears in its own training
     fold; every test block is exactly one system; folds partition the dataset;
  2. determinism — the harness is single-seed deterministic (identical inputs
     and params → identical per-system and pooled readouts);
  3. sidecar schema — the committed sidecar matches the locked schema, both
     arms carry the locked vocabularies, and the decision band is consistent
     with the pooled arm-A R².

Also covers the guard primitives: near-vacuous flag, three-band rule, and
arm-B row alignment with arm A over the same kept compositions.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from sklearn.model_selection import LeaveOneGroupOut

from nfm_db.ml.energy_features_v11 import ENERGY_V11_FEATURE_NAMES
from nfm_db.ml.energy_features_v31 import ENERGY_V31_FEATURE_NAMES
from nfm_db.ml.group_kfold_cv import build_group_labels
from nfm_db.ml.train_energy_v30 import RANDOM_STATE, build_dataset, load_v30_data
from nfm_db.ml.train_energy_v30_grouped_cv import derive_kept_compositions
from nfm_db.ml.train_energy_v32_loeso import (
    BAND_HIGH,
    BAND_LOW,
    BUCKET_MIN_N,
    EXPECTED_N_GROUPS,
    NEAR_VACUOUS_STD,
    SIDECAR_FILENAME,
    build_arm_b_matrix,
    decision_band,
    r2_band,
    run_loeso_arm,
    validate_sidecar_payload,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _PROJECT_ROOT / "data"
_SIDECAR = _PROJECT_ROOT / "apps" / "api" / "models" / SIDECAR_FILENAME

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
    """Every fold: test block = exactly one system, absent from its train fold;
    the 68 folds partition the full dataset."""
    _, y, _, groups = real_dataset
    logo = LeaveOneGroupOut()
    seen_test: set[int] = set()

    folds = list(logo.split(np.zeros((len(y), 1)), y, groups=groups))
    assert len(folds) == EXPECTED_N_GROUPS
    assert len({g for g in groups}) == EXPECTED_N_GROUPS

    for train_idx, test_idx in folds:
        held_out = groups[test_idx[0]]
        assert set(groups[i] for i in test_idx) == {held_out}
        assert all(groups[i] != held_out for i in train_idx)
        assert not (seen_test & set(test_idx.tolist()))
        seen_test.update(test_idx.tolist())

    assert seen_test == set(range(len(y)))


@requires_real_data
def test_split_integrity_is_order_independent(real_dataset) -> None:
    """LeaveOneGroupOut is deterministic by construction: two derivations of
    the fold assignment are identical."""
    _, y, _, groups = real_dataset
    X_dummy = np.zeros((len(y), 1))
    f1 = list(LeaveOneGroupOut().split(X_dummy, y, groups=groups))
    f2 = list(LeaveOneGroupOut().split(X_dummy, y, groups=groups))
    assert [(tr.tolist(), te.tolist()) for tr, te in f1] == [
        (tr.tolist(), te.tolist()) for tr, te in f2
    ]


# ---------------------------------------------------------------------------
# 2. Arm alignment
# ---------------------------------------------------------------------------


@requires_real_data
def test_arm_b_rows_align_with_arm_a(real_dataset) -> None:
    """Arm B is computed over exactly the rows arm A kept, in the same order."""
    X_a, y, kept, groups = real_dataset
    X_b = build_arm_b_matrix(kept)
    assert X_b.shape == (len(y), len(ENERGY_V31_FEATURE_NAMES))
    assert X_b.shape[0] == X_a.shape[0] == len(groups)


def test_locked_vocabularies_untouched() -> None:
    assert len(ENERGY_V11_FEATURE_NAMES) == 20
    assert len(ENERGY_V31_FEATURE_NAMES) == 12
    assert set(ENERGY_V31_FEATURE_NAMES) < set(ENERGY_V11_FEATURE_NAMES)


# ---------------------------------------------------------------------------
# 3. Determinism (synthetic, reduced trees — the property tested is the
#    harness's, XGB_PARAMS themselves are never varied in the confirmatory run)
# ---------------------------------------------------------------------------


def _synthetic(n_per_group: int = 30, n_groups: int = 6, seed: int = 7):
    rng = np.random.default_rng(seed)
    groups = [f"sys_{g}" for g in range(n_groups) for _ in range(n_per_group)]
    X = rng.normal(size=(n_per_group * n_groups, 5))
    X[:, 0] = np.arange(X.shape[0]) % n_groups  # group-correlated signal
    y = X[:, 0] * 2.0 + X[:, 1] * 0.5 + rng.normal(scale=0.05, size=X.shape[0])
    return X, y, groups


_TEST_PARAMS: dict[str, object] = {
    "n_estimators": 40,
    "max_depth": 3,
    "learning_rate": 0.1,
    "subsample": 0.7,
    "colsample_bytree": 0.7,
    "reg_alpha": 1.5,
    "reg_lambda": 10.0,
    "min_child_weight": 10,
    "gamma": 0.1,
    "random_state": RANDOM_STATE,
    "verbosity": 0,
}


def test_loeso_determinism_synthetic() -> None:
    X, y, groups = _synthetic()
    r1 = run_loeso_arm(
        X, y, groups, arm="T", label="synthetic", feature_names=tuple("abcde"),
        xgb_params=_TEST_PARAMS,
    )
    r2 = run_loeso_arm(
        X, y, groups, arm="T", label="synthetic", feature_names=tuple("abcde"),
        xgb_params=_TEST_PARAMS,
    )
    assert r1.to_dict() == r2.to_dict()


def test_loeso_split_integrity_synthetic() -> None:
    """The harness runs the guard end-to-end: per-system rows partition the
    dataset, every row is bucket-labelled, pooled R² is finite."""
    X, y, groups = _synthetic()
    r = run_loeso_arm(
        X, y, groups, arm="T", label="synthetic", feature_names=tuple("abcde"),
        xgb_params=_TEST_PARAMS,
    )
    assert r.n_systems == 6
    assert sum(s.n_test for s in r.per_system) == len(y)
    assert all(s.r2_band in ("ge_0.60", "0.30-0.60", "lt_0.30") for s in r.per_system)
    assert np.isfinite(r.pooled_r2)


# ---------------------------------------------------------------------------
# 4. Guard primitives
# ---------------------------------------------------------------------------


def test_near_vacuous_threshold() -> None:
    assert NEAR_VACUOUS_STD == 0.010
    assert BUCKET_MIN_N == 20


def test_r2_band_three_band_rule() -> None:
    assert r2_band(0.75) == "ge_0.60"
    assert r2_band(BAND_HIGH) == "ge_0.60"
    assert r2_band(0.599999) == "0.30-0.60"
    assert r2_band(BAND_LOW) == "0.30-0.60"
    assert r2_band(0.2999) == "lt_0.30"
    assert r2_band(-1.0) == "lt_0.30"


def test_decision_band_routing() -> None:
    assert decision_band(0.61)[0] == "pass_dispatch_candidate"
    assert decision_band(0.60)[0] == "pass_dispatch_candidate"
    assert decision_band(0.45)[0] == "mid_band_route_to_nde"
    assert decision_band(0.30)[0] == "mid_band_route_to_nde"
    assert decision_band(0.29)[0] == "falsified"


# ---------------------------------------------------------------------------
# 5. Sidecar schema
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _SIDECAR.exists(), reason="sidecar not yet committed")
def test_committed_sidecar_schema() -> None:
    import json

    payload = json.loads(_SIDECAR.read_text())
    assert validate_sidecar_payload(payload) == []
    assert payload["n_groups"] == EXPECTED_N_GROUPS
    assert payload["random_state"] == RANDOM_STATE


def test_sidecar_validator_rejects_broken_payload() -> None:
    violations = validate_sidecar_payload({"run_tag": "x"})
    assert any("arms" in v for v in violations)

    payload = {
        "run_tag": "x",
        "preregistration": "x",
        "n_samples": 10,
        "n_groups": 2,
        "arms": {
            "A": {
                "pooled_r2": 0.9,
                "pooled_mae": 0.01,
                "pooled_baseline_r2": 0.0,
                "bucket_counts": {},
                "per_system": [],
                "feature_names": ["wrong", "vocab"],
            },
            "B": {
                "pooled_r2": 0.5,
                "pooled_mae": 0.02,
                "pooled_baseline_r2": 0.0,
                "bucket_counts": {},
                "per_system": [],
                "feature_names": ["wrong"],
            },
        },
        "decision_band_arm_a": "falsified",  # inconsistent with 0.9
    }
    violations = validate_sidecar_payload(payload)
    assert any("feature_names" in v for v in violations)
    assert any("inconsistent" in v for v in violations)
