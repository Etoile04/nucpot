"""Integration tests for POST /api/v1/predict/phase (NFM-1567).

Wraps the trained PhaseClassifier v1.0 into a FastAPI route. The tests
exercise the live HTTP path through the ``async_client`` fixture, which
loads the full app stack with SQLite overrides. The model artifact at
``models/phase_classifier_v1.0.0.joblib`` is loaded once per process and
cached by the dependency provider.

Cases covered:
- Happy path: U-10Mo -> H/M with high probability.
- Ternary composition: U-5Mo-7Zr.
- Pure U (no solute) -> H via Type-I prior.
- Cluster type is reported in the response payload.
- 8 physical feature names are returned.
- 422 for negative atomic fraction.
- 422 for empty composition dict.
- 422 for missing composition field.
- 503 when artifact path points to a missing file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import AsyncClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
ARTIFACT_PATH = REPO_ROOT / "models" / "phase_classifier_v1.0.0.joblib"

EXPECTED_PHYSICAL_FEATURES: frozenset[str] = frozenset(
    {
        "mo_equivalent",
        "pauling_chi_diff",
        "allen_chi_diff",
        "config_entropy",
        "bv_ratio",
        "u_density",
        "mixing_enthalpy",
        "lattice_distortion",
    },
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def predict_client(
    async_client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    """Wire the route's artifact-path dependency to the real trained model.

    The conftest's ``async_client`` fixture already sets up the app with
    SQLite and rate-limit overrides. We additionally override the
    ``get_artifact_path`` dependency used by the predict route.
    """
    from nfm_db.api.v1 import predict as predict_module

    app = async_client._transport.app  # noqa: SLF001
    app.dependency_overrides[predict_module.get_artifact_path] = (
        lambda: ARTIFACT_PATH
    )
    try:
        yield async_client
    finally:
        app.dependency_overrides.pop(
            predict_module.get_artifact_path, None,
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPredictPhaseHappyPath:
    """Successful inference for known compositions."""

    @pytest.mark.asyncio
    async def test_u10mo_returns_valid_prediction(
        self, predict_client: AsyncClient,
    ) -> None:
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={"composition": {"U": 0.90, "Mo": 0.10}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["success"] is True
        assert body["data"]["phase"] in {"H", "M"}
        assert "H" in body["data"]["probabilities"]
        assert "M" in body["data"]["probabilities"]
        assert (
            abs(
                body["data"]["probabilities"]["H"]
                + body["data"]["probabilities"]["M"]
                - 1.0,
            )
            < 1e-6
        )
        assert body["data"]["cluster_type"] in {"I", "II", "III", "IV"}
        assert isinstance(body["data"]["features"], dict)
        assert len(body["data"]["features"]) == 8
        predicted = body["data"]["phase"]
        assert body["data"]["probabilities"][predicted] > 0.5

    @pytest.mark.asyncio
    async def test_pure_u_returns_h(self, predict_client: AsyncClient) -> None:
        """Pure uranium defaults to Type I → H prior."""
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={"composition": {"U": 1.0}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["phase"] == "H"
        assert body["data"]["cluster_type"] == "I"

    @pytest.mark.asyncio
    async def test_ternary_composition(
        self, predict_client: AsyncClient,
    ) -> None:
        """U-5Mo-7Zr ternary composition is accepted."""
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={
                "composition": {"U": 0.88, "Mo": 0.05, "Zr": 0.07},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["phase"] in {"H", "M"}
        assert body["data"]["cluster_type"] in {"I", "II", "III", "IV"}

    @pytest.mark.asyncio
    async def test_response_includes_features_dict(
        self, predict_client: AsyncClient,
    ) -> None:
        """The response must include the 8 physical feature values."""
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={"composition": {"U": 0.90, "Mo": 0.10}},
        )
        assert resp.status_code == 200
        features = resp.json()["data"]["features"]
        assert EXPECTED_PHYSICAL_FEATURES.issubset(set(features.keys()))
        for value in features.values():
            assert isinstance(value, float)


# ---------------------------------------------------------------------------
# Validation errors (422)
# ---------------------------------------------------------------------------


class TestPredictPhaseValidation:
    """422 for malformed compositions."""

    @pytest.mark.asyncio
    async def test_negative_fraction_rejected(
        self, predict_client: AsyncClient,
    ) -> None:
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={"composition": {"U": 1.1, "Mo": -0.1}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_composition_rejected(
        self, predict_client: AsyncClient,
    ) -> None:
        resp = await predict_client.post(
            "/api/v1/predict/phase",
            json={"composition": {}},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_composition_field_rejected(
        self, predict_client: AsyncClient,
    ) -> None:
        resp = await predict_client.post(
            "/api/v1/predict/phase", json={},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Artifact missing (503)
# ---------------------------------------------------------------------------


class TestPredictPhaseArtifactMissing:
    """503 when the model artifact cannot be loaded."""

    @pytest.mark.asyncio
    async def test_missing_artifact_returns_503(
        self, async_client: AsyncClient, tmp_path: Path,
    ) -> None:
        from nfm_db.api.v1 import predict as predict_module

        app = async_client._transport.app  # noqa: SLF001
        app.dependency_overrides[predict_module.get_artifact_path] = (
            lambda: tmp_path / "missing.joblib"
        )
        try:
            resp = await async_client.post(
                "/api/v1/predict/phase",
                json={"composition": {"U": 0.90, "Mo": 0.10}},
            )
            assert resp.status_code == 503
            assert resp.json()["success"] is False
        finally:
            app.dependency_overrides.pop(
                predict_module.get_artifact_path, None,
            )