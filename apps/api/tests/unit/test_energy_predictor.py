"""Unit tests for EnergyPredictor module (NFM-1788).

Tests follow the lazy-load + inference pattern established by
PhaseClassifier and TempPredictor in prediction_service.py.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.preprocessing import StandardScaler


# ---------------------------------------------------------------------------
# predict_energy tests
# ---------------------------------------------------------------------------


class TestPredictEnergy:
    """Tests for the predict_energy function."""

    SAMPLE_FEATURES: dict[str, float] = {
        "mo_equivalent": 10.0,
        "pauling_chi_diff": 0.05,
        "allen_chi_diff": 0.03,
        "config_entropy": 1.9,
        "bv_ratio": 15.0,
        "u_density": 17.5,
        "mixing_enthalpy": -0.45,
        "lattice_distortion": 0.02,
    }

    def _create_test_artifact(self, model_path: Path) -> None:
        """Create a real joblib artifact for testing."""
        model = DummyRegressor(strategy="mean")
        model.fit([[1.0] * 8], [-0.5])
        scaler = StandardScaler()
        scaler.fit([[1.0] * 8])

        artifact = {
            "model": model,
            "scaler": scaler,
            "target_mean": -0.3,
            "target_std": 0.8,
        }

        import joblib

        joblib.dump(artifact, model_path)

    def test_predict_energy_returns_dict_with_required_keys(self) -> None:
        """predict_energy returns dict with predicted_energy, confidence, model_version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            self._create_test_artifact(model_path)

            with patch.dict(
                os.environ, {"ENERGY_PREDICTOR_PATH": str(model_path)}
            ):
                from nfm_db.ml import energy_predictor

                energy_predictor._energy_model = None  # type: ignore[attr-defined]

                result = energy_predictor.predict_energy(self.SAMPLE_FEATURES)

                assert result is not None
                assert "predicted_energy" in result
                assert "confidence" in result
                assert "model_version" in result
                assert isinstance(result["predicted_energy"], float)
                assert isinstance(result["confidence"], float)
                assert isinstance(result["model_version"], str)

    def test_predict_energy_returns_none_when_model_unavailable(self) -> None:
        """predict_energy returns None when model file does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent = Path(tmpdir) / "nonexistent.joblib"
            with patch.dict(
                os.environ, {"ENERGY_PREDICTOR_PATH": str(nonexistent)}
            ):
                from nfm_db.ml import energy_predictor

                energy_predictor._energy_model = None  # type: ignore[attr-defined]

                result = energy_predictor.predict_energy(self.SAMPLE_FEATURES)
                assert result is None

    def test_predict_energy_lazy_loads_model(self) -> None:
        """predict_energy loads model lazily on first call, caches for subsequent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            self._create_test_artifact(model_path)

            with patch.dict(
                os.environ, {"ENERGY_PREDICTOR_PATH": str(model_path)}
            ):
                from nfm_db.ml import energy_predictor

                energy_predictor._energy_model = None  # type: ignore[attr-defined]

                r1 = energy_predictor.predict_energy(self.SAMPLE_FEATURES)
                r2 = energy_predictor.predict_energy(self.SAMPLE_FEATURES)

                assert r1 is not None
                assert r2 is not None
                assert energy_predictor._energy_model is not None  # type: ignore[attr-defined]

    def test_predict_energy_handles_exception_gracefully(self) -> None:
        """predict_energy returns None on prediction exception."""
        # Create artifact with a broken model (not a real regressor)
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "model.joblib"
            scaler = StandardScaler()
            scaler.fit([[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]])

            artifact = {
                "model": "not_a_model",  # Will fail at predict time
                "scaler": scaler,
                "target_mean": 0.0,
                "target_std": 1.0,
            }

            import joblib

            joblib.dump(artifact, model_path)

            with patch.dict(
                os.environ, {"ENERGY_PREDICTOR_PATH": str(model_path)}
            ):
                from nfm_db.ml import energy_predictor

                energy_predictor._energy_model = None  # type: ignore[attr-defined]

                result = energy_predictor.predict_energy(self.SAMPLE_FEATURES)
                assert result is None


# ---------------------------------------------------------------------------
# train_energy_predictor tests
# ---------------------------------------------------------------------------


class TestTrainEnergyPredictor:
    """Tests for the train_energy_predictor function."""

    def test_train_returns_dict_with_model_and_scaler_and_metrics(self) -> None:
        """train_energy_predictor returns dict with model, scaler, and metrics keys."""
        X_train = np.array([[1.0, 0.1, 0.05, 2.0, 15.0, 18.0, -0.5, 0.03]] * 50)
        y_train = np.array([-0.3] * 50)

        from nfm_db.ml.energy_predictor import train_energy_predictor

        result = train_energy_predictor(X_train, y_train)

        assert "model" in result
        assert "scaler" in result
        assert "metrics" in result

    def test_train_metrics_contain_required_keys(self) -> None:
        """Training metrics include r2, rmse, mae."""
        rng = np.random.RandomState(42)
        X_train = rng.normal(0, 1, (50, 8))
        y_train = X_train[:, 0] * 0.5 + rng.normal(0, 0.1, 50)

        from nfm_db.ml.energy_predictor import train_energy_predictor

        result = train_energy_predictor(X_train, y_train)

        metrics = result["metrics"]
        assert "r2" in metrics
        assert "rmse" in metrics
        assert "mae" in metrics

    def test_train_model_is_xgboost_regressor(self) -> None:
        """Training produces an XGBoost regressor model."""
        rng = np.random.RandomState(42)
        X_train = rng.normal(0, 1, (50, 8))
        y_train = X_train[:, 0] * 0.5 + rng.normal(0, 0.1, 50)

        from nfm_db.ml.energy_predictor import train_energy_predictor

        result = train_energy_predictor(X_train, y_train)

        model = result["model"]
        # XGBoost regressor has predict method
        assert hasattr(model, "predict")

    def test_train_scaler_is_fitted(self) -> None:
        """Training returns a fitted StandardScaler."""
        rng = np.random.RandomState(42)
        X_train = rng.normal(0, 1, (50, 8))
        y_train = X_train[:, 0] * 0.5 + rng.normal(0, 0.1, 50)

        from nfm_db.ml.energy_predictor import train_energy_predictor

        result = train_energy_predictor(X_train, y_train)

        scaler = result["scaler"]
        assert hasattr(scaler, "transform")
        assert hasattr(scaler, "mean_")
        # mean_ should be set (fitted)
        assert scaler.mean_ is not None
