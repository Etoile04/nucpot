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

import asyncio

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


@pytest.fixture
async def submitted_job_id(v4_client: AsyncClient, submit_payload: dict) -> str:
    """Submit a job and return the job_id (waits for completion)."""
    resp = await v4_client.post("/api/v4/extraction/submit", json=submit_payload)
    job_id = resp.json()["data"]["job_id"]
    # Poll until terminal state
    for _ in range(20):
        status_resp = await v4_client.get(f"/api/v4/extraction/{job_id}/status")
        status = status_resp.json()["data"]["status"]
        if status in ("completed", "partial", "failed"):
            break
        await asyncio.sleep(0.05)
    return job_id


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
        assert data["status"] in ("completed", "partial", "queued")
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
        assert "Invalid source_type" in body["error"]

    @pytest.mark.asyncio
    async def test_submit_returns_422_for_empty_source_reference(
        self, v4_client: AsyncClient
    ):
        payload = {
            "source_reference": "",
            "source_type": "doi",
        }
        response = await v4_client.post(
            "/api/v4/extraction/submit", json=payload
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_returns_422_for_too_many_element_systems(
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
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_accepts_minimal_payload(
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
        assert data["job_id"]

    @pytest.mark.asyncio
    async def test_submit_accepts_all_valid_source_types(
        self, v4_client: AsyncClient
    ):
        # Map each source_type to a representative valid source_reference.
        # DOI must match the regex guard added in NFM-632.
        refs = {
            "doi": "10.1016/j.nucengdes.2020.110756",
            "url": "https://example.com/paper",
            "file": "ref-file",
            "internal_id": "ref-internal_id",
        }
        for source_type in ("doi", "url", "file", "internal_id"):
            payload = {
                "source_reference": refs[source_type],
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
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        status_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/status"
        )
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == submitted_job_id
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
        assert "not found" in body["error"]

    @pytest.mark.asyncio
    async def test_status_includes_progress_object(
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        status_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/status"
        )
        data = status_resp.json()["data"]
        progress = data["progress"]
        assert isinstance(progress["steps_completed"], list)
        assert isinstance(progress["steps_remaining"], list)
        assert isinstance(progress["current_step"], str)

    @pytest.mark.asyncio
    async def test_status_shows_extracted_count(
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        status_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/status"
        )
        data = status_resp.json()["data"]
        assert "extracted_count" in data
        assert isinstance(data["extracted_count"], int)


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
        assert "not found" in body["error"]

    @pytest.mark.asyncio
    async def test_result_returns_409_for_non_completed_job(
        self, v4_client: AsyncClient
    ):
        """Accessing results on a running job must return 409 Conflict."""
        from nfm_db.services.extraction_pipeline import (
            ExtractionJob,
            JobStatus,
            _job_store,
        )

        job_id = "test-409-result-job"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="test://ref",
            source_type="url",
            status=JobStatus.RUNNING,
        )
        try:
            response = await v4_client.get(
                f"/api/v4/extraction/{job_id}/result"
            )
            assert response.status_code == 409
            body = response.json()
            assert body["success"] is False
            assert "not 'completed'" in body["error"]
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_returns_200_for_completed_job(
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        result_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/result"
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
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        result_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/result",
            params={"page": 1, "limit": 10, "confidence": "high"},
        )
        assert result_resp.status_code == 200
        body = result_resp.json()
        assert "meta" in body
        meta = body["meta"]
        assert meta["page"] == 1
        assert meta["limit"] == 10

    @pytest.mark.asyncio
    async def test_result_returns_422_for_invalid_limit(
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        result_resp = await v4_client.get(
            f"/api/v4/extraction/{submitted_job_id}/result",
            params={"limit": 999},
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

    @pytest.mark.asyncio
    async def test_browse_returns_400_for_invalid_sort_by(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/properties/UO2",
            params={"sort_by": "invalid_field"},
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_browse_returns_400_for_invalid_sort_order(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/properties/UO2",
            params={"sort_order": "invalid"},
        )
        assert response.status_code == 400
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_browse_meta_includes_filters(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get(
            "/api/v4/properties/UO2",
            params={"confidence": "high", "phase": "alpha"},
        )
        meta = response.json()["meta"]
        assert "filters" in meta
        assert meta["filters"]["confidence"] == "high"
        assert meta["filters"]["phase"] == "alpha"


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
        assert "not found" in body["error"]

    @pytest.mark.asyncio
    async def test_validate_returns_409_for_non_completed_job(
        self, v4_client: AsyncClient, validate_payload: dict
    ):
        """Triggering validation on a non-completed job must return 409 Conflict."""
        from nfm_db.services.extraction_pipeline import (
            ExtractionJob,
            JobStatus,
            _job_store,
        )

        job_id = "test-409-validate-job"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="test://ref",
            source_type="url",
            status=JobStatus.EXTRACTING,
        )
        try:
            response = await v4_client.post(
                f"/api/v4/extraction/{job_id}/validate",
                json=validate_payload,
            )
            assert response.status_code == 409
            body = response.json()
            assert body["success"] is False
            assert "not 'completed'" in body["error"]
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_validate_returns_202_for_completed_job(
        self, v4_client: AsyncClient, submitted_job_id: str, validate_payload: dict
    ):
        validate_resp = await v4_client.post(
            f"/api/v4/extraction/{submitted_job_id}/validate",
            json=validate_payload,
        )
        assert validate_resp.status_code == 202
        body = validate_resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == submitted_job_id
        assert "validation_id" in data
        assert "total_properties" in data
        assert "auto_approved" in data
        assert "sent_to_review" in data
        assert "flagged" in data

    @pytest.mark.asyncio
    async def test_validate_defaults_when_empty_body(
        self, v4_client: AsyncClient, submitted_job_id: str
    ):
        validate_resp = await v4_client.post(
            f"/api/v4/extraction/{submitted_job_id}/validate",
            json={},
        )
        assert validate_resp.status_code == 202
        data = validate_resp.json()["data"]
        assert data["job_id"] == submitted_job_id

    @pytest.mark.asyncio
    async def test_validate_includes_review_url(
        self, v4_client: AsyncClient, submitted_job_id: str, validate_payload: dict
    ):
        validate_resp = await v4_client.post(
            f"/api/v4/extraction/{submitted_job_id}/validate",
            json=validate_payload,
        )
        data = validate_resp.json()["data"]
        assert "review_url" in data
        assert "/admin/v4-extraction/validate/" in data["review_url"]


# ---------------------------------------------------------------------------
# 6. GET /api/v4/material-systems
# ---------------------------------------------------------------------------


class TestMaterialSystems:
    """Tests for the material systems listing endpoint."""

    @pytest.mark.asyncio
    async def test_list_returns_200(self, v4_client: AsyncClient):
        response = await v4_client.get("/api/v4/material-systems")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body
        assert "material_systems" in body["data"]
        assert "meta" in body
        assert "total" in body["meta"]

    @pytest.mark.asyncio
    async def test_list_returns_array_of_systems(self, v4_client: AsyncClient):
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

    @pytest.mark.asyncio
    async def test_list_material_system_has_required_fields(
        self, v4_client: AsyncClient
    ):
        response = await v4_client.get("/api/v4/material-systems")
        systems = response.json()["data"]["material_systems"]
        for system in systems:
            assert "name" in system
            assert "display_name" in system
            assert "total_properties" in system
            assert "categories" in system
            assert "pending_review_count" in system


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


# ---------------------------------------------------------------------------
# Multimodal field wiring tests (NFM-1197)
# ---------------------------------------------------------------------------


class TestMultimodalSubmitWiring:
    """Tests that multimodal extraction options are passed through submit."""

    @pytest.mark.asyncio
    async def test_submit_passes_all_multimodal_options(
        self, v4_client: AsyncClient
    ):
        """POST submit with all 5 multimodal fields stores them on the job."""
        payload = {
            "source_reference": "10.1016/j.nucmat.2024.01.001",
            "source_type": "doi",
            "extract_figures": True,
            "extract_tables": True,
            "figure_types": ["line", "scatter"],
            "confidence_threshold": 0.8,
            "conflict_strategy": "merge",
        }
        resp = await v4_client.post("/api/v4/extraction/submit", json=payload)
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]

        # Verify the job was created with multimodal options stored
        from nfm_db.services.extraction_pipeline import get_job

        job = get_job(job_id)
        assert job is not None
        assert job.extract_figures is True
        assert job.extract_tables is True
        assert job.figure_types == ["line", "scatter"]
        assert job.confidence_threshold == 0.8
        assert job.conflict_strategy == "merge"

    @pytest.mark.asyncio
    async def test_submit_defaults_multimodal_fields(
        self, v4_client: AsyncClient
    ):
        """Omitting multimodal fields uses correct defaults."""
        payload = {
            "source_reference": "10.1016/j.test.2024.01.001",
            "source_type": "doi",
        }
        resp = await v4_client.post("/api/v4/extraction/submit", json=payload)
        assert resp.status_code == 202
        job_id = resp.json()["data"]["job_id"]

        from nfm_db.services.extraction_pipeline import get_job

        job = get_job(job_id)
        assert job is not None
        assert job.extract_figures is False
        assert job.extract_tables is False
        assert job.figure_types is None
        assert job.confidence_threshold == 0.5
        assert job.conflict_strategy == "prefer_vlm"


class TestMultimodalResultWiring:
    """Tests that figures and tables are returned in result endpoint."""

    @pytest.mark.asyncio
    async def test_result_includes_figures_and_tables(
        self, v4_client: AsyncClient
    ):
        """GET result returns populated figures[] and tables[] from job data."""
        from nfm_db.services.extraction_pipeline import (
            ExtractionJob,
            JobStatus,
            _job_store,
        )

        job_id = "test-multimodal-result-populated"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="test://multimodal",
            source_type="url",
            status=JobStatus.COMPLETED,
            figures=[
                {
                    "page_number": 3,
                    "source_file": "paper.pdf",
                    "extraction": {
                        "figure_type": "line",
                        "title": "Thermal Conductivity vs Temperature",
                        "description": "Measured data for UO2",
                        "confidence": 0.92,
                        "data_points": [],
                    },
                },
            ],
            tables=[
                {
                    "page_number": 5,
                    "source_file": "paper.pdf",
                    "table_data": {
                        "caption": "Property Summary",
                        "headers": {
                            "columns": ["Property", "Value", "Unit"],
                        },
                        "rows": [
                            [
                                {"value": "Density"},
                                {"value": "10.5"},
                                {"value": "g/cm³"},
                            ]
                        ],
                    },
                },
            ],
        )
        try:
            resp = await v4_client.get(f"/api/v4/extraction/{job_id}/result")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert "figures" in data
            assert "tables" in data
            assert len(data["figures"]) == 1
            assert data["figures"][0]["page_number"] == 3
            assert len(data["tables"]) == 1
            assert data["tables"][0]["page_number"] == 5
        finally:
            _job_store.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_result_returns_empty_figures_tables_when_absent(
        self, v4_client: AsyncClient
    ):
        """GET result returns empty arrays when job has no figures/tables (backward compat)."""
        from nfm_db.services.extraction_pipeline import (
            ExtractionJob,
            JobStatus,
            _job_store,
        )

        job_id = "test-multimodal-result-empty"
        _job_store[job_id] = ExtractionJob(
            job_id=job_id,
            source_reference="test://no-multimodal",
            source_type="url",
            status=JobStatus.COMPLETED,
        )
        try:
            resp = await v4_client.get(f"/api/v4/extraction/{job_id}/result")
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["figures"] == []
            assert data["tables"] == []
        finally:
            _job_store.pop(job_id, None)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestV4Helpers:
    """Tests for internal helper functions."""

    def test_build_progress_for_queued(self):
        from nfm_db.api.v4.extraction import _build_progress
        from nfm_db.services.extraction_pipeline import JobStatus

        progress = _build_progress(JobStatus.QUEUED)
        assert progress.current_step == "queued"
        assert progress.steps_completed == []
        assert progress.steps_remaining == [
            "running", "extracting", "mapping", "quality_gate", "completed",
        ]

    def test_build_progress_for_completed(self):
        from nfm_db.api.v4.extraction import _build_progress
        from nfm_db.services.extraction_pipeline import JobStatus

        progress = _build_progress(JobStatus.COMPLETED)
        assert progress.current_step == "completed"
        assert "quality_gate" in progress.steps_completed
        assert progress.steps_remaining == []

    def test_build_progress_for_extracting(self):
        from nfm_db.api.v4.extraction import _build_progress
        from nfm_db.services.extraction_pipeline import JobStatus

        progress = _build_progress(JobStatus.EXTRACTING)
        assert progress.current_step == "extracting"
        assert "queued" in progress.steps_completed
        assert "running" in progress.steps_completed
        assert "completed" in progress.steps_remaining

    def test_to_v4_property_converts_basic_fields(self):
        from nfm_db.api.v4.extraction import _to_v4_property

        prop = {
            "property": "thermal_conductivity",
            "value": "8.5",
            "unit": "W/(m·K)",
            "confidence": "high",
            "material_name": "UO2",
        }
        result = _to_v4_property(prop, job_id="test-job")
        assert result.property == "thermal_conductivity"
        assert result.value == "8.5"
        assert result.unit == "W/(m·K)"
        assert result.confidence == "high"
        assert result.material_name == "UO2"
        assert result.job_id == "test-job"

    def test_to_v4_property_builds_conditions_from_flat(self):
        from nfm_db.api.v4.extraction import _to_v4_property

        prop = {
            "property": "density",
            "value": "10.5",
            "unit": "g/cm³",
            "temperature": 300.0,
            "method": "measurement",
        }
        result = _to_v4_property(prop)
        assert result.conditions is not None
        assert result.conditions["temperature"] == "300.0"
        assert result.conditions["method"] == "measurement"

    def test_to_v4_property_defaults_confidence(self):
        from nfm_db.api.v4.extraction import _to_v4_property

        prop = {
            "property": "density",
            "value": "10.5",
            "unit": "g/cm³",
        }
        result = _to_v4_property(prop)
        assert result.confidence == "medium"

    def test_error_response_format(self):
        from nfm_db.api.v4.extraction import _error_response

        resp = _error_response(404, "Job not found")
        assert resp.status_code == 404
        assert resp.body is not None  # type: ignore[union-attr]
        import json

        body = json.loads(resp.body.decode())
        assert body["success"] is False
        assert body["error"] == "Job not found"
        assert body["data"] is None
        assert body["meta"] is None
