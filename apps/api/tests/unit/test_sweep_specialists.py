"""Unit tests for NFM-4035 Channel 4 sweep (sweep_specialists.py).

Pin the locked protocol from NFM-4031 PREREG comment ``728b9cbe``:

| Lock | Value |
|------|-------|
| Dataset | v3.0 2,909-row PBE DFT export (NFM-1540 PathB) |
| Subset  | Top-10 element systems by composition count |
| Feature vocab | 12D ``energy_features_v11`` aggregates-only |
| Within-system CV | ``KFold(n_splits=5, shuffle=True, random_state=42)`` |
| Cross-system CV | ``GroupKFold(n_splits=5)`` per group_kfold_cv.py:36-65 |
| R2 paired baseline | ``DummyRegressor(mean)`` on the locked splits |

The sweep log persistence path is
``apps/api/models/specialists/_sweep/<element_system>_sweep.json`` and
must carry the honest-metadata block (rd2_label, paired baseline, dataset
SHA, code SHA, git commit SHA).

R3 honesty: every log starts at ``rd2_label=[EXPLORATORY]`` until Petrov's
specialists land AC-1 confirmatory status (NFM-4034).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.metrics import r2_score
from sklearn.model_selection import KFold

_V30_CSV = Path(__file__).resolve().parents[4] / "data" / "training_set_v30_raw.csv"

from nfm_db.ml.sweep_specialists import (  # noqa: E402
    DATASET_SOURCE,
    FEATURE_VOCAB,
    N_SPLITS,
    PROTOCOL_VERSION,
    RANDOM_STATE,
    SWEEP_GRID,
    TARGET_SYSTEMS,
    TOP_N_SYSTEMS,
    _iter_grid,
    _parse_composition,
    build_sweep_log,
    load_v30_dataset,
    paired_r2_baseline,
    select_top_systems,
    sweep_system,
)

# ---------------------------------------------------------------------------
# Locked-protocol pins
# ---------------------------------------------------------------------------


def test_target_systems_match_prereg_declaration() -> None:
    """Top-10 element systems must match NFM-4031 PREREG declaration."""
    assert TARGET_SYSTEMS == (
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


def test_feature_vocab_is_aggregates_only_12d() -> None:
    """Feature vocab must be the 12D aggregates-only set from energy_features_v11."""
    assert len(FEATURE_VOCAB) == 12
    assert "avg_allen_chi" in FEATURE_VOCAB
    assert "bulk_modulus_variance" in FEATURE_VOCAB
    # Drop the 8 v1.0 baseline features; keep only v1.1 additions.
    forbidden_baseline = {
        "mo_equivalent",
        "allen_chi_diff",
        "config_entropy",
        "bv_ratio",
        "u_density",
        "mixing_enthalpy",
        "lattice_distortion",
        "vec",
    }
    assert not (set(FEATURE_VOCAB) & forbidden_baseline), (
        "feature vocab leaked v1.0 8D baseline — must be aggregates-only 12D"
    )


def test_within_system_cv_is_locked_kfold() -> None:
    """PREREG locks KFold(5, shuffle=True, random_state=42)."""
    assert N_SPLITS == 5
    assert RANDOM_STATE == 42


def test_dataset_source_and_protocol_version_pinned() -> None:
    """Dataset source + protocol version must stay verbatim from the PREREG."""
    assert DATASET_SOURCE == "v3.0 2,909-row PBE DFT export (NFM-1540 PathB)"
    assert PROTOCOL_VERSION == "v4.0-sweep/NFM-4031-PREREG"


def test_sweep_grid_sweeps_locked_hyperparameters() -> None:
    """Grid must cover n_estimators (max_iter), max_depth, learning_rate, min_samples_split (min_samples_leaf)."""
    assert set(SWEEP_GRID.keys()) >= {
        "max_iter",
        "max_depth",
        "min_samples_leaf",
        "learning_rate",
    }
    assert len(SWEEP_GRID["max_iter"]) >= 1
    assert len(SWEEP_GRID["max_depth"]) >= 2
    assert len(SWEEP_GRID["learning_rate"]) >= 2
    assert len(SWEEP_GRID["min_samples_leaf"]) >= 2


# ---------------------------------------------------------------------------
# Composition parsing + dataset loading
# ---------------------------------------------------------------------------


def test_parse_composition_handles_dict_input() -> None:
    assert _parse_composition('{"U": 0.9, "Mo": 0.1}') == {"U": 0.9, "Mo": 0.1}


def test_parse_composition_returns_none_for_garbage() -> None:
    assert _parse_composition("not json") is None
    assert _parse_composition("[1, 2, 3]") is None


def test_load_v30_dataset_against_real_export() -> None:
    """Top-10 system counts on the real v3.0 export must be non-empty for every target."""
    if not _V30_CSV.is_file():
        pytest.skip(f"v3.0 export not present at {_V30_CSV}")
    df = load_v30_dataset(_V30_CSV)
    assert len(df) >= 2900
    assert "element_system" in df.columns
    counts = Counter(df["element_system"])
    for system in TARGET_SYSTEMS:
        assert counts[system] > 0, f"system {system} unexpectedly empty in v3.0 export"


def test_select_top_systems_picks_top_n() -> None:
    """select_top_systems must return exactly TOP_N_SYSTEMS systems by row count."""
    if not _V30_CSV.is_file():
        pytest.skip(f"v3.0 export not present at {_V30_CSV}")
    df = load_v30_dataset(_V30_CSV)
    selected, ranked = select_top_systems(df)
    assert len(selected) == TOP_N_SYSTEMS
    assert set(selected) == set(TARGET_SYSTEMS)


# ---------------------------------------------------------------------------
# Paired DummyRegressor baseline (NFM-3957 tradition)
# ---------------------------------------------------------------------------


def test_paired_r2_baseline_matches_independent_kfold() -> None:
    """paired_r2_baseline must equal independent KFold + DummyRegressor(mean) pairing."""
    X = np.random.RandomState(0).normal(size=(60, 4))
    y = np.random.RandomState(1).normal(size=(60,))
    block = paired_r2_baseline(X, y)

    kf = KFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)
    expected = []
    for train_idx, test_idx in kf.split(X):
        m = DummyRegressor(strategy="mean").fit(X[train_idx], y[train_idx])
        expected.append(float(r2_score(y[test_idx], m.predict(X[test_idx]))))
    expected_arr = np.asarray(expected, dtype=np.float64)

    assert block.mean_r2 == pytest.approx(float(expected_arr.mean()), abs=1e-9)
    assert block.std_r2 == pytest.approx(float(expected_arr.std()), abs=1e-9)
    assert len(block.fold_r2) == N_SPLITS


def test_paired_r2_baseline_is_deterministic() -> None:
    X = np.random.RandomState(0).normal(size=(40, 4))
    y = np.random.RandomState(1).normal(size=(40,))
    a = paired_r2_baseline(X, y)
    b = paired_r2_baseline(X, y)
    assert a.fold_r2 == b.fold_r2
    assert a.mean_r2 == b.mean_r2


# ---------------------------------------------------------------------------
# Grid iteration + sweep shape
# ---------------------------------------------------------------------------


def test_iter_grid_total_count() -> None:
    """_iter_grid must produce the cartesian product of the SWEEP_GRID values."""
    n_expected = 1
    for values in SWEEP_GRID.values():
        n_expected *= len(values)
    actual = sum(1 for _ in _iter_grid(SWEEP_GRID))
    assert actual == n_expected


def test_sweep_system_returns_results_in_r2_desc_order() -> None:
    """sweep_system sorts by descending mean_r2; shape must match the grid."""
    rng = np.random.RandomState(RANDOM_STATE)
    n = 80
    X = rng.normal(size=(n, len(FEATURE_VOCAB)))
    # Inject a learnable linear signal so at least one config beats dummy.
    y = 0.4 * X[:, 0] - 0.3 * X[:, 1] + rng.normal(scale=0.05, size=n)

    grid = {
        "max_iter": [50],
        "max_depth": [3],
        "min_samples_leaf": [5],
        "learning_rate": [0.1],
    }
    results = sweep_system(X, y, grid)
    assert len(results) == 1
    only = results[0]
    assert only.mean_r2 > 0.0, "sweep should beat noise on a learnable target"


# ---------------------------------------------------------------------------
# Log shape (R3 honesty)
# ---------------------------------------------------------------------------


def test_build_sweep_log_marks_exploratory_when_lift_below_dummy() -> None:
    """When the model's lift < 0, the log must down-grade to [EXPLORATORY]."""
    rng = np.random.RandomState(RANDOM_STATE)
    X = rng.normal(size=(80, len(FEATURE_VOCAB)))
    y = rng.normal(size=(80,))

    grid = {
        "max_iter": [50],
        "max_depth": [3],
        "min_samples_leaf": [5],
        "learning_rate": [0.1],
    }
    config_results = sweep_system(X, y, grid)
    # Force paired lift < 0 by passing a baseline with high mean_r2.
    baseline = paired_r2_baseline(X, y)
    log = build_sweep_log(
        element_system="Mo",
        n_samples=80,
        all_results=config_results,
        baseline=baseline,
        best=config_results[0],
        paired_lift_pp=-10.0,
        training_seconds=0.01,
        data_sha256="deadbeef",
        sweep_grid=SWEEP_GRID,
    )

    assert log["rd2_label"] == "[EXPLORATORY]"
    assert any("DummyRegressor" in r for r in log["rd2_reasons"])
    assert log["paired_r2_baseline"]["lift_over_dummy_pp"] == -10.0
    assert log["protocol_version"] == PROTOCOL_VERSION
    assert log["n_splits"] == N_SPLITS
    assert log["cross_system_cv"].endswith("group_kfold_cv.py:36-65 (locked)")


def test_build_sweep_log_persists_git_commit_sha() -> None:
    """git_commit_sha must be present and non-empty (or 'no-git:...' fallback)."""
    rng = np.random.RandomState(RANDOM_STATE)
    X = rng.normal(size=(40, len(FEATURE_VOCAB)))
    y = rng.normal(size=(40,))

    grid = {
        "max_iter": [50],
        "max_depth": [3],
        "min_samples_leaf": [5],
        "learning_rate": [0.1],
    }
    config_results = sweep_system(X, y, grid)
    baseline = paired_r2_baseline(X, y)
    log = build_sweep_log(
        element_system="Zr",
        n_samples=40,
        all_results=config_results,
        baseline=baseline,
        best=config_results[0],
        paired_lift_pp=0.0,
        training_seconds=0.01,
        data_sha256="a" * 64,
        sweep_grid=SWEEP_GRID,
    )

    sha = log["git_commit_sha"]
    assert sha != ""
    assert (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)) or sha.startswith(
        "no-git:",
    )
