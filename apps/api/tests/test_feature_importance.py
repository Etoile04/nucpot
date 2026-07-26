"""Tests for feature importance computation and API integration (NFM-1790).

Covers:
- feature_importance.py: compute_permutation_importance, get_cached_importance
- prediction_service.py: include_importance parameter
- API endpoints: ?importance=true query parameter
- Schema validation: feature_importance field
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app
from nfm_db.ml.feature_importance import (
    compute_permutation_importance,
    get_cached_importance,
)

# ---------------------------------------------------------------------------
# Unit tests: feature_importance.py
# ---------------------------------------------------------------------------


class TestComputePermutationImportance:
    """Tests for compute_permutation_importance function."""

    def test_returns_dict_of_feature_importances(self) -> None:
        """Returns a dict mapping feature names to float importance values."""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([1, 0, 1, 0])

        X = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]])
        y = np.array([1, 0, 1, 0])
        feature_names = ["feature_a", "feature_b"]

        mock_perm_result = MagicMock()
        mock_perm_result.importances_mean = np.array([0.25, 0.10])

        with patch(
            "nfm_db.ml.feature_importance.permutation_importance",
            return_value=mock_perm_result,
        ):
            result = compute_permutation_importance(
                mock_model, X, y, feature_names
            )

        assert isinstance(result, dict)
        assert "feature_a" in result
        assert "feature_b" in result
        assert result["feature_a"] == 0.25
        assert result["feature_b"] == 0.1

    def test_importances_rounded_to_4_decimals(self) -> None:
        """Importance values are rounded to 4 decimal places."""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([1, 0])

        X = np.array([[1.0], [2.0]])
        y = np.array([1, 0])
        feature_names = ["feat"]

        mock_perm_result = MagicMock()
        mock_perm_result.importances_mean = np.array([0.123456789])

        with patch(
            "nfm_db.ml.feature_importance.permutation_importance",
            return_value=mock_perm_result,
        ):
            result = compute_permutation_importance(
                mock_model, X, y, feature_names
            )

        assert result["feat"] == 0.1235

    def test_uses_n_repeats_and_random_state(self) -> None:
        """Passes n_repeats=10 and random_state=42 to permutation_importance."""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([1, 0])

        X = np.array([[1.0], [2.0]])
        y = np.array([1, 0])
        feature_names = ["feat"]

        mock_perm_result = MagicMock()
        mock_perm_result.importances_mean = np.array([0.5])

        with patch(
            "nfm_db.ml.feature_importance.permutation_importance",
            return_value=mock_perm_result,
        ) as mock_perm:
            compute_permutation_importance(mock_model, X, y, feature_names)

            mock_perm.assert_called_once()
            call_kwargs = mock_perm.call_args
            assert call_kwargs.kwargs.get("n_repeats") == 10
            assert call_kwargs.kwargs.get("random_state") == 42


class TestGetCachedImportance:
    """Tests for get_cached_importance function."""

    def test_returns_cached_importance_from_file(self, tmp_path: Path) -> None:
        """Loads and returns importance dict from .importance.json sidecar file."""
        model_file = tmp_path / "model.joblib"
        model_file.touch()

        cache_data = {"feature_a": 0.3, "feature_b": 0.15}
        cache_file = tmp_path / "model.importance.json"
        cache_file.write_text(json.dumps(cache_data))

        result = get_cached_importance(str(model_file), ["feature_a", "feature_b"])

        assert result == {"feature_a": 0.3, "feature_b": 0.15}

    def test_returns_empty_dict_when_no_cache_file(self, tmp_path: Path) -> None:
        """Returns empty dict when .importance.json does not exist."""
        model_file = tmp_path / "model.joblib"
        model_file.touch()

        result = get_cached_importance(str(model_file), ["feature_a"])

        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, tmp_path: Path) -> None:
        """Returns empty dict when cache file contains invalid JSON."""
        model_file = tmp_path / "model.joblib"
        model_file.touch()

        cache_file = tmp_path / "model.importance.json"
        cache_file.write_text("not valid json {")

        result = get_cached_importance(str(model_file), ["feature_a"])

        assert result == {}


# ---------------------------------------------------------------------------
# API endpoint tests: ?importance=true query parameter
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

PHASE_MODEL_RESULT_WITH_IMPORTANCE = {
    "predicted_phase": "single_phase",
    "predicted_phase_label": "single phase",
    "probabilities": [
        {"class": "single_phase", "probability": 0.88},
        {"class": "multi_phase", "probability": 0.12},
    ],
    "confidence": 0.88,
    "warnings": [],
    "model_version": "v1.0",
    "feature_importance": {
        "mo_equivalent": 0.35,
        "pauling_chi_diff": 0.12,
        "allen_chi_diff": 0.08,
        "config_entropy": 0.05,
        "bv_ratio": 0.15,
        "u_density": 0.10,
        "mixing_enthalpy": 0.08,
        "lattice_distortion": 0.07,
    },
}

PHASE_MODEL_RESULT_WITHOUT_IMPORTANCE = {
    "predicted_phase": "single_phase",
    "predicted_phase_label": "single phase",
    "probabilities": [
        {"class": "single_phase", "probability": 0.88},
        {"class": "multi_phase", "probability": 0.12},
    ],
    "confidence": 0.88,
    "warnings": [],
    "model_version": "v1.0",
    "feature_importance": None,
}

TEMP_MODEL_RESULT_WITH_IMPORTANCE = {
    "predicted_temp_c": 620.5,
    "confidence_lower_c": 595.0,
    "confidence_upper_c": 646.0,
    "gpr_predicted_temp_c": 615.0,
    "svr_predicted_temp_c": 626.0,
    "confidence": 0.72,
    "warnings": [],
    "model_version": "v1.0",
    "feature_importance": {
        "mo_equivalent": 0.30,
        "pauling_chi_diff": 0.10,
        "allen_chi_diff": 0.06,
        "config_entropy": 0.04,
        "bv_ratio": 0.18,
        "u_density": 0.14,
        "mixing_enthalpy": 0.10,
        "lattice_distortion": 0.08,
    },
}

TEMP_MODEL_RESULT_WITHOUT_IMPORTANCE = {
    "predicted_temp_c": 620.5,
    "confidence_lower_c": 595.0,
    "confidence_upper_c": 646.0,
    "gpr_predicted_temp_c": 615.0,
    "svr_predicted_temp_c": 626.0,
    "confidence": 0.72,
    "warnings": [],
    "model_version": "v1.0",
    "feature_importance": None,
}


@pytest.fixture
async def client():
    """Create an async test client for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestPhaseEndpointImportance:
    """Tests for POST /api/v1/predict/phase with importance parameter."""

    @pytest.mark.asyncio
    async def test_phase_importance_true_returns_importance(
        self, client: AsyncClient
    ) -> None:
        """?importance=true includes feature_importance in response."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT_WITH_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/phase?importance=true", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is not None
        assert "mo_equivalent" in data["feature_importance"]
        assert isinstance(data["feature_importance"]["mo_equivalent"], float)

    @pytest.mark.asyncio
    async def test_phase_importance_false_returns_null(
        self, client: AsyncClient
    ) -> None:
        """?importance=false returns null feature_importance."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT_WITHOUT_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/phase?importance=false", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is None

    @pytest.mark.asyncio
    async def test_phase_no_importance_param_returns_null(
        self, client: AsyncClient
    ) -> None:
        """Without ?importance param, feature_importance is null."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT_WITHOUT_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/phase", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is None

    @pytest.mark.asyncio
    async def test_phase_importance_values_are_floats(
        self, client: AsyncClient
    ) -> None:
        """All feature_importance values are floats within reasonable range."""
        with patch(
            "nfm_db.api.v1.prediction.predict_phase",
            return_value=PHASE_MODEL_RESULT_WITH_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/phase?importance=true", json=VALID_FEATURES
            )

        data = resp.json()["data"]
        for feature_name, value in data["feature_importance"].items():
            assert isinstance(value, float), f"{feature_name}: {type(value)}"
            assert 0.0 <= value <= 1.0, f"{feature_name}: {value}"


class TestTemperatureEndpointImportance:
    """Tests for POST /api/v1/predict/temperature with importance parameter."""

    @pytest.mark.asyncio
    async def test_temperature_importance_true_returns_importance(
        self, client: AsyncClient
    ) -> None:
        """?importance=true includes feature_importance in temperature response."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT_WITH_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature?importance=true",
                json=VALID_FEATURES,
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is not None
        assert "mo_equivalent" in data["feature_importance"]

    @pytest.mark.asyncio
    async def test_temperature_no_importance_param_returns_null(
        self, client: AsyncClient
    ) -> None:
        """Without ?importance param, feature_importance is null."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT_WITHOUT_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature", json=VALID_FEATURES
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is None

    @pytest.mark.asyncio
    async def test_temperature_importance_false_returns_null(
        self, client: AsyncClient
    ) -> None:
        """?importance=false returns null feature_importance."""
        with patch(
            "nfm_db.api.v1.prediction.predict_temperature",
            return_value=TEMP_MODEL_RESULT_WITHOUT_IMPORTANCE,
        ):
            resp = await client.post(
                "/api/v1/predict/temperature?importance=false",
                json=VALID_FEATURES,
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["feature_importance"] is None
