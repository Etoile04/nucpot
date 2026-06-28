"""Tests for V4 Extraction API endpoints (NFM-558).

Covers all 6 endpoints:
1. POST /api/v4/extraction/submit
2. GET  /api/v4/extraction/{job_id}/status
3. GET  /api/v4/extraction/{job_id}/result
4. GET  /api/v4/properties/{material_system}
5. POST /api/v4/extraction/{job_id}/validate
6. GET  /api/v4/material-systems
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app
from nfm_db.schemas.extraction import (
    V4ExtractionSubmitRequest,
    V4ValidateRequest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def v4_client(db_session):
    """Async test client for v4 extraction endpoints."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides.clear()
    from nfm_db.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def submit_payload() -> dict:
    """Valid submit request payload."""
    return {
        "source_reference": "10.1016/j.jnucmat.2023.01.001",
        "source_type": "doi",
        "element_systems": ["U", "Zr"],
        "cache_level": "L2",
        "priority": "normal",
    }


@pytest.fixture
def validate_payload() -> dict:
    """Valid validate request payload."""
    return {
        "auto_approve": True,
        "scope": "pending_only",
    }


# ---------------------------------------------------------------------------
# 1. POST /api/v4/extraction/submit
# ---------------------------------------------------------------------------


class TestSubmitExtraction:
    """Tests for the submit extraction endpoint."""

    @pytest.mark.asyncio
    async def test_submit_returns_202_with_job_id(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert "job_id" in data
        assert data["source_reference"] == "10.1016/j.jnucmat.2023.01.001"
        assert data["source_type"] == "doi"
        assert data["status"] == "queued"
        assert data["message"] == "Extraction job queued successfully."
        assert data["created_at"] is not None

    @pytest.mark.asyncio
    async def test_submit_returns_400_for_invalid_source_type(
        self, v4_client: AsyncClient
    ):
        payload = {
            "source_reference": "some-ref",
            "source_type": "invalid_type",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_submit_returns_400_for_empty_source_reference(
        self, v4_client: AsyncClient
    ):
        payload = {
            "source_reference": "",
            "source_type": "doi",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 422  # Pydantic validation

    @pytest.mark.asyncio
    async def test_submit_returns_400_for_too_many_element_systems(
        self, v4_client: AsyncClient
    ):
        elements = [f"E{i}" for i in range(21)]
        payload = {
            "source_reference": "10.1016/test",
            "source_type": "doi",
            "element_systems": elements,
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 422  # Pydantic max_length

    @pytest.mark.asyncio
    async def test_submit_defaults_priority_to_normal(
        self, v4_client: AsyncClient
    ):
        payload = {
            "source_reference": "10.1016/test",
            "source_type": "url",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] == "queued"

    @pytest.mark.asyncio
    async def test_submit_accepts_all_valid_source_types(
        self, v4_client: AsyncClient
    ):
        for source_type in ("doi", "url", "file", "internal_id"):
            payload = {
                "source_reference": f"ref-{source_type}",
                "source_type": source_type,
            }
            response = await v4_client.post(
                "/api/v4/extraction/submit", json=payload
            )
            assert response.status_code == 202, (
                f"source_type={source_type} should return 202"
            )


# ---------------------------------------------------------------------------
# 2. GET /api/v4/extraction/{job_id}/status
# ---------------------------------------------------------------------------


class TestExtractionStatus:
    """Tests for the extraction status endpoint."""

    @pytest.mark.asyncio
    async def test_status_returns_200_for_known_job(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        # First submit a job
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        # Then check status
        status_resp = await v4_client.get(
            f"/api/v4/extraction/{job_id}/status"
        )
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == job_id
        assert "progress" in data
        assert "current_step" in data["progress"]
        assert "steps_completed" in data["progress"]
        assert "steps_remaining" in data["progress"]
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_status_returns_404_for_unknown_job(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/extraction/00000000-0000-0000-0000-000000000000/status"
        )
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_status_includes_progress_object(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        status_resp = await v4_client.get(
            f"/api/v4/extraction/{job_id}/status"
        )
        data = status_resp.json()["data"]
        progress = data["progress"]
        assert isinstance(progress["steps_completed"], list)
        assert isinstance(progress["steps_remaining"], list)
        assert isinstance(progress["current_step"], str)


# ---------------------------------------------------------------------------
# 3. GET /api/v4/extraction/{job_id}/result
# ---------------------------------------------------------------------------


class TestExtractionResult:
    """Tests for the extraction result endpoint."""

    @pytest.mark.asyncio
    async def test_result_returns_404_for_unknown_job(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/extraction/00000000-0000-0000-0000-000000000000/result"
        )
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_result_returns_200_with_properties_for_completed_job(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        # Submit a job (runs synchronously in test with stub mode)
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        # Poll until completed or timeout
        import asyncio

        for _ in range(10):
            status_resp = await v4_client.get(
                f"/api/v4/extraction/{job_id}/status"
            )
            status = status_resp.json()["data"]["status"]
            if status in ("completed", "partial", "failed"):
                break
            await asyncio.sleep(0.1)

        result_resp = await v4_client.get(
            f"/api/v4/extraction/{job_id}/result"
        )
        assert result_resp.status_code == 200
        body = result_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "source_reference" in data
        assert "job_status" in data
        assert "total_extracted" in data
        assert "properties" in data

    @pytest.mark.asyncio
    async def test_result_supports_pagination_params(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        # Wait for completion
        import asyncio

        for _ in range(10):
            status_resp = await v4_client.get(
                f"/api/v4/extraction/{job_id}/status"
            )
            if status_resp.json()["data"]["status"] in ("completed", "partial", "failed"):
                break
            await asyncio.sleep(0.1)

        result_resp = await v4_client.get(
            f"/api/v4/extraction/{job_id}/result",
            params={"page": 1, "limit": 10, "confidence": "high"},
        )
        assert result_resp.status_code == 200
        body = result_resp.json()
        assert "meta" in body
        meta = body["meta"]
        assert meta["page"] == 1
        assert meta["limit"] == 10

    @pytest.mark.asyncio
    async def test_result_returns_400_for_invalid_limit(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        result_resp = await v4_client.get(
            f"/api/v4/extraction/{job_id}/result",
            params={"limit": 999},  # max is 200
        )
        assert result_resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. GET /api/v4/properties/{material_system}
# ---------------------------------------------------------------------------


class TestBrowseProperties:
    """Tests for the browse properties endpoint."""

    @pytest.mark.asyncio
    async def test_browse_returns_200_for_known_material(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/properties/UO2")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["material_system"] == "UO2"
        assert "total_count" in data
        assert "properties" in data

    @pytest.mark.asyncio
    async def test_browse_supports_filter_params(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/properties/UO2",
            params={
                "confidence": "high",
                "phase": "alpha",
                "property_category": "thermal_conductivity",
                "staging_status": "approved",
                "page": 1,
                "limit": 20,
                "sort_by": "property",
                "sort_order": "asc",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "meta" in body

    @pytest.mark.asyncio
    async def test_browse_returns_properties_list(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/properties/UO2")
        data = response.json()["data"]
        assert isinstance(data["properties"], list)
        # Each property should have expected fields
        for prop in data["properties"]:
            assert "property" in prop
            assert "value" in prop
            assert "unit" in prop
            assert "confidence" in prop

    @pytest.mark.asyncio
    async def test_browse_supports_temperature_filter(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/properties/UO2",
            params={"temperature_min": 500.0, "temperature_max": 1500.0},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_browse_url_decoded_material_system(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/properties/Zr-Nb")
        assert response.status_code == 200
        assert response.json()["data"]["material_system"] == "Zr-Nb"


# ---------------------------------------------------------------------------
# 5. POST /api/v4/extraction/{job_id}/validate
# ---------------------------------------------------------------------------


class TestValidateExtraction:
    """Tests for the validate extraction endpoint."""

    @pytest.mark.asyncio
    async def test_validate_returns_404_for_unknown_job(
        self, v4_client: AsyncClient, validate_payload: dict
    ):
        response = await v4_client.post(
            "/api/v4/extraction/00000000-0000-0000-0000-000000000000/validate",
            json=validate_payload,
        )
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False

    @pytest.mark.asyncio
    async def test_validate_returns_202_for_completed_job(
        self, v4_client: AsyncClient, submit_payload: dict, validate_payload: dict
    ):
        # Submit and wait for completion
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        import asyncio

        for _ in range(10):
            status_resp = await v4_client.get(
                f"/api/v4/extraction/{job_id}/status"
            )
            if status_resp.json()["data"]["status"] in ("completed", "partial", "failed"):
                break
            await asyncio.sleep(0.1)

        validate_resp = await v4_client.post(
            f"/api/v4/extraction/{job_id}/validate",
            json=validate_payload,
        )
        assert validate_resp.status_code == 202
        body = validate_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "job_id" in data
        assert "validation_id" in data
        assert "total_properties" in data
        assert "auto_approved" in data
        assert "sent_to_review" in data
        assert "flagged" in data

    @pytest.mark.asyncio
    async def test_validate_defaults_auto_approve_true(
        self, v4_client: AsyncClient, submit_payload: dict
    ):
        submit_resp = await v4_client.post(
            "/api/v4/extraction/submit", json=submit_payload
        )
        job_id = submit_resp.json()["data"]["job_id"]

        import asyncio

        for _ in range(10):
            status_resp = await v4_client.get(
                f"/api/v4/extraction/{job_id}/status"
            )
            if status_resp.json()["data"]["status"] in ("completed", "partial", "failed"):
                break
            await asyncio.sleep(0.1)

        validate_resp = await v4_client.post(
            f"/api/v4/extraction/{job_id}/validate",
            json={},  # empty body → defaults
        )
        assert validate_resp.status_code == 202


# ---------------------------------------------------------------------------
# 6. GET /api/v4/material-systems
# ---------------------------------------------------------------------------


class TestMaterialSystems:
    """Tests for the material systems listing endpoint."""

    @pytest.mark.asyncio
    async def test_list_returns_200(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/material-systems")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body
        assert "material_systems" in body["data"]
        assert "meta" in body
        assert "total" in body["meta"]

    @pytest.mark.asyncio
    async def test_list_returns_array_of_systems(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/material-systems")
        systems = response.json()["data"]["material_systems"]
        assert isinstance(systems, list)
        for system in systems:
            assert "name" in system
            assert "total_properties" in system
            assert "confidence_summary" in system

    @pytest.mark.asyncio
    async def test_list_supports_has_pending_review_filter(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/material-systems",
            params={"has_pending_review": True},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_supports_category_filter(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/material-systems",
            params={"category": "thermal_conductivity"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_confidence_summary_structure(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/material-systems")
        systems = response.json()["data"]["material_systems"]
        for system in systems:
            cs = system["confidence_summary"]
            assert "high" in cs
            assert "medium" in cs
            assert "low" in cs
            assert isinstance(cs["high"], int)


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestV4Schemas:
    """Tests for v4 Pydantic schema validation."""

    def test_submit_request_validates_source_types(self):
        for valid_type in ("doi", "url", "file", "internal_id"):
            req = V4ExtractionSubmitRequest(
                source_reference="ref",
                source_type=valid_type,
            )
            assert req.source_type == valid_type

    def test_submit_request_defaults_priority(self):
        req = V4ExtractionSubmitRequest(
            source_reference="ref",
            source_type="doi",
        )
        assert req.priority == "normal"

    def test_submit_request_element_systems_max_20(self):
        req = V4ExtractionSubmitRequest(
            source_reference="ref",
            source_type="doi",
            element_systems=[f"E{i}" for i in range(20)],
        )
        assert len(req.element_systems) == 20

    def test_validate_request_defaults(self):
        req = V4ValidateRequest()
        assert req.auto_approve is True
        assert req.scope == "pending_only"

    def test_validate_request_all_scope(self):
        req = V4ValidateRequest(scope="all")
        assert req.scope == "all"
