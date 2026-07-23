"""Leakage guards for the PhaseClassifier v2.0 physical feature schema."""

from __future__ import annotations

import importlib
import inspect
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier, VotingClassifier

from nfm_db.ml.feature_engineering import (
    calculate_allen_chi_diff,
    calculate_bv_ratio,
    calculate_config_entropy,
    calculate_lattice_distortion,
    calculate_mixing_enthalpy,
    calculate_mo_equivalent,
    calculate_u_density,
    calculate_vec,
)

EXPECTED_FEATURE_NAMES = (
    "mo_equivalent",
    "allen_chi_diff",
    "config_entropy",
    "bv_ratio",
    "u_density",
    "mixing_enthalpy",
    "lattice_distortion",
    "vec",
)


def _v20_module():
    return importlib.import_module("nfm_db.ml.train_v20")


def test_v20_feature_schema_is_exactly_locked_physical_8d() -> None:
    module = _v20_module()

    assert module.PHASE_CLASSIFIER_V2_FEATURE_NAMES == EXPECTED_FEATURE_NAMES
    assert not any("cluster" in name or "type_" in name for name in EXPECTED_FEATURE_NAMES)


def test_compute_v20_feature_vector_matches_locked_calculators() -> None:
    module = _v20_module()
    composition = {"U": 90.0, "Mo": 7.0, "Nb": 3.0}

    vector = module.compute_v20_feature_vector(composition)

    expected = np.array(
        [
            calculate_mo_equivalent(composition),
            calculate_allen_chi_diff(composition),
            calculate_config_entropy(composition),
            calculate_bv_ratio(composition),
            calculate_u_density(composition),
            calculate_mixing_enthalpy(composition),
            calculate_lattice_distortion(composition),
            calculate_vec(composition),
        ],
        dtype=np.float64,
    )
    np.testing.assert_allclose(vector, expected)
    assert vector.shape == (8,)


def test_compute_v20_feature_vector_has_no_phase_or_cluster_input() -> None:
    module = _v20_module()
    signature = inspect.signature(module.compute_v20_feature_vector)

    assert tuple(signature.parameters) == ("composition",)


def test_compute_v20_feature_vector_is_basis_invariant() -> None:
    module = _v20_module()

    fraction_vector = module.compute_v20_feature_vector({"U": 0.9, "Mo": 0.1})
    percent_vector = module.compute_v20_feature_vector({"U": 90.0, "Mo": 10.0})

    np.testing.assert_allclose(fraction_vector, percent_vector)


def test_canonicalize_composition_is_basis_invariant_and_immutable() -> None:
    module = _v20_module()
    original = {"U": 0.9, "Mo": 0.1, "Nb": 0.0}

    canonical = module.canonicalize_composition(original)

    assert canonical == {"Mo": 10.0, "U": 90.0}
    assert original == {"U": 0.9, "Mo": 0.1, "Nb": 0.0}
    assert module.canonicalize_composition(canonical) == canonical


def test_prepare_v20_training_data_uses_only_composition_and_label() -> None:
    module = _v20_module()
    records = pd.DataFrame(
        [
            {"composition": '{"U": 90, "Mo": 10}', "label": "H", "phase": "type_I"},
            {"composition": '{"U": 0.9, "Mo": 0.1}', "label": "H", "phase": "type_IV"},
            {"composition": '{"U": 90, "Nb": 10}', "label": "M", "phase": "type_II"},
        ]
    )
    changed_phase = records.assign(phase=["type_IV", "type_I", "type_III"])

    prepared = module.prepare_v20_training_data(records)
    prepared_changed_phase = module.prepare_v20_training_data(changed_phase)

    assert prepared.X.shape == (2, 8)
    assert prepared.y.tolist() == [0, 1]
    assert prepared.deduplicated_count == 1
    np.testing.assert_allclose(prepared.X, prepared_changed_phase.X)
    np.testing.assert_array_equal(prepared.y, prepared_changed_phase.y)


