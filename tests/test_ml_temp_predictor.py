"""Unit tests for TempPredictor v1.0 — NFM-1532.

Tests cover:
- Feature vector construction (8 physical + 4 cluster one-hot = 12)
- Cluster type inference from physical features
- Model training on the 55-sample experimental design matrix
- LOO-CV evaluation (Sprint 4 acceptance: mean MAE < 40°C)
- Prediction API (single composition and batch)
- Confidence interval symmetry and floor-clamping
- Serialization/deserialization via joblib round-trip
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from nfm_db.ml.temp_predictor import (
    TARGET_MAE_C,
    RegressionFoldResult,
    RegressionReport,
    TempPrediction,
    TempPredictor,
    build_experimental_design_matrix,
    build_temp_feature_vector,
    cluster_type_from_features,
    format_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trained_predictor() -> TempPredictor:
    """A TempPredictor that has been trained and evaluated on the full set."""
    predictor = TempPredictor()
    X, y = build_experimental_design_matrix()
    predictor.train_and_evaluate(X=X, y=y)
    return predictor


@pytest.fixture(scope="module")
def design_matrix() -> tuple[np.ndarray, np.ndarray]:
    """The (X, y) experimental design matrix."""
    return build_experimental_design_matrix()


@pytest.fixture
def u10mo_features() -> dict:
    """Physical features for a U-10Mo composition."""
    from nfm_db.ml.feature_engineering import compute_all_features
    return compute_all_features({"U": 0.90, "Mo": 0.10})


# ---------------------------------------------------------------------------
# Feature vector construction
# ---------------------------------------------------------------------------


class TestFeatureVector:
    """Tests for feature vector construction."""

    def test_feature_vector_shape(self, u10mo_features) -> None:
        vec = build_temp_feature_vector(u10mo_features, "II")
        assert vec.shape == (12,)
        assert vec.dtype == np.float64

    def test_feature_vector_physical_part(self, u10mo_features) -> None:
        vec = build_temp_feature_vector(u10mo_features, "I")
        from nfm_db.ml.phase_classifier import PHYSICAL_FEATURE_NAMES
        for i, name in enumerate(PHYSICAL_FEATURE_NAMES):
            assert abs(float(vec[i]) - float(u10mo_features[name])) < 1e-10

    def test_feature_vector_cluster_part(self, u10mo_features) -> None:
        vec = build_temp_feature_vector(u10mo_features, "IV")
        assert vec[8] == 0.0   # type_I
        assert vec[9] == 0.0   # type_II
        assert vec[10] == 0.0  # type_III
        assert vec[11] == 1.0  # type_IV


# ---------------------------------------------------------------------------
# Cluster type inference
# ---------------------------------------------------------------------------


class TestClusterTypeInference:
    """Tests for heuristic cluster-type inference from physical features."""

    def test_strongly_exothermic_is_type_i(self) -> None:
        features = {"mixing_enthalpy": -10.0, "pauling_chi_diff": 0.20}
        assert cluster_type_from_features(features) == "I"

    def test_moderate_is_type_ii(self) -> None:
        features = {"mixing_enthalpy": -1.0, "pauling_chi_diff": 0.05}
        assert cluster_type_from_features(features) == "II"

    def test_mild_endothermic_is_type_iii(self) -> None:
        features = {"mixing_enthalpy": 10.0, "pauling_chi_diff": 0.05}
        assert cluster_type_from_features(features) == "III"

    def test_strong_endothermic_is_type_iv(self) -> None:
        features = {"mixing_enthalpy": 30.0, "pauling_chi_diff": 0.20}
        assert cluster_type_from_features(features) == "IV"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class TestDataModel:
    """Tests for dataclass invariants."""

    def test_regression_fold_result(self) -> None:
        fold = RegressionFoldResult(
            fold_index=0,
            true_temp_c=668.0,
            predicted_temp_c=644.25,
            gpr_predicted_temp_c=640.0,
            svr_predicted_temp_c=648.5,
            gpr_std_c=9.49,
            absolute_error_c=23.75,
        )
        assert fold.fold_index == 0
        assert fold.absolute_error_c == 23.75

    def test_regression_report_acceptance_flag(self) -> None:
        report = RegressionReport(
            mean_mae_c=19.4,
            rmse_c=24.0,
            r2=0.74,
            max_abs_error_c=49.8,
            min_abs_error_c=0.2,
            fold_results=(),
            passed_acceptance=True,
        )
        assert report.passed_acceptance is True

    def test_temp_prediction_ci_ordering(self) -> None:
        pred = TempPrediction(
            composition={"U": 0.9, "Mo": 0.1},
            predicted_temp_c=600.0,
            confidence_lower_c=580.0,
            confidence_upper_c=620.0,
            gpr_predicted_temp_c=595.0,
            svr_predicted_temp_c=605.0,
            gpr_std_c=10.2,
            features={},
        )
        assert pred.confidence_lower_c < pred.predicted_temp_c
        assert pred.predicted_temp_c < pred.confidence_upper_c


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


class TestTraining:
    """Tests for model fit / training."""

    def test_untrained_predictor_repr(self) -> None:
        predictor = TempPredictor()
        assert "untrained" in repr(predictor)
        assert not predictor.is_trained

    def test_fit_sets_trained_flag(
        self, design_matrix: tuple[np.ndarray, np.ndarray],
    ) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        predictor.fit(X, y)
        assert predictor.is_trained

    def test_n_features_constant(self) -> None:
        assert TempPredictor().n_features == 12

    def test_fit_rejects_wrong_shape(self) -> None:
        predictor = TempPredictor()
        bad_X = np.random.randn(5, 8)
        y = np.array([600.0] * 5)
        with pytest.raises(ValueError, match="shape"):
            predictor.fit(bad_X, y)

    def test_fit_rejects_length_mismatch(self) -> None:
        predictor = TempPredictor()
        X = np.random.randn(5, 12)
        y = np.array([600.0] * 4)
        with pytest.raises(ValueError, match="length"):
            predictor.fit(X, y)

    def test_design_matrix_shape(self, design_matrix) -> None:
        X, y = design_matrix
        assert X.ndim == 2
        assert X.shape[1] == 12
        assert y.shape[0] == X.shape[0]
        assert y.dtype == np.float64
        assert np.all(y > 0)


# ---------------------------------------------------------------------------
# LOO-CV (Sprint 4 acceptance)
# ---------------------------------------------------------------------------


class TestLOOCrossValidation:
    """LOO-CV evaluation — Sprint 4 acceptance: mean MAE < 40°C."""

    def test_loo_mae_below_target(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        assert report.mean_mae_c < TARGET_MAE_C, (
            f"Mean LOO-CV MAE {report.mean_mae_c:.2f}°C exceeds "
            f"target {TARGET_MAE_C}°C"
        )

    def test_loo_per_fold_count(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        assert len(report.fold_results) == X.shape[0]

    def test_loo_per_fold_metrics_non_negative(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        for fold in report.fold_results:
            assert fold.absolute_error_c >= 0.0
            assert fold.gpr_std_c > 0.0

    def test_loo_passed_acceptance_flag(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        assert report.passed_acceptance is True

    def test_loo_report_stored_on_predictor(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        assert predictor.loo_report is report

    def test_format_report_contains_summary(self, design_matrix) -> None:
        X, y = design_matrix
        predictor = TempPredictor()
        report = predictor.train_and_evaluate(X=X, y=y)
        text = format_report(report)
        assert "TempPredictor v1.0" in text
        assert "Mean MAE" in text
        assert "PASS" in text
        assert "fold=" in text


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


class TestInference:
    """Tests for prediction API."""

    def test_predict_untrained_raises(self) -> None:
        predictor = TempPredictor()
        with pytest.raises(RuntimeError, match="not trained"):
            predictor.predict_phase_transition_temp({"U": 0.9, "Mo": 0.1})

    def test_predict_returns_temp_prediction(
        self, trained_predictor: TempPredictor,
    ) -> None:
        result = trained_predictor.predict_phase_transition_temp(
            {"U": 0.90, "Mo": 0.10},
        )
        assert isinstance(result, TempPrediction)
        assert 400.0 < result.predicted_temp_c < 800.0

    def test_predict_ci_encloses_point(
        self, trained_predictor: TempPredictor,
    ) -> None:
        result = trained_predictor.predict_phase_transition_temp(
            {"U": 0.95, "Nb": 0.05},
        )
        assert result.confidence_lower_c <= result.predicted_temp_c
        assert result.predicted_temp_c <= result.confidence_upper_c

    def test_predict_ci_half_width_at_least_floor(
        self, trained_predictor: TempPredictor,
    ) -> None:
        result = trained_predictor.predict_phase_transition_temp(
            {"U": 0.98, "Mo": 0.02},
        )
        half_width = (
            result.confidence_upper_c - result.confidence_lower_c
        ) / 2.0
        assert half_width >= 15.0  # _MIN_CONFIDENCE_HALF_WIDTH_C

    def test_predict_gpr_svr_both_populated(
        self, trained_predictor: TempPredictor,
    ) -> None:
        result = trained_predictor.predict_phase_transition_temp(
            {"U": 0.88, "Mo": 0.12},
        )
        assert result.gpr_predicted_temp_c != 0.0
        assert result.svr_predicted_temp_c != 0.0

    def test_predict_with_explicit_cluster_type(
        self, trained_predictor: TempPredictor,
    ) -> None:
        result = trained_predictor.predict_phase_transition_temp(
            {"U": 0.88, "Mo": 0.12}, cluster_type="I",
        )
        assert result.predicted_temp_c > 0.0

    def test_predict_does_not_mutate_input(
        self, trained_predictor: TempPredictor,
    ) -> None:
        comp = {"U": 0.90, "Mo": 0.10}
        snapshot = dict(comp)
        trained_predictor.predict_phase_transition_temp(comp)
        assert comp == snapshot

    def test_predict_batch(
        self, trained_predictor: TempPredictor,
    ) -> None:
        comps = [
            {"U": 0.95, "Mo": 0.05},
            {"U": 0.90, "Nb": 0.10},
            {"U": 1.0},
        ]
        results = trained_predictor.predict_batch(comps)
        assert len(results) == len(comps)
        for r in results:
            assert isinstance(r, TempPrediction)
            assert r.confidence_lower_c < r.predicted_temp_c < r.confidence_upper_c

    def test_predict_batch_untrained_raises(self) -> None:
        predictor = TempPredictor()
        with pytest.raises(RuntimeError, match="not trained"):
            predictor.predict_batch([{"U": 1.0}])


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Tests for joblib save/load round-trip."""

    def test_save_and_load_roundtrip(
        self, trained_predictor: TempPredictor,
    ) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".joblib", delete=False,
        ) as f:
            path = f.name
        try:
            trained_predictor.save(path)
            loaded = TempPredictor.load(path)

            assert loaded.is_trained
            assert loaded.n_features == 12

            comp = {"U": 0.90, "Mo": 0.10}
            r_orig = trained_predictor.predict_phase_transition_temp(comp)
            r_loaded = loaded.predict_phase_transition_temp(comp)

            assert abs(
                r_orig.predicted_temp_c - r_loaded.predicted_temp_c,
            ) < 1e-9
            assert abs(
                r_orig.confidence_lower_c - r_loaded.confidence_lower_c,
            ) < 1e-9
            assert abs(
                r_orig.confidence_upper_c - r_loaded.confidence_upper_c,
            ) < 1e-9
        finally:
            Path(path).unlink(missing_ok=True)

    def test_save_creates_parent_dirs(
        self, trained_predictor: TempPredictor,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "deep" / "model.joblib"
            trained_predictor.save(path)
            assert path.exists()
            loaded = TempPredictor.load(path)
            assert loaded.is_trained

    def test_loo_report_survives_serialization(
        self, trained_predictor: TempPredictor,
    ) -> None:
        with tempfile.NamedTemporaryFile(
            suffix=".joblib", delete=False,
        ) as f:
            path = f.name
        try:
            trained_predictor.save(path)
            loaded = TempPredictor.load(path)
            assert loaded.loo_report is not None
            assert (
                loaded.loo_report.mean_mae_c
                == trained_predictor.loo_report.mean_mae_c
            )
            assert (
                loaded.loo_report.passed_acceptance
                == trained_predictor.loo_report.passed_acceptance
            )
        finally:
            Path(path).unlink(missing_ok=True)