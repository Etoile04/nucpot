"""Unit tests for PhaseClassifier v1.0 — NFM-1545.

Tests cover:
- Feature vector construction (8 physical + 4 cluster one-hot = 12)
- Phase labeling from cluster types and physical features
- Data augmentation (composition perturbation)
- Model training on synthetic data
- 5-fold CV accuracy > 75% (Sprint 4 acceptance)
- Each fold > 70% (Sprint 4 acceptance)
- Prediction API (single and batch)
- SHAP feature importance report generation
- Model serialization/deserialization (pickle)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from nfm_db.ml.phase_classifier import (
    PHASE_H,
    PHASE_M,
    PHASE_LABELS,
    ALL_FEATURE_NAMES,
    CLUSTER_TYPE_NAMES,
    CVResult,
    SHAPReport,
    PhaseClassifier,
    TrainingSample,
    augment_training_data,
    build_feature_array,
    build_feature_vector,
    cluster_type_to_one_hot,
    generate_synthetic_training_data,
    label_phase_from_cluster_type,
    label_phase_from_features,
    perturb_composition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trained_classifier():
    """A trained PhaseClassifier on synthetic data."""
    X, y, _, _ = generate_synthetic_training_data(
        n_target=500, augmentation=True, seed=42,
    )
    clf = PhaseClassifier()
    clf.train(X, y, compute_shap=True)
    return clf, X, y


@pytest.fixture(scope="module")
def sample_physical_features():
    """Sample physical features for a U-10Mo composition."""
    return {
        "mo_equivalent": 0.1,
        "pauling_chi_diff": 0.078,
        "allen_chi_diff": 0.086,
        "config_entropy": 0.325,
        "bv_ratio": 12.73,
        "u_density": 17.94,
        "mixing_enthalpy": -0.216,
        "lattice_distortion": 0.023,
    }


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------


class TestFeatureVector:
    """Tests for feature vector construction."""

    def test_cluster_type_one_hot_type_i(self) -> None:
        one_hot = cluster_type_to_one_hot("I")
        assert one_hot == {
            "type_I": 1.0,
            "type_II": 0.0,
            "type_III": 0.0,
            "type_IV": 0.0,
        }

    def test_cluster_type_one_hot_type_iv(self) -> None:
        one_hot = cluster_type_to_one_hot("IV")
        assert one_hot == {
            "type_I": 0.0,
            "type_II": 0.0,
            "type_III": 0.0,
            "type_IV": 1.0,
        }

    def test_cluster_type_one_hot_invalid(self) -> None:
        result = cluster_type_to_one_hot("V")
        # Unknown cluster type → all zeros (no match)
        assert all(v == 0.0 for v in result.values())

    def test_feature_vector_length(self, sample_physical_features) -> None:
        vec = build_feature_vector(sample_physical_features, "II")
        assert len(vec) == 12

    def test_feature_vector_physical_part(self, sample_physical_features) -> None:
        vec = build_feature_vector(sample_physical_features, "II")
        feature_keys = [
            "mo_equivalent", "pauling_chi_diff", "allen_chi_diff",
            "config_entropy", "bv_ratio", "u_density",
            "mixing_enthalpy", "lattice_distortion",
        ]
        for i, fname in enumerate(feature_keys):
            assert abs(vec[i] - sample_physical_features[fname]) < 1e-10

    def test_feature_vector_cluster_part(self, sample_physical_features) -> None:
        vec = build_feature_vector(sample_physical_features, "III")
        assert vec[8] == 0.0   # type_I
        assert vec[9] == 0.0   # type_II
        assert vec[10] == 1.0  # type_III
        assert vec[11] == 0.0  # type_IV

    def test_feature_array_shape(self, sample_physical_features) -> None:
        arr = build_feature_array(sample_physical_features, "I")
        assert arr.shape == (12,)
        assert arr.dtype == np.float64


# ---------------------------------------------------------------------------
# Phase labeling
# ---------------------------------------------------------------------------


class TestPhaseLabeling:
    """Tests for phase label assignment rules."""

    def test_type_i_is_h(self) -> None:
        assert label_phase_from_cluster_type("I") == PHASE_H

    def test_type_ii_is_m(self) -> None:
        assert label_phase_from_cluster_type("II") == PHASE_M

    def test_type_iii_is_h(self) -> None:
        assert label_phase_from_cluster_type("III") == PHASE_H

    def test_type_iv_is_h(self) -> None:
        assert label_phase_from_cluster_type("IV") == PHASE_H

    def test_invalid_cluster_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown cluster type"):
            label_phase_from_cluster_type("X")

    def test_feature_label_high_mo_eq(self) -> None:
        features = {
            "mo_equivalent": 0.5, "mixing_enthalpy": -5.0,
            "config_entropy": 0.2, "pauling_chi_diff": 0.05,
        }
        assert label_phase_from_features(features) == PHASE_M

    def test_feature_label_strongly_exothermic(self) -> None:
        features = {
            "mo_equivalent": 0.1, "mixing_enthalpy": -30.0,
            "config_entropy": 0.3, "pauling_chi_diff": 0.2,
        }
        assert label_phase_from_features(features) == PHASE_H

    def test_feature_label_high_entropy(self) -> None:
        features = {
            "mo_equivalent": 0.2, "mixing_enthalpy": -3.0,
            "config_entropy": 1.0, "pauling_chi_diff": 0.05,
        }
        assert label_phase_from_features(features) == PHASE_M

    def test_feature_label_high_chi_diff(self) -> None:
        features = {
            "mo_equivalent": 0.05, "mixing_enthalpy": -10.0,
            "config_entropy": 0.3, "pauling_chi_diff": 0.2,
        }
        assert label_phase_from_features(features) == PHASE_H


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TestDataModel:
    """Tests for TrainingSample, CVResult, SHAPReport data classes."""

    def test_training_sample_valid(self) -> None:
        sample = TrainingSample(
            features=tuple(0.1 for _ in range(12)),
            label=PHASE_H,
        )
        assert sample.label == PHASE_H

    def test_training_sample_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="Expected 12 features"):
            TrainingSample(
                features=tuple(0.1 for _ in range(8)),
                label=PHASE_H,
            )

    def test_training_sample_invalid_label(self) -> None:
        with pytest.raises(ValueError, match="Invalid label"):
            TrainingSample(
                features=tuple(0.1 for _ in range(12)),
                label="X",
            )

    def test_cv_result(self) -> None:
        result = CVResult(
            mean_accuracy=0.82,
            fold_accuracies=(0.80, 0.84, 0.81, 0.83, 0.82),
            fold_details=(
                (0.80, 0), (0.84, 1), (0.81, 2), (0.83, 3), (0.82, 4),
            ),
            fold_precision=(0.81, 0.83, 0.80, 0.82, 0.81),
            fold_recall=(0.79, 0.85, 0.80, 0.84, 0.83),
            fold_f1=(0.80, 0.84, 0.80, 0.83, 0.82),
            min_fold_accuracy=0.80,
            passed=True,
        )
        assert result.passed is True
        assert result.mean_accuracy == 0.82
        assert len(result.fold_precision) == 5
        assert len(result.fold_recall) == 5
        assert len(result.fold_f1) == 5

    def test_shap_report(self) -> None:
        report = SHAPReport(
            feature_names=tuple(ALL_FEATURE_NAMES),
            mean_abs_shap=tuple(0.01 * i for i in range(12)),
            feature_importance_ranking=(
                ("feature_1", 0.11), ("feature_2", 0.10),
            ),
        )
        assert len(report.feature_names) == 12


# ---------------------------------------------------------------------------
# Data augmentation
# ---------------------------------------------------------------------------


class TestDataAugmentation:
    """Tests for composition perturbation and augmentation."""

    def test_perturb_composition_valid(self) -> None:
        comp = {"U": 0.90, "Mo": 0.10}
        perturbed = perturb_composition(comp, 0.005, seed=0)
        assert perturbed is not None
        assert abs(sum(perturbed.values()) - 1.0) < 1e-10
        assert perturbed["Mo"] > comp["Mo"]

    def test_perturb_composition_negative(self) -> None:
        comp = {"U": 0.90, "Mo": 0.10}
        perturbed = perturb_composition(comp, -0.005, seed=0)
        assert perturbed is not None
        assert perturbed["Mo"] < comp["Mo"]

    def test_perturb_composition_out_of_bounds(self) -> None:
        comp = {"U": 0.96, "Mo": 0.04}
        perturbed = perturb_composition(comp, 1.0, seed=0)
        assert perturbed is None

    def test_perturb_composition_only_uranium(self) -> None:
        comp = {"U": 1.0}
        perturbed = perturb_composition(comp, 0.005, seed=0)
        assert perturbed is None

    def test_augment_training_data(self) -> None:
        compositions = [{"U": 0.90, "Mo": 0.10}]
        cluster_types = ["II"]
        features = [{
            "mo_equivalent": 0.1,
            "pauling_chi_diff": 0.078,
            "allen_chi_diff": 0.086,
            "config_entropy": 0.325,
            "bv_ratio": 12.73,
            "u_density": 17.94,
            "mixing_enthalpy": -0.216,
            "lattice_distortion": 0.023,
        }]

        aug_c, aug_ct, aug_f = augment_training_data(
            compositions, cluster_types, features,
            perturbation_steps=[-0.005, 0.0, 0.005],
        )

        assert len(aug_c) > 1
        assert len(aug_c) == len(aug_ct)
        assert len(aug_c) == len(aug_f)
        assert aug_c[0] == compositions[0]


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------


class TestModelTraining:
    """Tests for model training and evaluation."""

    def test_synthetic_data_generation(self) -> None:
        X, y, comps, cts = generate_synthetic_training_data(
            n_target=200, augmentation=True, seed=42,
        )
        assert X.shape[0] > 200  # augmented
        assert X.shape[1] == 12
        assert y.shape[0] == X.shape[0]
        assert len(comps) == X.shape[0]
        assert len(cts) == X.shape[0]
        assert set(np.unique(y)).issubset({0, 1})

    def test_synthetic_data_both_classes(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=300, augmentation=True, seed=42,
        )
        unique, _ = np.unique(y, return_counts=True)
        assert len(unique) == 2, "Both H and M classes must be present"

    def test_train_sets_trained_flag(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=200, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        assert not clf.is_trained
        clf.train(X, y)
        assert clf.is_trained

    def test_feature_names_property(self) -> None:
        clf = PhaseClassifier()
        assert clf.feature_names == ALL_FEATURE_NAMES
        assert clf.n_features == 12

    def test_repr_untrained(self) -> None:
        clf = PhaseClassifier()
        assert "untrained" in repr(clf)

    def test_repr_trained(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=100, augmentation=False, seed=42,
        )
        clf = PhaseClassifier()
        clf.train(X, y, compute_shap=False)
        assert "trained" in repr(clf)


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


class TestPrediction:
    """Tests for prediction API."""

    def test_predict_untrained_raises(self, sample_physical_features) -> None:
        clf = PhaseClassifier()
        with pytest.raises(RuntimeError, match="not trained"):
            clf.predict(sample_physical_features, "II")

    def test_predict_returns_valid_structure(
        self, trained_classifier, sample_physical_features,
    ) -> None:
        clf = trained_classifier[0]
        result = clf.predict(sample_physical_features, "II")

        assert "phase" in result
        assert result["phase"] in PHASE_LABELS
        assert "probabilities" in result
        assert PHASE_H in result["probabilities"]
        assert PHASE_M in result["probabilities"]
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert abs(
            result["probabilities"][PHASE_H]
            + result["probabilities"][PHASE_M] - 1.0
        ) < 1e-6

    def test_predict_batch_shapes(self, trained_classifier) -> None:
        clf, X, y = trained_classifier
        labels, proba = clf.predict_batch(X)

        assert labels.shape == (X.shape[0],)
        assert proba.shape == (X.shape[0], 2)
        assert all(l in PHASE_LABELS for l in labels)

    def test_predict_batch_untrained_raises(self) -> None:
        clf = PhaseClassifier()
        X = np.random.randn(5, 12)
        with pytest.raises(RuntimeError, match="not trained"):
            clf.predict_batch(X)


# ---------------------------------------------------------------------------
# Cross-validation (Sprint 4 acceptance criteria)
# ---------------------------------------------------------------------------


class TestCrossValidation:
    """Tests for CV evaluation — Sprint 4: >75% mean, each >70%."""

    def test_cv_accuracy_above_75_percent(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert cv_result.mean_accuracy > 0.75, (
            f"Mean CV accuracy {cv_result.mean_accuracy:.4f} "
            f"does not meet 75% target"
        )

    def test_cv_each_fold_above_70_percent(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        for fold_acc, fold_idx in cv_result.fold_details:
            assert fold_acc > 0.70, (
                f"Fold {fold_idx} accuracy {fold_acc:.4f} "
                f"does not meet 70% minimum"
            )

    def test_cv_five_folds(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=300, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert len(cv_result.fold_accuracies) == 5
        assert len(cv_result.fold_details) == 5

    def test_cv_passed_flag(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert cv_result.passed is True, (
            f"CV did not pass: mean={cv_result.mean_accuracy:.4f}, "
            f"min={cv_result.min_fold_accuracy:.4f}"
        )


# ---------------------------------------------------------------------------
# SHAP analysis
# ---------------------------------------------------------------------------


class TestSHAP:
    """Tests for SHAP feature importance."""

    def test_shap_report_generated(self, trained_classifier) -> None:
        clf = trained_classifier[0]
        report = clf.shap_report
        if report is not None:
            assert len(report.feature_names) == 12
            assert len(report.mean_abs_shap) == 12
            assert len(report.feature_importance_ranking) == 12
            values = [v for _, v in report.feature_importance_ranking]
            assert values == sorted(values, reverse=True)

    def test_shap_top_features_are_physical(self, trained_classifier) -> None:
        """Top features should include physical features, not just cluster types."""
        clf = trained_classifier[0]
        report = clf.shap_report
        if report is None:
            pytest.skip("SHAP not available in this environment")

        top_3 = [name for name, _ in report.feature_importance_ranking[:3]]
        physical_in_top = any(f in top_3 for f in [
            "mo_equivalent", "pauling_chi_diff", "allen_chi_diff",
            "config_entropy", "mixing_enthalpy", "lattice_distortion",
        ])
        assert physical_in_top, f"Expected physical features in top 3, got: {top_3}"


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------


class TestFullEvaluation:
    """Tests for the full_evaluation pipeline."""

    def test_full_evaluation_structure(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=300, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        result = clf.full_evaluation(X, y)

        assert "cv_result" in result
        assert "shap_report" in result
        assert "classification_report" in result
        assert "n_samples" in result
        assert "n_features" in result
        assert result["n_features"] == 12

        cv = result["cv_result"]
        assert isinstance(cv, CVResult)
        assert cv.passed is True


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Tests for model save/load via pickle."""

    def test_save_and_load_roundtrip(self, trained_classifier) -> None:
        clf, X, y = trained_classifier

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name

        try:
            clf.save(path)
            loaded = PhaseClassifier.load(path)

            assert loaded.is_trained
            assert loaded.n_features == 12

            labels_orig, proba_orig = clf.predict_batch(X[:10])
            labels_loaded, proba_loaded = loaded.predict_batch(X[:10])

            np.testing.assert_array_equal(labels_orig, labels_loaded)
            np.testing.assert_allclose(proba_orig, proba_loaded, atol=1e-10)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_save_creates_parent_dirs(self, trained_classifier) -> None:
        clf = trained_classifier[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "model" / "phase_v1.pkl"
            clf.save(path)
            assert path.exists()
            loaded = PhaseClassifier.load(path)
            assert loaded.is_trained

    def test_shap_report_survives_serialization(self, trained_classifier) -> None:
        clf = trained_classifier[0]
        if clf.shap_report is None:
            pytest.skip("SHAP report not available")

        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            path = f.name

        try:
            clf.save(path)
            loaded = PhaseClassifier.load(path)
            assert loaded.shap_report is not None
            assert loaded.shap_report == clf.shap_report
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# NFM-1531 acceptance: .joblib artifact format
# ---------------------------------------------------------------------------


class TestJoblibSerialization:
    """NFM-1531 deliverable #2: model artifact must be a .joblib file.

    joblib is preferred over pickle for scikit-learn models carrying
    large numpy arrays (lighter, faster, sklearn-recommended).
    """

    def test_save_creates_valid_joblib_artifact(self, trained_classifier) -> None:
        import joblib

        clf, _, _ = trained_classifier
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = (
                Path(tmpdir) / "phase_classifier_v1.0.0.joblib"
            )
            clf.save(artifact_path)
            assert artifact_path.exists(), "Artifact file not created"
            # Verify the file is loadable with joblib directly
            state = joblib.load(artifact_path)
            assert isinstance(state, dict)
            assert "model" in state, "joblib state missing 'model' key"

    def test_load_roundtrip_via_joblib_path(self, trained_classifier) -> None:
        clf, X, _ = trained_classifier
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = (
                Path(tmpdir) / "phase_classifier_v1.0.0.joblib"
            )
            clf.save(artifact_path)
            loaded = PhaseClassifier.load(artifact_path)
            assert loaded.is_trained

            labels_orig, proba_orig = clf.predict_batch(X[:5])
            labels_loaded, proba_loaded = loaded.predict_batch(X[:5])
            np.testing.assert_array_equal(labels_orig, labels_loaded)
            np.testing.assert_allclose(
                proba_orig, proba_loaded, atol=1e-10,
            )


# ---------------------------------------------------------------------------
# NFM-1531 acceptance: predict_phase(composition) inference interface
# ---------------------------------------------------------------------------


class TestCompositionInference:
    """NFM-1531 deliverable #4: predict_phase(composition) -> phase + probas.

    The composition-level entry point wraps feature computation, cluster
    type inference, and PhaseClassifier.predict behind a single call so
    the FastAPI route can hand it a raw composition dict from the user.
    """

    def test_predict_phase_is_importable(self) -> None:
        from nfm_db.ml.phase_classifier import predict_phase
        assert callable(predict_phase)

    def test_predict_phase_returns_phase_label_and_probabilities(
        self, trained_classifier,
    ) -> None:
        from nfm_db.ml.phase_classifier import predict_phase

        clf, _, _ = trained_classifier
        result = predict_phase(
            {"U": 0.90, "Mo": 0.10}, classifier=clf,
        )

        assert "phase" in result
        assert result["phase"] in PHASE_LABELS
        assert "probabilities" in result
        assert PHASE_H in result["probabilities"]
        assert PHASE_M in result["probabilities"]
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert abs(
            result["probabilities"][PHASE_H]
            + result["probabilities"][PHASE_M] - 1.0
        ) < 1e-6

    def test_predict_phase_handles_ternary(
        self, trained_classifier,
    ) -> None:
        from nfm_db.ml.phase_classifier import predict_phase

        clf, _, _ = trained_classifier
        result = predict_phase(
            {"U": 0.88, "Mo": 0.05, "Zr": 0.07},
            classifier=clf,
        )
        assert result["phase"] in PHASE_LABELS

    def test_predict_phase_raises_for_untrained_classifier(self) -> None:
        from nfm_db.ml.phase_classifier import predict_phase

        untrained = PhaseClassifier()
        with pytest.raises(RuntimeError, match="not trained"):
            predict_phase(
                {"U": 0.90, "Mo": 0.10}, classifier=untrained,
            )


# ---------------------------------------------------------------------------
# NFM-1531 acceptance: per-fold precision / recall / f1
# ---------------------------------------------------------------------------


class TestCrossValidationMetrics:
    """NFM-1531 deliverable #3: per-fold precision/recall/f1 in CV report."""

    def test_cv_result_has_per_fold_precision(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert hasattr(cv_result, "fold_precision")
        assert len(cv_result.fold_precision) == 5
        for p in cv_result.fold_precision:
            assert 0.0 <= p <= 1.0

    def test_cv_result_has_per_fold_recall(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert hasattr(cv_result, "fold_recall")
        assert len(cv_result.fold_recall) == 5
        for r in cv_result.fold_recall:
            assert 0.0 <= r <= 1.0

    def test_cv_result_has_per_fold_f1(self) -> None:
        X, y, _, _ = generate_synthetic_training_data(
            n_target=500, augmentation=True, seed=42,
        )
        clf = PhaseClassifier()
        cv_result = clf.cross_validate(X, y)

        assert hasattr(cv_result, "fold_f1")
        assert len(cv_result.fold_f1) == 5
        for f1 in cv_result.fold_f1:
            assert 0.0 <= f1 <= 1.0


# ---------------------------------------------------------------------------
# NFM-1531 acceptance: train_phase_classifier.py script
# ---------------------------------------------------------------------------


class TestTrainingScript:
    """NFM-1531 deliverable #1: train_phase_classifier.py CLI script.

    The script must exist, accept --output-dir, and produce:
      - phase_classifier_v1.0.0.joblib artifact
      - phase_classifier_v1.0.0_report.txt with per-fold metrics
    """

    def test_training_script_exists(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        script = (
            repo_root
            / "apps"
            / "api"
            / "src"
            / "nfm_db"
            / "ml"
            / "train_phase_classifier.py"
        )
        assert script.exists(), f"Training script not found: {script}"

    def test_training_script_produces_artifact_and_report(self) -> None:
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        script = (
            repo_root
            / "apps"
            / "api"
            / "src"
            / "nfm_db"
            / "ml"
            / "train_phase_classifier.py"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--output-dir",
                    str(out),
                ],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=600,
            )
            assert result.returncode == 0, (
                f"Training script failed:\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
            artifact = out / "phase_classifier_v1.0.0.joblib"
            report = out / "phase_classifier_v1.0.0_report.txt"
            assert artifact.exists(), (
                f"Artifact not produced: {artifact}"
            )
            assert report.exists(), f"Report not produced: {report}"
            report_text = report.read_text()
            for keyword in ("accuracy", "precision", "recall", "f1"):
                assert keyword in report_text.lower(), (
                    f"Report missing '{keyword}' metric: {report_text}"
                )