def test_prepare_v20_training_data_rejects_conflicting_duplicate_labels() -> None:
    module = _v20_module()
    records = pd.DataFrame(
        [
            {"composition": '{"U": 90, "Mo": 10}', "label": "H"},
            {"composition": '{"U": 0.9, "Mo": 0.1}', "label": "M"},
        ]
    )

    with pytest.raises(ValueError, match="conflicting labels"):
        module.prepare_v20_training_data(records)


def _grouped_prepared_data(module, n_groups: int = 5):
    element_systems = ["Mo", "Nb", "Ti", "V", "Cr"][:n_groups]
    compositions = tuple(
        {"U": 90.0, element: 10.0} for element in element_systems for _ in range(2)
    )
    rng = np.random.default_rng(42)
    return module.PreparedPhaseData(
        X=rng.normal(size=(len(compositions), 8)),
        y=np.tile(np.array([0, 1], dtype=np.int64), n_groups),
        compositions=compositions,
        deduplicated_count=0,
    )


def test_run_v20_grouped_cv_uses_exactly_five_element_system_folds() -> None:
    module = _v20_module()
    prepared = _grouped_prepared_data(module)
    estimator = RandomForestClassifier(n_estimators=5, random_state=42)

    result = module.run_v20_grouped_cv(prepared, estimator)

    assert result.cv_strategy == "groupkf"
    assert result.n_splits == 5
    assert result.n_groups == 5
    assert result.n_features == 8


def test_run_v20_grouped_cv_fails_closed_with_fewer_than_five_groups() -> None:
    module = _v20_module()
    prepared = _grouped_prepared_data(module, n_groups=4)
    estimator = RandomForestClassifier(n_estimators=5, random_state=42)

    with pytest.raises(ValueError, match="at least 5 distinct element systems"):
        module.run_v20_grouped_cv(prepared, estimator)


@pytest.mark.parametrize(
    ("mean_accuracy", "std_accuracy", "max_fold_accuracy", "expected_label", "rd3"),
    [
        (0.72, 0.04, 0.80, "[CONFIRMED]", False),
        (0.90, 0.04, 0.94, "[EXPLORATORY]", False),
        (0.72, 0.09, 0.84, "[EXPLORATORY]", True),
        (0.96, 0.02, 0.98, "[EXPLORATORY]", True),
    ],
)
def test_assess_v20_cv_applies_preregistered_rd2_gates(
    mean_accuracy: float,
    std_accuracy: float,
    max_fold_accuracy: float,
    expected_label: str,
    rd3: bool,
) -> None:
    module = _v20_module()

    assessment = module.assess_v20_cv(
        mean_accuracy=mean_accuracy,
        std_accuracy=std_accuracy,
        max_fold_accuracy=max_fold_accuracy,
    )

    assert assessment.rd2_label == expected_label
    assert assessment.rd3_triggered is rd3


def test_build_v20_ensemble_preserves_preregistered_architecture() -> None:
    module = _v20_module()

    ensemble = module.build_v20_ensemble()

    assert isinstance(ensemble, VotingClassifier)
    assert ensemble.voting == "soft"
    assert [name for name, _ in ensemble.estimators] == ["rf", "xgb"]
    assert all(estimator.get_params()["random_state"] == 42 for _, estimator in ensemble.estimators)


