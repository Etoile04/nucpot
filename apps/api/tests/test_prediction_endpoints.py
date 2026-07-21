"""Tests for ML prediction API endpoints (NFM-1596).

Covers POST /api/v1/predict/phase and POST /api/v1/predict/temperature
with mocked model inference, validation edge cases, and 503 fallback.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

VALID_FEATURES = {
    "mo_equivalent": 2.0,
    "pauling_chi_diff": 0.08,
    "allen_chi_diff": 0.12,
    "config_entropy": 0.35,
    "bv_ratio": 1.2,
    "u_density": 18.8,
    "mixing_enthalpy": -5.0,
    "lattice_distortion": 0.02,
}

PHASE_MODEL_RESULT = {
    "predicted_phase": "single_phase",
    "predicted_phase_label": "single phase",
    "probabilities": [
        {"class": "single_phase", "probability": 0.88},
        {"class": "multi_phase", "probability": 0.12},
    ],
    "confidence": 0.88,
    "warnings": [],
    "model_version": "v1.0",
}

TEMP_MODEL_RESULT = {
    "predicted_temp_c": 620.5,
    "confidence_lower_c": 595.0,
    "confidence_upper_c": 646.0,
    "gpr_predicted_temp_c": 615.0,
    "svr_predicted_temp_c": 626.0,
    "confidence": 0.72,
    "warnings": [],
    "model_version": "v1.0",
}


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/predict/phase
# ---------------------------------------------------------------------------


class TestPredictPhase:
    """Tests for the phase classification endpoint."""

    @pytest.mark.asyncio
    async def test_phase_success(self, client: AsyncClient) -> None:
        """Returns 200 with phase classification when model is available."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT,
        ):
            resp = await client.post("/api/v1/predict/phase", json=VALID_FEATURES)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["predicted_phase"] == "single_phase"
        assert data["predicted_phase_label"] == "single phase"
        assert len(data["probabilities"]) == 2
        assert data["model_version"] == "v1.0"

    @pytest.mark.asyncio
    async def test_phase_503_when_model_unavailable(self, client: AsyncClient) -> None:
        """Returns 503 when the phase classifier model cannot be loaded."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=None,
        ):
            resp = await client.post("/api/v1/predict/phase", json=VALID_FEATURES)

        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_phase_validation_missing_field(self, client: AsyncClient) -> None:
        """Returns 422 when a required feature field is missing."""
        incomplete = dict(VALID_FEATURES)
        del incomplete["mo_equivalent"]

        resp = await client.post("/api/v1/predict/phase", json=incomplete)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_phase_validation_negative_ge_zero(self, client: AsyncClient) -> None:
        """Returns 422 when a ge=0 field is negative."""
        bad_features = dict(VALID_FEATURES, mo_equivalent=-1.0)

        resp = await client.post("/api/v1/predict/phase", json=bad_features)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_phase_validation_extra_fields_ignored(
        self, client: AsyncClient
    ) -> None:
        """Extra fields in the request body are silently ignored."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT,
        ):
            payload = {**VALID_FEATURES, "extra_field": 42}
            resp = await client.post("/api/v1/predict/phase", json=payload)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_phase_validation_empty_body(self, client: AsyncClient) -> None:
        """Returns 422 when the request body is empty."""
        resp = await client.post("/api/v1/predict/phase", json={})

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_phase_allows_negative_mixing_enthalpy(
        self, client: AsyncClient
    ) -> None:
        """mixing_enthalpy has no ge constraint — negative values are valid."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT,
        ):
            features = dict(VALID_FEATURES, mixing_enthalpy=-25.0)
            resp = await client.post("/api/v1/predict/phase", json=features)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_phase_probability_bounds(self, client: AsyncClient) -> None:
        """Response probabilities are within [0, 1]."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT,
        ):
            resp = await client.post("/api/v1/predict/phase", json=VALID_FEATURES)

        data = resp.json()["data"]
        for item in data["probabilities"]:
            assert 0.0 <= item["probability"] <= 1.0

    @pytest.mark.asyncio
    async def test_phase_confidence_and_warnings(self, client: AsyncClient) -> None:
        """Response includes confidence score and warnings array."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT,
        ):
            resp = await client.post("/api/v1/predict/phase", json=VALID_FEATURES)

        data = resp.json()["data"]
        assert "confidence" in data
        assert 0.0 <= data["confidence"] <= 1.0
        assert "warnings" in data
        assert isinstance(data["warnings"], list)

    @pytest.mark.asyncio
    async def test_phase_low_confidence_warning(self, client: AsyncClient) -> None:
        """Low confidence generates warning in response."""
        low_conf_result = dict(PHASE_MODEL_RESULT)
        low_conf_result["confidence"] = 0.35
        low_conf_result["warnings"] = [
            {"code": "low_confidence_on_phase", "message": "Below threshold"}
        ]

        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=low_conf_result,
        ):
            resp = await client.post("/api/v1/predict/phase", json=VALID_FEATURES)

        data = resp.json()["data"]
        assert data["confidence"] == 0.35
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["code"] == "low_confidence_on_phase"


# ---------------------------------------------------------------------------
# POST /api/v1/predict/temperature
# ---------------------------------------------------------------------------


class TestPredictTemperature:
    """Tests for the temperature prediction endpoint."""

    @pytest.mark.asyncio
    async def test_temperature_success(self, client: AsyncClient) -> None:
        """Returns 200 with temperature prediction when model is available."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["predicted_temp_c"] == 620.5
        assert data["confidence_lower_c"] < data["predicted_temp_c"]
        assert data["confidence_upper_c"] > data["predicted_temp_c"]
        assert data["gpr_predicted_temp_c"] == 615.0
        assert data["svr_predicted_temp_c"] == 626.0
        assert data["model_version"] == "v1.0"

    @pytest.mark.asyncio
    async def test_temperature_503_when_model_unavailable(
        self, client: AsyncClient
    ) -> None:
        """Returns 503 when the temperature predictor model cannot be loaded."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=None,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_temperature_validation_missing_field(
        self, client: AsyncClient
    ) -> None:
        """Returns 422 when a required feature field is missing."""
        incomplete = dict(VALID_FEATURES)
        del incomplete["u_density"]

        resp = await client.post(
            "/api/v1/predict/temperature", json=incomplete
        )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_temperature_null_gpr_svr(self, client: AsyncClient) -> None:
        """Handles None values for GPR/SVR component predictions."""
        result_no_components = dict(TEMP_MODEL_RESULT)
        result_no_components["gpr_predicted_temp_c"] = None
        result_no_components["svr_predicted_temp_c"] = None

        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=result_no_components,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["gpr_predicted_temp_c"] is None
        assert data["svr_predicted_temp_c"] is None

    @pytest.mark.asyncio
    async def test_temperature_confidence_interval_ordering(
        self, client: AsyncClient
    ) -> None:
        """Confidence interval is always ordered: lower < predicted < upper."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        data = resp.json()["data"]
        assert data["confidence_lower_c"] < data["predicted_temp_c"] < data[
            "confidence_upper_c"
        ]

    @pytest.mark.asyncio
    async def test_temperature_confidence_and_warnings(
        self, client: AsyncClient
    ) -> None:
        """Response includes confidence score and warnings array."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        data = resp.json()["data"]
        assert "confidence" in data
        assert 0.0 <= data["confidence"] <= 1.0
        assert "warnings" in data
        assert isinstance(data["warnings"], list)

    @pytest.mark.asyncio
    async def test_temperature_low_confidence_warning(
        self, client: AsyncClient
    ) -> None:
        """Low confidence generates warning in response."""
        low_conf_result = dict(TEMP_MODEL_RESULT)
        low_conf_result["confidence"] = 0.30
        low_conf_result["warnings"] = [
            {
                "code": "low_confidence_on_temperature",
                "message": "GPR std is high",
            }
        ]

        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=low_conf_result,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        data = resp.json()["data"]
        assert data["confidence"] == 0.30
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["code"] == "low_confidence_on_temperature"

    @pytest.mark.asyncio
    async def test_temperature_no_uncertainty_warning(
        self, client: AsyncClient
    ) -> None:
        """Default confidence when GPR std unavailable generates warning."""
        no_uncertainty_result = dict(TEMP_MODEL_RESULT)
        no_uncertainty_result["confidence"] = 0.5
        no_uncertainty_result["warnings"] = [
            {
                "code": "temperature_no_uncertainty",
                "message": "GPR uncertainty not available",
            }
        ]

        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=no_uncertainty_result,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        data = resp.json()["data"]
        assert data["confidence"] == 0.5
        assert len(data["warnings"]) == 1
        assert data["warnings"][0]["code"] == "temperature_no_uncertainty"


# ---------------------------------------------------------------------------
# Cross-cutting: method not allowed, wrong path
# ---------------------------------------------------------------------------


class TestPredictionEdgeCases:
    """Cross-cutting edge case tests for prediction endpoints."""

    @pytest.mark.asyncio
    async def test_get_method_not_allowed(self, client: AsyncClient) -> None:
        """GET requests to prediction endpoints return 405."""
        resp = await client.get("/api/v1/predict/phase")
        assert resp.status_code == 405

        resp = await client.get("/api/v1/predict/temperature")
        assert resp.status_code == 405

    @pytest.mark.asyncio
    async def test_nonexistent_predict_path(self, client: AsyncClient) -> None:
        """Nonexistent prediction paths return 404."""
        resp = await client.post(
            "/api/v1/predict/nonexistent", json=VALID_FEATURES
        )
        assert resp.status_code == 404
