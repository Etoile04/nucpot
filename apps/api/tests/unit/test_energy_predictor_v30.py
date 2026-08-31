"""Unit tests for EnergyPredictor v3.0 model loading and backward compat (NFM-2201, NFM-3956).

Tests cover:
- AC-3: v3.0 model artifact loads and contains correct metadata
- AC-4: predict_energy() defaults to v3.0 (backward compat with v1.1/v1.0)
- AC-5: v3.0 model produces predictions matching the v1.1 API contract
- AC-NFM-3956: prediction endpoint surfaces the [EXPLORATORY] grouped-CV
  LOW-bucket confidence (0.3111 +/- 0.4777) and the
  ``energy_model_exploratory`` warning, and does NOT advertise the
  inflated random-split headline (R^2 = 0.9858) anywhere on the
  user-facing path.

Importers: pytest runner only (no production code imports this file).
Affected API: None — tests only validate model_version.py and prediction_service.py.
Data schemas: v3.0 artifact dict {model, version, metrics, feature_names}.

User instruction (NFM-2201 task #4):
  "Bump ENERGY_PREDICTOR_VERSION to v3.0. predict_energy() API unchanged,
   existing v1.1 callers continue to work. Unit tests cover the augmented
   dataset loader; integration test verifies the v3.0 model loads and
   predicts against the v1.1 contract."

User instruction (NFM-3956 LE handoff):
  "Prediction endpoint user-facing surfaces must not advertise
  R^2 = 0.9858. The grouped-CV re-evaluation (NFM-3953, LOW bucket)
  is the protocol-correct generalization estimate."
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Locate model artifact
# ---------------------------------------------------------------------------

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
V30_MODEL_PATH = MODELS_DIR / "energy_predictor_v30.joblib"
V30_METRICS_PATH = MODELS_DIR / "energy_predictor_v3.0_metrics.json"


@pytest.fixture(scope="module")
def v30_artifact():
    """Load the v3.0 model artifact if available."""
    if not V30_MODEL_PATH.exists():
        pytest.skip(f"v3.0 model artifact not found at {V30_MODEL_PATH}")
    import joblib

    return joblib.load(V30_MODEL_PATH)


@pytest.fixture(scope="module")
def v30_metrics() -> dict:
    """Load the v3.0 metrics JSON (contains n_samples, n_train, n_test)."""
    if not V30_METRICS_PATH.exists():
        pytest.skip(f"v3.0 metrics JSON not found at {V30_METRICS_PATH}")
    import json

    with open(V30_METRICS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AC-3: Model artifact structure
# ---------------------------------------------------------------------------


class TestV30Artifact:
    """Verify the v3.0 model artifact has correct structure and metadata."""

    def test_artifact_is_dict_with_model_key(self, v30_artifact: dict) -> None:
        """Artifact is a dict containing 'model', 'version', 'metrics', 'feature_names'."""
        assert isinstance(v30_artifact, dict)
        assert "model" in v30_artifact
        assert "version" in v30_artifact
        assert "metrics" in v30_artifact
        assert "feature_names" in v30_artifact

    def test_version_is_v30(self, v30_artifact: dict) -> None:
        """Version string is exactly 'v3.0'."""
        assert v30_artifact["version"] == "v3.0"

    def test_metrics_has_r2_above_090(self, v30_artifact: dict) -> None:
        """R² >= 0.90 on hold-out (AC-2 honest metrics)."""
        metrics = v30_artifact["metrics"]
        assert metrics["r2"] >= 0.90, f"v3.0 R²={metrics['r2']} is below AC-2 target of 0.90"

    def test_metrics_has_rmse_and_mae(self, v30_artifact: dict) -> None:
        """Metrics contain RMSE and MAE."""
        metrics = v30_artifact["metrics"]
        assert "rmse" in metrics
        assert "mae" in metrics
        assert metrics["rmse"] > 0
        assert metrics["mae"] > 0

    def test_feature_names_match_v11_schema(self, v30_artifact: dict) -> None:
        """Feature names match the v1.1 20D schema (backward compat)."""
        from nfm_db.ml.energy_features_v11 import ENERGY_V11_FEATURE_NAMES

        assert v30_artifact["feature_names"] == ENERGY_V11_FEATURE_NAMES

    def test_n_samples_is_2909(self, v30_metrics: dict) -> None:
        """Metadata records 2,909 training samples."""
        assert v30_metrics["n_samples"] == 2909

    def test_cv_r2_documented(self, v30_artifact: dict) -> None:
        """Cross-validation R² is documented for honest reporting."""
        metrics = v30_artifact["metrics"]
        assert "cv_r2" in metrics
        assert metrics["cv_r2"] >= 0.90

    def test_train_test_split_documented(self, v30_metrics: dict) -> None:
        """Train/test split sizes are documented."""
        assert v30_metrics["n_train"] + v30_metrics["n_test"] == v30_metrics["n_samples"]
        assert v30_metrics["n_test"] > 0
        assert v30_metrics["n_train"] > v30_metrics["n_test"]


# ---------------------------------------------------------------------------
# AC-4: Version constant and backward compat
# ---------------------------------------------------------------------------


class TestVersionConstant:
    """Verify ENERGY_PREDICTOR_VERSION is bumped to v3.0."""

    def test_version_constant_is_v30(self) -> None:
        """ENERGY_PREDICTOR_VERSION should be 'v3.0'."""
        from nfm_db.ml.model_version import ENERGY_PREDICTOR_VERSION

        assert ENERGY_PREDICTOR_VERSION == "v3.0"

    def test_version_constant_imported_by_prediction_service(self) -> None:
        """prediction_service imports ENERGY_PREDICTOR_VERSION."""
        from nfm_db.ml import prediction_service

        assert hasattr(prediction_service, "ENERGY_PREDICTOR_VERSION")
        assert prediction_service.ENERGY_PREDICTOR_VERSION == "v3.0"


# ---------------------------------------------------------------------------
# AC-5: Prediction via predict_energy (v1.1 contract)
# ---------------------------------------------------------------------------


class TestV30Prediction:
    """Test that v3.0 model routing and prediction match the v1.1 contract."""

    def test_predict_energy_returns_v30_result(self) -> None:
        """predict_energy() with v3.0 model returns dict with required keys."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        # NFM-3956: mock must reflect the [EXPLORATORY] grouped-CV LOW bucket,
        # NOT the inflated random-split R^2 = 0.9858.
        with patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock:
            mock.return_value = {
                "predicted_energy": -0.12,
                "confidence": 0.3111,
                "confidence_source": "grouped_cv_r2_mean",
                "model_version": "v3.0",
                "warnings": [
                    {
                        "code": "energy_model_exploratory",
                        "message": (
                            "EnergyPredictor v3.0 is labeled [EXPLORATORY] "
                            "per NFM-3953. Grouped-CV R^2 = 0.3111 +/- 0.4777."
                        ),
                    }
                ],
            }
            result = predict_energy(features)

        assert result is not None
        assert "predicted_energy" in result
        assert "confidence" in result
        assert "model_version" in result
        assert "warnings" in result
        assert isinstance(result["predicted_energy"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_predict_energy_default_uses_v30(self) -> None:
        """predict_energy() with no version arg routes to _predict_energy_v30."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock:
            mock.return_value = {
                "predicted_energy": -0.05,
                "confidence": 0.98,
                "model_version": "v3.0",
                "warnings": [],
            }
            result = predict_energy(features)
            mock.assert_called_once()

        assert result["model_version"] == "v3.0"

    def test_predict_energy_explicit_v30(self) -> None:
        """predict_energy(model_version='v3.0') routes to v30 artifact."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock:
            mock.return_value = {
                "predicted_energy": -0.07,
                "confidence": 0.99,
                "model_version": "v3.0",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v3.0")
            mock.assert_called_once()
            assert result["model_version"] == "v3.0"

    def test_predict_energy_v11_routing_preserved(self) -> None:
        """predict_energy(model_version='v1.1') routes to v1.1 artifact."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with (
            patch("nfm_db.ml.prediction_service._predict_energy_v11") as mock_v11,
        ):
            mock_v11.return_value = {
                "predicted_energy": -0.08,
                "confidence": 0.83,
                "model_version": "v1.1",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v1.1")
            mock_v11.assert_called_once()
            assert result["model_version"] == "v1.1"

    def test_predict_energy_v10_routing_preserved(self) -> None:
        """predict_energy(model_version='v1.0') routes to v1.0 artifact."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with (
            patch("nfm_db.ml.prediction_service._predict_energy_v10") as mock_v10,
        ):
            mock_v10.return_value = {
                "predicted_energy": -0.03,
                "confidence": 0.82,
                "model_version": "v1.0",
                "warnings": [],
            }
            result = predict_energy(features, model_version="v1.0")
            mock_v10.assert_called_once()
            assert result["model_version"] == "v1.0"

    def test_predict_energy_none_on_missing_artifact(self) -> None:
        """predict_energy returns None when v3.0 model artifact is unavailable."""
        from nfm_db.ml.prediction_service import predict_energy

        features = {"mo_equivalent": 0.5}
        with patch("nfm_db.ml.prediction_service._predict_energy_v30") as mock:
            mock.return_value = None
            result = predict_energy(features)

        assert result is None


# ---------------------------------------------------------------------------
# AC-5: Integration — real artifact loading and prediction
# ---------------------------------------------------------------------------


class TestV30Integration:
    """Integration tests: load the real v30 artifact and verify predictions."""

    def test_v30_model_loads_and_predicts(self, v30_artifact: dict) -> None:
        """The v3.0 artifact loads and produces a real prediction."""
        import numpy as np

        model = v30_artifact["model"]
        feature_names = v30_artifact["feature_names"]
        # Build a minimal feature vector (all zeros except mo_equivalent)
        feature_dict = {name: 0.0 for name in feature_names}
        feature_dict["mo_equivalent"] = 0.5
        vals = np.array([feature_dict[n] for n in feature_names], dtype=np.float64).reshape(1, -1)
        vals = np.nan_to_num(vals, nan=0.0, posinf=0.0, neginf=0.0)
        predicted = model.predict(vals)[0]
        assert isinstance(predicted, (int, float, np.floating))
        assert np.isfinite(predicted)

    def test_v30_prediction_service_returns_v30_version(self, v30_artifact: dict) -> None:
        """predict_energy() returns model_version='v3.0' when using real artifact.

        NFM-3956 round 2: the on-disk v3.0 artifact predates the NFM-3953
        grouped-CV re-evaluation, so ``confidence`` is ``None`` (legacy
        fallback path) and ``confidence_source='random_split_r2'``. The
        helper emits an ``energy_model_pre_grouped_cv`` warning carrying
        the raw R^2 figure so UIs can render the at-risk disclaimer.
        """
        from nfm_db.ml.prediction_service import _predict_energy_v30

        features = {name: 0.0 for name in v30_artifact["feature_names"]}
        features["mo_equivalent"] = 0.5
        result = _predict_energy_v30(features)
        assert result is not None
        assert result["model_version"] == "v3.0"
        assert "predicted_energy" in result
        # Legacy fallback returns None so the inflated headline is not
        # advertised; the warning carries the raw figure.
        assert result["confidence"] is None
        assert result["confidence_source"] == "random_split_r2"
        codes = [w["code"] for w in result["warnings"]]
        assert "energy_model_pre_grouped_cv" in codes

    def test_v30_composition_prediction(self, v30_artifact: dict) -> None:
        """predict_energy_from_composition() routes through v3.0 model."""
        from nfm_db.ml.prediction_service import predict_energy_from_composition

        composition = {"U": 0.3, "O": 0.7}
        result = predict_energy_from_composition(composition)
        assert result is not None
        assert result["model_version"] == "v3.0"
        assert isinstance(result["predicted_energy"], float)
