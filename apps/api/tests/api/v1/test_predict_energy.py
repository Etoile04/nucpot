"""Unit tests for POST /api/v1/predict/energy endpoint (NFM-1789).

Tests use unittest.mock to patch the predict_energy_v11 service function,
avoiding the need for a real model artifact or full app import chain.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Sample feature dict matching PredictionFeatures.to_feature_dict()
# ---------------------------------------------------------------------------

SAMPLE_FEATURES = {
    "mo_equivalent": 2.0,
    "pauling_chi_diff": 0.08,
    "allen_chi_diff": 0.05,
    "config_entropy": 1.2,
    "bv_ratio": 8.5,
    "u_density": 18.5,
    "mixing_enthalpy": -5.0,
    "lattice_distortion": 0.03,
}


def _mock_v11_response(value: float = -0.35) -> dict:
    """Build a mock v1.1 predict response."""
    return {
        "predicted_energy": round(value, 6),
        "confidence": 0.85,
        "model_version": "v1.1",
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Service-level tests (prediction_service.predict_energy)
# ---------------------------------------------------------------------------


class TestPredictEnergyService:
    """Unit tests for the predict_energy service function."""

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_returns_result_dict(self, mock_v11: MagicMock) -> None:
        """predict_energy returns dict with required keys when model available."""
        mock_v11.return_value = _mock_v11_response()

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

        assert result is not None
        assert "predicted_energy" in result
        assert "confidence" in result
        assert "warnings" in result
        assert "model_version" in result
        assert isinstance(result["predicted_energy"], float)
        assert 0 <= result["confidence"] <= 1

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_rounds_energy_value(self, mock_v11: MagicMock) -> None:
        """predicted_energy should be rounded to 6 decimal places."""
        mock_v11.return_value = _mock_v11_response(value=-0.345678901)

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

        assert result is not None
        assert result["predicted_energy"] == -0.345679

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_returns_none_when_model_unavailable(
        self, mock_v11: MagicMock,
    ) -> None:
        """predict_energy returns None when model loading fails."""
        mock_v11.return_value = None

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_returns_none_on_predict_exception(
        self, mock_v11: MagicMock,
    ) -> None:
        """predict_energy returns None when model.predict raises."""
        mock_v11.side_effect = RuntimeError("predict failed")

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    def test_predict_energy_includes_model_version(
        self, mock_v11: MagicMock,
    ) -> None:
        """Result dict should include the energy predictor version."""
        mock_v11.return_value = _mock_v11_response(value=-0.1)

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

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

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    async def test_endpoint_returns_200_with_valid_prediction(
        self, mock_v11: MagicMock,
    ) -> None:
        """POST /predict/energy returns 200 with predicted_energy."""
        mock_v11.return_value = _mock_v11_response(value=-0.42)

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.success is True
        assert response.data is not None
        assert response.data.predicted_energy == -0.42
        assert response.data.model_version == "v1.1"

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    async def test_endpoint_raises_503_when_model_unavailable(
        self, mock_v11: MagicMock,
    ) -> None:
        """POST /predict/energy raises 503 when model is None."""
        mock_v11.return_value = None

        from fastapi import HTTPException

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)

        with pytest.raises(HTTPException) as exc_info:
            await predict_energy_endpoint(request)

        assert exc_info.value.status_code == 503
        assert "energy" in exc_info.value.detail.lower()

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    async def test_endpoint_response_conforms_to_api_response_schema(
        self, mock_v11: MagicMock,
    ) -> None:
        """Response body conforms to ApiResponse[EnergyPredictResponse]."""
        mock_v11.return_value = _mock_v11_response(value=-0.123456)

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.common import ApiResponse
        from nfm_db.schemas.prediction import EnergyPredictRequest, EnergyPredictResponse

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
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

    @patch("nfm_db.ml.prediction_service.predict_energy_v11")
    async def test_endpoint_includes_warnings_in_response(
        self, mock_v11: MagicMock,
    ) -> None:
        """Warnings from confidence scoring are propagated to response."""
        mock_v11.return_value = _mock_v11_response(value=-0.1)

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.data is not None
        # v1.1 model may include warnings
        assert isinstance(response.data.warnings, list)


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestEnergyPredictSchemas:
    """Tests for EnergyPredictRequest and EnergyPredictResponse validation."""

    def test_energy_predict_request_accepts_valid_features(self) -> None:
        """EnergyPredictRequest validates correctly with 8 features."""
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        assert request.mo_equivalent == 2.0
        assert request.mixing_enthalpy == -5.0

    def test_energy_predict_request_rejects_negative_ge_field(self) -> None:
        """Fields with ge=0 constraint reject negative values."""
        import pydantic

        from nfm_db.schemas.prediction import EnergyPredictRequest

        bad_features = {
            **SAMPLE_FEATURES,
            "mo_equivalent": -1.0,
        }
        with pytest.raises(pydantic.ValidationError):
            EnergyPredictRequest(**bad_features)

    def test_energy_predict_response_validation(self) -> None:
        """EnergyPredictResponse validates confidence range."""
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

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        features = request.to_feature_dict()

        assert set(features.keys()) == {
            "mo_equivalent", "pauling_chi_diff", "allen_chi_diff",
            "config_entropy", "bv_ratio", "u_density",
            "mixing_enthalpy", "lattice_distortion",
        }
        assert features["mo_equivalent"] == 2.0
        assert features["mixing_enthalpy"] == -5.0
