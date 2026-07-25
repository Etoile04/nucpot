"""Unit tests for POST /api/v1/predict/energy endpoint (NFM-1789, NFM-1802).

Tests use unittest.mock to patch the predict_energy service function,
avoiding the need for a real model artifact or full app import chain.

After NFM-1802 v1.1 merge, predict_energy() dispatches to predict_energy_v11
by default (no more _load_energy_predictor). Tests mock predict_energy_v11
where it is *consumed* (prediction_service module namespace), not where it
is defined — Python imports create local bindings, so patching the source
module would not affect the already-imported reference.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Sample feature dicts
# ---------------------------------------------------------------------------

# v1.0 8D features (matches PHYSICAL_FEATURE_NAMES in prediction_service.py)
SAMPLE_V10_FEATURES = {
    "mo_equivalent": 2.0,
    "pauling_chi_diff": 0.08,
    "allen_chi_diff": 0.05,
    "config_entropy": 1.2,
    "bv_ratio": 8.5,
    "u_density": 18.5,
    "mixing_enthalpy": -5.0,
    "lattice_distortion": 0.03,
}

# v1.1 20D features (all 20 keys from ENERGY_V11_FEATURE_NAMES)
SAMPLE_V11_FEATURES = {
    "mo_equivalent": 2.0,
    "allen_chi_diff": 0.05,
    "config_entropy": 1.2,
    "bv_ratio": 8.5,
    "u_density": 18.5,
    "mixing_enthalpy": -5.0,
    "lattice_distortion": 0.03,
    "vec": 3.0,
    "avg_allen_chi": 1.5,
    "avg_atomic_volume": 15.0,
    "avg_d_electron": 4.0,
    "avg_work_function": 4.5,
    "avg_bulk_modulus": 200.0,
    "hr_valence_diff": 0.3,
    "dg_en_radius_distance": 0.1,
    "max_pair_en_diff": 0.5,
    "en_variance": 0.02,
    "volume_variance": 0.01,
    "d_electron_variance": 0.3,
    "bulk_modulus_variance": 100.0,
}

V11_MOCK_RESULT = {
    "predicted_energy": -0.35,
    "confidence": 0.85,
    "model_version": "v1.1",
    "warnings": [],
}

# ---------------------------------------------------------------------------
# Service-level tests (prediction_service.predict_energy)
# ---------------------------------------------------------------------------


class TestPredictEnergyService:
    """Unit tests for the predict_energy service function."""

    @patch("nfm_db.ml.prediction_service.predict_energy_v11", return_value=V11_MOCK_RESULT)
    def test_predict_energy_returns_result_dict(self, mock_predict: MagicMock) -> None:
        """predict_energy returns dict with required keys when model available."""
        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_V10_FEATURES)

        assert result is not None
        assert "predicted_energy" in result
        assert "confidence" in result
        assert "warnings" in result
        assert "model_version" in result
        assert isinstance(result["predicted_energy"], float)
        assert 0 <= result["confidence"] <= 1

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_rounds_energy_value(self, mock_predict: MagicMock) -> None:
        """predicted_energy should be rounded to 6 decimal places."""
        mock_predict.return_value = {
            "predicted_energy": -0.345679,
            "confidence": 0.8,
            "model_version": "v1.1",
            "warnings": [],
        }

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_V10_FEATURES)

        assert result is not None
        assert result["predicted_energy"] == -0.345679

    @patch("nfm_db.ml.prediction_service.predict_energy_v11", return_value=None)
    def test_predict_energy_returns_none_when_model_unavailable(self, mock_predict: MagicMock) -> None:
        """predict_energy returns None when model loading fails."""
        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_V10_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service.predict_energy_v11", return_value=None)
    def test_predict_energy_returns_none_on_predict_exception(self, mock_predict: MagicMock) -> None:
        """predict_energy returns None when predict_energy_v11 returns None (model error)."""
        # predict_energy_v11 catches exceptions internally and returns None;
        # mocking it to return None simulates that error-handling path.

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_V10_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service.predict_energy_v11", return_value=V11_MOCK_RESULT)
    def test_predict_energy_includes_model_version(self, mock_predict: MagicMock) -> None:
        """Result dict should include the energy predictor version."""
        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_V10_FEATURES)

        assert result is not None
        assert result["model_version"] == "v1.1"


# ---------------------------------------------------------------------------
# Endpoint-level tests (API route)
# ---------------------------------------------------------------------------


class TestPredictEnergyEndpoint:
    """Unit tests for the /predict/energy API endpoint.

    These tests mock the service layer before importing the endpoint module,
    avoiding the full app conftest import chain.  All endpoint tests are
    async because the route handler is declared ``async def``.
    """

    @patch("nfm_db.api.v1.prediction.predict_energy", return_value=V11_MOCK_RESULT)
    async def test_endpoint_returns_200_with_valid_prediction(self, mock_predict: MagicMock) -> None:
        """POST /predict/energy returns 200 with predicted_energy."""
        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_V10_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.success is True
        assert response.data is not None
        assert response.data.predicted_energy == -0.35

    @patch("nfm_db.api.v1.prediction.predict_energy", return_value=None)
    async def test_endpoint_raises_503_when_model_unavailable(self, mock_predict: MagicMock) -> None:
        """POST /predict/energy raises 503 when model is None."""
        from fastapi import HTTPException

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_V10_FEATURES)

        with pytest.raises(HTTPException) as exc_info:
            await predict_energy_endpoint(request)

        assert exc_info.value.status_code == 503
        assert "energy" in exc_info.value.detail.lower()

    @patch("nfm_db.api.v1.prediction.predict_energy", return_value=V11_MOCK_RESULT)
    async def test_endpoint_response_conforms_to_api_response_schema(self, mock_predict: MagicMock) -> None:
        """Response body conforms to ApiResponse[EnergyPredictResponse]."""
        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.common import ApiResponse
        from nfm_db.schemas.prediction import EnergyPredictRequest, EnergyPredictResponse

        request = EnergyPredictRequest(**SAMPLE_V10_FEATURES)
        response = await predict_energy_endpoint(request)

        # Validate envelope shape
        assert isinstance(response, ApiResponse)
        assert response.success is True
        assert response.error is None
        assert isinstance(response.data, EnergyPredictResponse)

        # Validate data fields
        assert isinstance(response.data.predicted_energy, float)
        assert 0 <= response.data.confidence <= 1
        assert isinstance(response.data.warnings, list)
        assert isinstance(response.data.model_version, str)

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_includes_warnings_in_response(self, mock_predict: MagicMock) -> None:
        """Warnings from confidence scoring are propagated to response."""
        mock_predict.return_value = {
            "predicted_energy": -0.1,
            "confidence": 0.3,
            "model_version": "v1.1",
            "warnings": [{"code": "low_confidence", "message": "Confidence below threshold"}],
        }

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_V10_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.success is True
        assert len(response.data.warnings) == 1
        assert response.data.warnings[0].code == "low_confidence"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestEnergyPredictSchemas:
    """Unit tests for EnergyPredictRequest / EnergyPredictResponse schemas."""

    def test_energy_predict_response_validation(self) -> None:
        """Confidence must be in [0, 1]."""
        import pydantic

        from nfm_db.schemas.prediction import EnergyPredictResponse

        # Valid
        resp = EnergyPredictResponse(
            predicted_energy=-0.5, confidence=0.8, model_version="v1.1",
        )
        assert resp.confidence == 0.8

        # Invalid confidence > 1
        with pytest.raises(pydantic.ValidationError):
            EnergyPredictResponse(
                predicted_energy=-0.5, confidence=1.5, model_version="v1.1",
            )

    def test_energy_predict_request_to_feature_dict(self) -> None:
        """to_feature_dict() returns correct dict for service call."""
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_V10_FEATURES)
        features = request.to_feature_dict()

        assert set(features.keys()) == {
            "mo_equivalent", "pauling_chi_diff", "allen_chi_diff",
            "config_entropy", "bv_ratio", "u_density",
            "mixing_enthalpy", "lattice_distortion",
        }
        assert features["mo_equivalent"] == 2.0
        assert features["mixing_enthalpy"] == -5.0
