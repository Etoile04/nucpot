"""Integration tests for ML prediction API endpoints (NFM-1598).

Tests POST /api/v1/predict/phase and POST /api/v1/predict/temperature
using mocked models to avoid requiring trained artifacts in CI.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nfm_db.main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_FEATURES: dict[str, float] = {
    "mo_equivalent": 10.0,
    "pauling_chi_diff": 0.078,
    "allen_chi_diff": 0.065,
    "config_entropy": 1.904,
    "bv_ratio": 15.2,
    "u_density": 17.65,
    "mixing_enthalpy": -0.45,
    "lattice_distortion": 0.042,
}

VALID_EXOTHERMIC: dict[str, float] = {
    "mo_equivalent": 15.0,
    "pauling_chi_diff": 0.12,
    "allen_chi_diff": 0.09,
    "config_entropy": 2.5,
    "bv_ratio": 18.0,
    "u_density": 16.5,
    "mixing_enthalpy": -20.0,
    "lattice_distortion": 0.06,
}


def _make_mock_phase_model():
    model = MagicMock()
    model.predict.return_value = [0]
    model.predict_proba.return_value = [[0.6, 0.3, 0.08, 0.02]]
    return model


def _make_mock_temp_model():
    model = MagicMock()
    model.predict.return_value = [620.0]
    return model


# ---------------------------------------------------------------------------
# Phase classification endpoint tests
# ---------------------------------------------------------------------------


class TestPredictPhaseEndpoint:
    """Tests for POST /api/v1/predict/phase."""

    @patch("nfm_db.ml.prediction_service._phase_model", None)
    def test_phase_503_when_model_unavailable(self):
        response = client.post("/api/v1/predict/phase", json=VALID_FEATURES)
        assert response.status_code == 503
        assert response.json()["detail"] is not None

    @patch("nfm_db.ml.prediction_service._phase_model", _make_mock_phase_model())
    def test_phase_success(self):
        response = client.post("/api/v1/predict/phase", json=VALID_FEATURES)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["predicted_phase"] == "I"
        assert data["predicted_phase_label"] == "α-U (single phase)"
        assert len(data["probabilities"]) == 4
        assert data["probabilities"][0]["cluster_type"] == "I"
        assert data["probabilities"][0]["probability"] == 0.6
        assert data["model_version"] == "v0.1"

    @patch("nfm_db.ml.prediction_service._phase_model", _make_mock_phase_model())
    def test_phase_probabilities_sum_to_one(self):
        response = client.post("/api/v1/predict/phase", json=VALID_FEATURES)
        probs = response.json()["data"]["probabilities"]
        total = sum(p["probability"] for p in probs)
        assert abs(total - 1.0) < 0.01

    @patch("nfm_db.ml.prediction_service._phase_model", _make_mock_phase_model())
    def test_phase_exothermic_input(self):
        response = client.post("/api/v1/predict/phase", json=VALID_EXOTHERMIC)
        assert response.status_code == 200
        assert response.json()["data"]["predicted_phase"] == "I"

    def test_phase_422_missing_fields(self):
        response = client.post(
            "/api/v1/predict/phase",
            json={"mo_equivalent": 10.0, "pauling_chi_diff": 0.078},
        )
        assert response.status_code == 422

    def test_phase_422_negative_mo_equivalent(self):
        bad = {**VALID_FEATURES, "mo_equivalent": -1.0}
        response = client.post("/api/v1/predict/phase", json=bad)
        assert response.status_code == 422

    def test_phase_422_wrong_type(self):
        bad = {**VALID_FEATURES, "config_entropy": "not_a_number"}
        response = client.post("/api/v1/predict/phase", json=bad)
        assert response.status_code == 422

    @patch("nfm_db.ml.prediction_service._phase_model", _make_mock_phase_model())
    def test_phase_response_time_under_500ms(self):
        start = time.perf_counter()
        response = client.post("/api/v1/predict/phase", json=VALID_FEATURES)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 500, f"Phase prediction took {elapsed_ms:.0f}ms"


# ---------------------------------------------------------------------------
# Temperature prediction endpoint tests
# ---------------------------------------------------------------------------


class TestPredictTemperatureEndpoint:
    """Tests for POST /api/v1/predict/temperature."""

    @patch("nfm_db.ml.prediction_service._temp_model", None)
    def test_temp_503_when_model_unavailable(self):
        response = client.post("/api/v1/predict/temperature", json=VALID_FEATURES)
        assert response.status_code == 503
        assert response.json()["detail"] is not None

    @patch("nfm_db.ml.prediction_service._temp_model", _make_mock_temp_model())
    def test_temp_success(self):
        response = client.post("/api/v1/predict/temperature", json=VALID_FEATURES)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["predicted_temp_c"] == 620.0
        assert data["confidence_lower_c"] < data["predicted_temp_c"]
        assert data["confidence_upper_c"] > data["predicted_temp_c"]
        assert data["model_version"] == "v0.1"

    @patch("nfm_db.ml.prediction_service._temp_model", _make_mock_temp_model())
    def test_temp_confidence_interval_width(self):
        response = client.post("/api/v1/predict/temperature", json=VALID_FEATURES)
        data = response.json()["data"]
        width = data["confidence_upper_c"] - data["confidence_lower_c"]
        assert width >= 30.0

    @patch("nfm_db.ml.prediction_service._temp_model", _make_mock_temp_model())
    def test_temp_endothermic_input(self):
        response = client.post("/api/v1/predict/temperature", json=VALID_EXOTHERMIC)
        assert response.status_code == 200
        assert response.json()["data"]["predicted_temp_c"] == 620.0

    def test_temp_422_missing_fields(self):
        response = client.post(
            "/api/v1/predict/temperature",
            json={"mo_equivalent": 10.0},
        )
        assert response.status_code == 422

    def test_temp_422_wrong_type(self):
        bad = {**VALID_FEATURES, "bv_ratio": "bad"}
        response = client.post("/api/v1/predict/temperature", json=bad)
        assert response.status_code == 422

    @patch("nfm_db.ml.prediction_service._temp_model", _make_mock_temp_model())
    def test_temp_response_time_under_500ms(self):
        start = time.perf_counter()
        response = client.post("/api/v1/predict/temperature", json=VALID_FEATURES)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 500, f"Temp prediction took {elapsed_ms:.0f}ms"


# ---------------------------------------------------------------------------
# PredictionFeatures schema unit tests
# ---------------------------------------------------------------------------


class TestPredictionFeaturesSchema:
    """Unit tests for the PredictionFeatures Pydantic model."""

    def test_valid_features(self):
        from nfm_db.schemas.prediction import PredictionFeatures

        features = PredictionFeatures(**VALID_FEATURES)
        assert features.mo_equivalent == 10.0
        assert features.mixing_enthalpy == -0.45

    def test_to_feature_dict_keys(self):
        from nfm_db.schemas.prediction import PredictionFeatures

        features = PredictionFeatures(**VALID_FEATURES)
        d = features.to_feature_dict()
        expected_keys = [
            "mo_equivalent",
            "pauling_chi_diff",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        ]
        assert list(d.keys()) == expected_keys

    def test_to_feature_array(self):
        from nfm_db.schemas.prediction import PredictionFeatures

        features = PredictionFeatures(**VALID_FEATURES)
        arr = features.to_feature_array()
        assert len(arr) == 8
        assert arr[0] == 10.0

    def test_negative_mo_equivalent_rejected(self):
        from pydantic import ValidationError

        from nfm_db.schemas.prediction import PredictionFeatures

        with pytest.raises(ValidationError):
            PredictionFeatures(**{**VALID_FEATURES, "mo_equivalent": -1.0})

    def test_negative_entropy_rejected(self):
        from pydantic import ValidationError

        from nfm_db.schemas.prediction import PredictionFeatures

        with pytest.raises(ValidationError):
            PredictionFeatures(**{**VALID_FEATURES, "config_entropy": -1.0})

    def test_negative_lattice_distortion_rejected(self):
        from pydantic import ValidationError

        from nfm_db.schemas.prediction import PredictionFeatures

        with pytest.raises(ValidationError):
            PredictionFeatures(**{**VALID_FEATURES, "lattice_distortion": -1.0})

    def test_zero_features_valid(self):
        from nfm_db.schemas.prediction import PredictionFeatures

        zero = {k: 0.0 for k in VALID_FEATURES}
        features = PredictionFeatures(**zero)
        assert features.mo_equivalent == 0.0

    def test_mixing_enthalpy_allows_negative(self):
        from nfm_db.schemas.prediction import PredictionFeatures

        features = PredictionFeatures(**{**VALID_FEATURES, "mixing_enthalpy": -50.0})
        assert features.mixing_enthalpy == -50.0


# ---------------------------------------------------------------------------
# Feature vector construction tests
# ---------------------------------------------------------------------------


class TestFeatureVectorConstruction:
    """Tests for prediction_service.build_feature_vector."""

    def test_vector_length_is_12(self):
        from nfm_db.ml.prediction_service import build_feature_vector

        vec = build_feature_vector(VALID_FEATURES)
        assert vec.shape == (12,)

    def test_physical_features_first_8(self):
        from nfm_db.ml.prediction_service import build_feature_vector

        vec = build_feature_vector(VALID_FEATURES)
        assert vec[0] == pytest.approx(10.0)  # mo_equivalent
        assert vec[7] == pytest.approx(0.042)  # lattice_distortion

    def test_cluster_type_inference_strongly_exothermic(self):
        from nfm_db.ml.prediction_service import _cluster_type_from_features

        assert _cluster_type_from_features({"mixing_enthalpy": -10.0}) == "I"

    def test_cluster_type_inference_mild(self):
        from nfm_db.ml.prediction_service import _cluster_type_from_features

        assert _cluster_type_from_features(
            {"mixing_enthalpy": 0.0, "pauling_chi_diff": 0.05}
        ) == "II"

    def test_cluster_type_inference_moderate_endothermic(self):
        from nfm_db.ml.prediction_service import _cluster_type_from_features

        assert _cluster_type_from_features({"mixing_enthalpy": 8.0}) == "III"

    def test_cluster_type_inference_strongly_endothermic(self):
        from nfm_db.ml.prediction_service import _cluster_type_from_features

        assert _cluster_type_from_features({"mixing_enthalpy": 20.0}) == "IV"

    def test_explicit_cluster_type_iii_onehot(self):
        from nfm_db.ml.prediction_service import build_feature_vector

        vec = build_feature_vector(VALID_FEATURES, cluster_type="III")
        assert vec[8 + 2] == pytest.approx(1.0)  # type_III at index 2
        assert vec[8 + 0] == pytest.approx(0.0)  # type_I at index 0

    def test_explicit_cluster_type_i_onehot(self):
        from nfm_db.ml.prediction_service import build_feature_vector

        vec = build_feature_vector(VALID_FEATURES, cluster_type="I")
        assert vec[8 + 0] == pytest.approx(1.0)  # type_I at index 0


# ---------------------------------------------------------------------------
# Prediction service unit tests
# ---------------------------------------------------------------------------


class TestPredictionService:
    """Unit tests for predict_phase and predict_temperature functions."""

    @patch("nfm_db.ml.prediction_service._phase_model", _make_mock_phase_model())
    def test_predict_phase_returns_dict(self):
        from nfm_db.ml.prediction_service import predict_phase

        result = predict_phase(VALID_FEATURES)
        assert isinstance(result, dict)
        assert "predicted_phase" in result
        assert "probabilities" in result
        assert "model_version" in result

    @patch("nfm_db.ml.prediction_service._phase_model", None)
    def test_predict_phase_returns_none_when_unavailable(self):
        from nfm_db.ml.prediction_service import predict_phase

        result = predict_phase(VALID_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service._temp_model", _make_mock_temp_model())
    def test_predict_temperature_returns_dict(self):
        from nfm_db.ml.prediction_service import predict_temperature

        result = predict_temperature(VALID_FEATURES)
        assert isinstance(result, dict)
        assert "predicted_temp_c" in result
        assert "confidence_lower_c" in result
        assert "confidence_upper_c" in result

    @patch("nfm_db.ml.prediction_service._temp_model", None)
    def test_predict_temperature_returns_none_when_unavailable(self):
        from nfm_db.ml.prediction_service import predict_temperature

        result = predict_temperature(VALID_FEATURES)
        assert result is None
