"""Unit tests for POST /api/v1/predict/energy endpoint (NFM-1789).

Tests use unittest.mock to patch the predict_energy service function,
avoiding the need for a real model artifact or full app import chain.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

MOCK_V30_RESULT = {
    "predicted_energy": -0.35,
    "confidence": 0.98,
    "confidence_source": "random_split_r2",
    "model_version": "v3.0",
    "warnings": [],
}

MOCK_V30_EXPLORATORY_RESULT = {
    "predicted_energy": -0.31,
    "confidence": 0.3111,
    "confidence_source": "grouped_cv_r2_mean",
    "model_version": "v3.0",
    "warnings": [
        {
            "code": "energy_model_exploratory",
            "message": (
                "EnergyPredictor v3.0 is labeled [EXPLORATORY] per NFM-3953. "
                "Grouped-CV R^2 = 0.3111 +/- 0.4777 (bucket: low). The "
                "random-split headline R^2 = 0.9858 was materially "
                "optimistic; reported confidence is from the grouped-CV "
                "figure."
            ),
        }
    ],
}


# ---------------------------------------------------------------------------
# Service-level tests (prediction_service.predict_energy)
# ---------------------------------------------------------------------------


class TestPredictEnergyService:
    """Unit tests for the predict_energy service function."""

    @patch("nfm_db.ml.prediction_service._predict_energy_v30")
    def test_predict_energy_returns_result_dict(self, mock_predict: MagicMock) -> None:
        """predict_energy returns dict with required keys when model available."""
        mock_predict.return_value = MOCK_V30_RESULT

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

        assert result is not None
        assert "predicted_energy" in result
        assert "confidence" in result
        assert "warnings" in result
        assert "model_version" in result
        assert isinstance(result["predicted_energy"], float)
        assert 0 <= result["confidence"] <= 1

    @patch("nfm_db.ml.prediction_service._predict_energy_v30")
    def test_predict_energy_rounds_energy_value(self, mock_predict: MagicMock) -> None:
        """predicted_energy should be rounded to 6 decimal places."""
        # _predict_energy_v30 rounds to 6 decimal places internally
        mock_predict.return_value = {
            "predicted_energy": -0.345679,
            "confidence": 0.82,
            "model_version": "v3.0",
            "warnings": [],
        }

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

        assert result is not None
        assert result["predicted_energy"] == -0.345679

    @patch("nfm_db.ml.prediction_service._predict_energy_v30")
    def test_predict_energy_returns_none_when_model_unavailable(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """predict_energy returns None when model loading fails."""
        mock_predict.return_value = None

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)
        assert result is None

    @patch("nfm_db.ml.prediction_service._predict_energy_v30")
    def test_predict_energy_includes_model_version(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """Result dict should include the energy predictor version."""
        mock_predict.return_value = MOCK_V30_RESULT

        from nfm_db.ml.prediction_service import predict_energy

        result = predict_energy(SAMPLE_FEATURES)

        assert result is not None
        assert result["model_version"] == "v3.0"


# ---------------------------------------------------------------------------
# Endpoint-level tests (API route)
# ---------------------------------------------------------------------------


class TestPredictEnergyEndpoint:
    """Unit tests for the /predict/energy API endpoint.

    These tests mock the service layer before importing the endpoint module,
    avoiding the full app conftest import chain.  All endpoint tests are
    async because the route handler is declared ``async def``.
    """

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_returns_200_with_valid_prediction(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """POST /predict/energy returns 200 with predicted_energy."""
        mock_predict.return_value = MOCK_V30_RESULT

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.success is True
        assert response.data is not None
        assert response.data.predicted_energy == -0.35
        assert response.data.model_version == "v3.0"

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_raises_503_when_model_unavailable(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """POST /predict/energy raises 503 when model is None."""
        mock_predict.return_value = None

        from fastapi import HTTPException

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)

        with pytest.raises(HTTPException) as exc_info:
            await predict_energy_endpoint(request)

        assert exc_info.value.status_code == 503
        assert "energy" in exc_info.value.detail.lower()

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_response_conforms_to_api_response_schema(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """Response body conforms to ApiResponse[EnergyPredictResponse]."""
        mock_predict.return_value = MOCK_V30_RESULT

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

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_includes_warnings_in_response(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """Warnings from confidence scoring are propagated to response."""
        mock_predict.return_value = {
            "predicted_energy": -0.1,
            "confidence": 0.5,
            "confidence_source": "random_split_r2",
            "model_version": "v3.0",
            "warnings": [{"code": "low_confidence", "message": "low"}],
        }

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.data is not None
        assert len(response.data.warnings) >= 1

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_surfaces_confidence_source_for_exploratory_v30(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """The endpoint must pass ``confidence_source`` through to the
        response (NFM-3956 honesty contract regression). For an
        [EXPLORATORY] v3.0 prediction, confidence_source must be
        ``"grouped_cv_r2_mean"`` so downstream UIs can render the
        source-aware disclaimer instead of just the numeric confidence.
        """
        mock_predict.return_value = MOCK_V30_EXPLORATORY_RESULT

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.success is True
        assert response.data is not None
        assert response.data.confidence_source == "grouped_cv_r2_mean"
        assert response.data.confidence == pytest.approx(0.3111, abs=1e-4)
        # The exploratory warning must also surface — that's what makes
        # the disclaimer non-deceptive.
        warning_codes = [w.code for w in response.data.warnings]
        assert "energy_model_exploratory" in warning_codes

    @patch("nfm_db.api.v1.prediction.predict_energy")
    async def test_endpoint_response_never_advertises_inflated_headline(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """NFM-3956 AC: user-facing surfaces must not advertise
        ``confidence == 0.9858`` as the primary value. Even if a legacy
        artifact's ``metrics.r2`` is 0.9858, the helper collapses the
        random-split figure to the grouped-CV mean for [EXPLORATORY]
        artifacts. The endpoint must surface whatever the helper returns.
        """
        # Simulate the helper's exploratory-path output: random-split is
        # 0.9858 in the metrics, but the surfaced confidence is the
        # grouped-CV mean (0.3111) and confidence_source flags it.
        mock_predict.return_value = MOCK_V30_EXPLORATORY_RESULT

        from nfm_db.api.v1.prediction import predict_energy_endpoint
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        response = await predict_energy_endpoint(request)

        assert response.data is not None
        # The headline figure must not be advertised as confidence.
        assert response.data.confidence != pytest.approx(0.9858, abs=1e-4)
        # And confidence_source must not be silently absent.
        assert response.data.confidence_source != ""


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
            predicted_energy=-0.5,
            confidence=0.8,
            confidence_source="random_split_r2",
            model_version="v1.1",
        )
        assert resp.confidence == 0.8
        assert resp.confidence_source == "random_split_r2"

        # Invalid confidence > 1
        with pytest.raises(pydantic.ValidationError):
            EnergyPredictResponse(
                predicted_energy=-0.5,
                confidence=1.5,
                confidence_source="random_split_r2",
                model_version="v1.1",
            )

    def test_energy_predict_response_requires_confidence_source(self) -> None:
        """NFM-3956 honesty contract: ``confidence_source`` is required
        on every energy prediction response. Dropping it at the API
        boundary (the bug E2E QA flagged) must fail schema validation."""
        import pydantic

        from nfm_db.schemas.prediction import EnergyPredictResponse

        with pytest.raises(pydantic.ValidationError) as exc_info:
            EnergyPredictResponse(
                predicted_energy=-0.5,
                confidence=0.8,
                # confidence_source intentionally omitted
                model_version="v3.0",
            )
        # The error must reference confidence_source specifically.
        assert "confidence_source" in str(exc_info.value)

    def test_energy_predict_request_to_feature_dict(self) -> None:
        """to_feature_dict() returns correct dict for service call."""
        from nfm_db.schemas.prediction import EnergyPredictRequest

        request = EnergyPredictRequest(**SAMPLE_FEATURES)
        features = request.to_feature_dict()

        assert set(features.keys()) == {
            "mo_equivalent",
            "pauling_chi_diff",
            "allen_chi_diff",
            "config_entropy",
            "bv_ratio",
            "u_density",
            "mixing_enthalpy",
            "lattice_distortion",
        }
        assert features["mo_equivalent"] == 2.0
        assert features["mixing_enthalpy"] == -5.0


# ---------------------------------------------------------------------------
# End-to-end TestClient regression (NFM-3956 AC2 — surfaces full schema)
# ---------------------------------------------------------------------------


class TestPredictEnergyEndpointTestClient:
    """NFM-3956 E2E QA regression: the FastAPI endpoint must surface the
    full response schema including ``confidence_source``. The previous
    surface scan only checked status codes (200/503) and missed that
    ``confidence_source`` was being silently dropped at the Pydantic
    schema + endpoint constructor. This test calls the actual FastAPI
    route via ``TestClient`` and asserts the full response envelope.
    """

    def _make_client(self) -> TestClient:
        from nfm_db.api.v1.prediction import router as prediction_router

        app = FastAPI()
        app.include_router(prediction_router, prefix="/api/v1")
        return TestClient(app)

    @patch("nfm_db.api.v1.prediction.predict_energy")
    def test_full_response_envelope_includes_confidence_source(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """The JSON response envelope must carry ``confidence_source``
        alongside ``confidence`` and the warning list. This is the
        NFM-3956 E2E-QA regression — ``confidence_source`` must reach
        the wire, not be dropped at the API boundary.
        """
        mock_predict.return_value = MOCK_V30_EXPLORATORY_RESULT

        client = self._make_client()
        response = client.post("/api/v1/predict/energy", json=SAMPLE_FEATURES)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True

        data = body["data"]
        # Every advertised NFM-3956 field must be on the wire:
        assert "predicted_energy" in data
        assert "confidence" in data
        assert "confidence_source" in data, (
            "NFM-3956 honesty contract regression: confidence_source "
            "is missing from the response envelope"
        )
        assert "warnings" in data
        assert "model_version" in data

        # Honest values, not the inflated headline:
        assert data["confidence"] == pytest.approx(0.3111, abs=1e-4)
        assert data["confidence_source"] == "grouped_cv_r2_mean"
        assert data["confidence"] != pytest.approx(0.9858, abs=1e-4)

        # The exploratory warning must surface so callers know the
        # confidence is from grouped-CV, not the legacy figure.
        warning_codes = [w["code"] for w in data["warnings"]]
        assert "energy_model_exploratory" in warning_codes

    @patch("nfm_db.api.v1.prediction.predict_energy")
    def test_legacy_v11_path_also_surfaces_confidence_source(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """The v1.1 legacy caller path must also carry
        ``confidence_source`` (NFM-3956 — every energy prediction
        response surfaces the source, not just v3.0). v1.1's
        pre-NFM-3953 r2=0.8333 is not the inflated headline but the
        helper still labels it ``"random_split_r2"`` so callers can
        render a disclaimer.
        """
        mock_predict.return_value = {
            "predicted_energy": -0.4,
            "confidence": 0.8333,
            "confidence_source": "random_split_r2",
            "model_version": "v1.1",
            "warnings": [
                {
                    "code": "energy_model_pre_grouped_cv",
                    "message": (
                        "EnergyPredictor artifact predates the NFM-3953 "
                        "grouped-CV re-evaluation; the random-split R^2 "
                        "may be materially optimistic."
                    ),
                }
            ],
        }

        client = self._make_client()
        response = client.post("/api/v1/predict/energy", json=SAMPLE_FEATURES)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["confidence_source"] == "random_split_r2"
        # v1.1 is not the inflated headline figure.
        assert data["confidence"] != pytest.approx(0.9858, abs=1e-4)

    @patch("nfm_db.api.v1.prediction.predict_energy")
    def test_endpoint_returns_503_when_service_unavailable(
        self,
        mock_predict: MagicMock,
    ) -> None:
        """Sanity check — 503 path still works after the schema
        changes (model-unavailable must still surface, not silently
        crash with a Pydantic validation error)."""
        mock_predict.return_value = None

        client = self._make_client()
        response = client.post("/api/v1/predict/energy", json=SAMPLE_FEATURES)

        assert response.status_code == 503