def _write_synthetic_training_set(path: Path) -> None:
    rows = []
    for element in ["Mo", "Nb", "Ti", "V", "Cr"]:
        rows.extend(
            [
                {
                    "composition": json.dumps({"U": 95.0, element: 5.0}),
                    "label": "H",
                    "phase": "must_not_be_read",
                },
                {
                    "composition": json.dumps({"U": 85.0, element: 15.0}),
                    "label": "M",
                    "phase": "must_not_be_read",
                },
            ]
        )
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.mark.filterwarnings(
    "ignore:Setting the shape on a NumPy array has been deprecated:DeprecationWarning"
)
def test_train_phase_classifier_v20_writes_reproducible_artifacts(tmp_path: Path) -> None:
    module = _v20_module()
    training_path = tmp_path / "training.csv"
    _write_synthetic_training_set(training_path)
    estimator = RandomForestClassifier(n_estimators=5, random_state=42)

    metrics = module.train_phase_classifier_v20(
        training_set_path=training_path,
        models_dir=tmp_path,
        estimator=estimator,
    )

    model_path = tmp_path / "phase_classifier_v2.0.joblib"
    metrics_path = tmp_path / "phase_classifier_v2.0_metrics.json"
    assert model_path.exists()
    assert metrics_path.exists()
    assert metrics["version"] == "v2.0"
    assert metrics["n_features"] == 8
    assert metrics["cv_strategy"] == "groupkf"
    assert metrics["cv_n_splits"] == 5
    assert metrics["cv_n_groups"] == 5
    assert metrics["group_min_size"] == 2
    assert metrics["seed"] == 42
    assert metrics["rd2_label"] in {"[CONFIRMED]", "[EXPLORATORY]"}
    assert len(metrics["data_sha256"]) == 64
    assert len(metrics["dependency_lock_sha256"]) == 64
    assert len(metrics["schema_sha256"]) == 64

    persisted_metrics = json.loads(metrics_path.read_text())
    assert persisted_metrics == metrics

    import joblib

    artifact = joblib.load(model_path)
    assert artifact["version"] == "v2.0"
    assert tuple(artifact["feature_names"]) == EXPECTED_FEATURE_NAMES
    assert artifact["schema_sha256"] == metrics["schema_sha256"]


def test_train_phase_classifier_v20_refuses_to_overwrite_artifacts(tmp_path: Path) -> None:
    module = _v20_module()
    training_path = tmp_path / "training.csv"
    _write_synthetic_training_set(training_path)
    (tmp_path / "phase_classifier_v2.0.joblib").write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="already exists"):
        module.train_phase_classifier_v20(
            training_set_path=training_path,
            models_dir=tmp_path,
            estimator=RandomForestClassifier(n_estimators=5, random_state=42),
        )


def test_git_commit_sha_returns_real_hex_when_git_available(tmp_path: Path) -> None:
    module = _v20_module()

    sha = module._git_commit_sha()

    if shutil.which("git") is None:
        pytest.skip("git not available in environment")
    assert not sha.startswith("no-git:"), f"expected real SHA but got sentinel: {sha!r}"
    assert len(sha) == 40, f"expected 40-char hex SHA, got {sha!r}"
    int(sha, 16)


def test_apply_importance_gate_escalates_rd3_when_mixing_enthalpy_dominates() -> None:
    module = _v20_module()
    base = module.RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    triggered = module._apply_importance_gate(
        base,
        {
            "mixing_enthalpy": 0.50,
            "allen_chi_diff": 0.20,
            "mo_equivalent": 0.10,
        },
    )

    assert triggered.rd2_label == "[EXPLORATORY]"
    assert triggered.rd3_triggered is True
    assert any("mixing_enthalpy" in reason for reason in triggered.reasons)


def test_apply_importance_gate_does_not_escalate_when_below_two_x_threshold() -> None:
    module = _v20_module()
    base = module.RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    unchanged = module._apply_importance_gate(
        base,
        {
            "mixing_enthalpy": 0.40,
            "allen_chi_diff": 0.25,
            "mo_equivalent": 0.20,
        },
    )

    assert unchanged == base


def test_apply_importance_gate_boundary_two_x_inclusive() -> None:
    module = _v20_module()
    base = module.RD2Assessment(rd2_label="[CONFIRMED]", rd3_triggered=False, reasons=())

    boundary = module._apply_importance_gate(
        base,
        {
            "mixing_enthalpy": 0.40,
            "allen_chi_diff": 0.20,
            "mo_equivalent": 0.10,
        },
    )

    assert boundary.rd2_label == "[EXPLORATORY]"
    assert boundary.rd3_triggered is True
    assert boundary.reasons == ("mixing_enthalpy importance exceeded 2x the next feature",)
