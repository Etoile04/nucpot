"""Unit tests for the PhaseClassifier v2.0 DummyClassifier baseline (NFM-3957).

NDE ruling on NFM-3954 (item 4) requires every training run to log a
``DummyClassifier(strategy='most_frequent')`` baseline scored on the *same*
locked GroupKFold splits as the model, so the OKR acceptance bar can never
again drift past a constant predictor unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from sklearn.dummy import DummyClassifier
from sklearn.model_selection import GroupKFold, cross_val_score
from sklearn.tree import DecisionTreeClassifier

from nfm_db.ml.train_v20 import (
    DUMMY_BASELINE_STRATEGY,
    REQUIRED_CV_SPLITS,
    RD2Assessment,
    _apply_dummy_baseline_gate,
    compute_dummy_baseline,
    locked_group_kfold_splits,
    prepare_v20_training_data,
    train_phase_classifier_v20,
)

# Eight element systems x four compositions. Every system carries both labels so
# each GroupKFold fold sees H and M (classification_report needs both classes).
SOLUTE_SYSTEMS: tuple[tuple[str, ...], ...] = (
    ("Mo",),
    ("Nb",),
    ("Zr",),
    ("Ti",),
    ("V",),
    ("Cr",),
    ("Ta",),
    ("Ru",),
)


def _synthetic_records() -> list[dict[str, str]]:
    """Build a small, dedup-safe H/M training table spanning 8 element systems."""
    rows: list[dict[str, str]] = []
    for system in SOLUTE_SYSTEMS:
        for index, solute_pct in enumerate((4.0, 7.0, 11.0, 16.0)):
            share = solute_pct / len(system)
            composition = {"U": 100.0 - solute_pct} | {el: share for el in system}
            rows.append(
                {
                    "composition": json.dumps(composition),
                    # Low solute -> H, high solute -> M: both labels in every group.
                    "label": "H" if index < 2 else "M",
                }
            )
    return rows


@pytest.fixture
def training_csv(tmp_path: Path) -> Path:
    """Write the synthetic training table to a CSV the trainer can read."""
    import pandas as pd

    path = tmp_path / "training_set_synthetic.csv"
    pd.DataFrame(_synthetic_records()).to_csv(path, index=False)
    return path


@pytest.fixture
def prepared(training_csv: Path):
    """Prepared 8D matrix for the synthetic table."""
    import pandas as pd

    return prepare_v20_training_data(pd.read_csv(training_csv, usecols=["composition", "label"]))


def _groups_for(prepared) -> list[str]:
    from nfm_db.ml.group_kfold_cv import build_group_labels

    return build_group_labels(list(prepared.compositions))


def test_locked_splits_are_group_disjoint_and_five_fold(prepared) -> None:
    groups = _groups_for(prepared)
    splits = locked_group_kfold_splits(prepared.X, prepared.y, groups)

    assert len(splits) == REQUIRED_CV_SPLITS
    group_array = np.asarray(groups)
    for train_idx, test_idx in splits:
        assert not set(group_array[train_idx]) & set(group_array[test_idx])


def test_dummy_baseline_reuses_the_locked_splits(prepared) -> None:
    """The block must be paired per-fold with the model's own GroupKFold splits."""
    groups = _groups_for(prepared)
    block = compute_dummy_baseline(prepared.X, prepared.y, groups, model_macro_f1=0.6770)

    expected = cross_val_score(
        DummyClassifier(strategy=DUMMY_BASELINE_STRATEGY),
        prepared.X,
        prepared.y,
        cv=GroupKFold(n_splits=REQUIRED_CV_SPLITS).split(prepared.X, prepared.y, groups=groups),
        scoring="f1_macro",
    )

    assert block["macro_f1_per_fold"] == [round(float(score), 6) for score in expected]
    assert block["macro_f1_mean"] == pytest.approx(float(np.mean(expected)), abs=1e-6)
    assert block["macro_f1_std"] == pytest.approx(float(np.std(expected)), abs=1e-6)
    assert block["n_splits"] == REQUIRED_CV_SPLITS
    assert block["paired_with_model_splits"] is True


def test_dummy_baseline_lift_is_percentage_points(prepared) -> None:
    groups = _groups_for(prepared)
    block = compute_dummy_baseline(prepared.X, prepared.y, groups, model_macro_f1=0.6770)

    assert block["lift_over_dummy_pp"] == pytest.approx(
        round((0.6770 - block["macro_f1_mean"]) * 100, 2)
    )


def test_dummy_baseline_is_deterministic(prepared) -> None:
    groups = _groups_for(prepared)
    first = compute_dummy_baseline(prepared.X, prepared.y, groups, model_macro_f1=0.5)
    second = compute_dummy_baseline(prepared.X, prepared.y, groups, model_macro_f1=0.5)

    assert first == second


def test_gate_passes_through_when_model_beats_dummy() -> None:
    assessment = RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    result = _apply_dummy_baseline_gate(assessment, {"lift_over_dummy_pp": 23.46})

    assert result == assessment


def test_gate_fails_closed_when_model_loses_to_dummy() -> None:
    """A negative lift must downgrade the label and raise the RD-3 flag."""
    assessment = RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    result = _apply_dummy_baseline_gate(assessment, {"lift_over_dummy_pp": -4.2})

    assert result.rd2_label == "[EXPLORATORY]"
    assert result.rd3_triggered is True
    assert "DummyClassifier(most_frequent)" in result.reasons[-1]


def test_gate_treats_a_tie_as_passing() -> None:
    assessment = RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    assert _apply_dummy_baseline_gate(assessment, {"lift_over_dummy_pp": 0.0}) == assessment


def test_training_run_emits_dummy_baseline_in_metrics_json(
    training_csv: Path,
    tmp_path: Path,
) -> None:
    """End-to-end: train_phase_classifier_v20() persists the new key."""
    models_dir = tmp_path / "models"
    metrics = train_phase_classifier_v20(
        training_set_path=training_csv,
        models_dir=models_dir,
        estimator=DecisionTreeClassifier(max_depth=3, random_state=42),
    )

    block = metrics["dummy_baseline"]
    assert {
        "strategy",
        "macro_f1_mean",
        "macro_f1_std",
        "n_splits",
        "lift_over_dummy_pp",
    } <= set(block)
    assert block["strategy"] == "most_frequent"
    assert block["n_splits"] == REQUIRED_CV_SPLITS
    assert block["lift_over_dummy_pp"] == pytest.approx(
        round((metrics["cv_macro_avg_f1"] - block["macro_f1_mean"]) * 100, 2)
    )

    saved = json.loads((models_dir / "phase_classifier_v2.0_metrics.json").read_text())
    assert saved["dummy_baseline"] == block
