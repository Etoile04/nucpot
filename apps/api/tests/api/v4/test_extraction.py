"""Integration tests for /api/v4/extraction endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from nfm_db.services.extraction_pipeline import (
    ExtractionJob,
    JobStatus,
    _job_store,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    status: JobStatus = JobStatus.QUEUED,
    job_id: str | None = None,
    source_reference: str = "10.1016/j.nucengdes.2020.110756",
    source_type: str = "doi",
    extracted_count: int = 0,
    staged_count: int = 0,
    rejected_count: int = 0,
    error_message: str | None = None,
    fill_batch_id: str | None = None,
    figures: list[dict] | None = None,
    tables: list[dict] | None = None,
) -> ExtractionJob:
    now = datetime.now(UTC)
    return ExtractionJob(
        job_id=job_id or str(uuid.uuid4()),
        source_reference=source_reference,
        source_type=source_type,
        status=status,
        fill_batch_id=fill_batch_id or str(uuid.uuid4()),
        extracted_count=extracted_count,
        staged_count=staged_count,
        rejected_count=rejected_count,
        error_message=error_message,
        created_at=now,
        started_at=now if status != JobStatus.QUEUED else None,
        completed_at=now if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PARTIAL) else None,
        figures=figures or [],
        tables=tables or [],
    )


def _cleanup_job(job_id: str) -> None:
    _job_store.pop(job_id, None)


# ---------------------------------------------------------------------------
# 1. POST /api/v4/extraction/submit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_extraction_success(async_client) -> None:
    """Happy path: valid DOI submission returns 202 with job_id."""
    job = _make_job(status=JobStatus.QUEUED)

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ):
        response = await async_client.post(
            "/api/v4/extraction/submit",
            json={
                "source_reference": "10.1016/j.nucengdes.2020.110756",
                "source_type": "doi",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == job.job_id
    assert data["source_reference"] == job.source_reference
    assert data["source_type"] == "doi"
    assert data["status"] == "queued"
    assert "message" in data


@pytest.mark.asyncio
async def test_submit_extraction_with_multimodal_options(async_client) -> None:
    """Submit with figure/table extraction options forwarded to service."""
    job = _make_job(status=JobStatus.QUEUED)

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ) as mock_trigger:
        response = await async_client.post(
            "/api/v4/extraction/submit",
            json={
                "source_reference": "10.1016/j.nucengdes.2020.110756",
                "source_type": "doi",
                "extract_figures": True,
                "extract_tables": True,
                "figure_types": ["line", "scatter"],
                "confidence_threshold": 0.7,
                "conflict_strategy": "merge",
            },
        )

    assert response.status_code == 202
    call_kwargs = mock_trigger.call_args[1]
    assert call_kwargs["extract_figures"] is True
    assert call_kwargs["extract_tables"] is True
    assert call_kwargs["figure_types"] == ["line", "scatter"]
    assert call_kwargs["confidence_threshold"] == 0.7
    assert call_kwargs["conflict_strategy"] == "merge"


@pytest.mark.asyncio
async def test_submit_extraction_url_source(async_client) -> None:
    """Submit extraction with url source_type."""
    job = _make_job(source_type="url", source_reference="https://example.com/paper.pdf")

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ):
        response = await async_client.post(
            "/api/v4/extraction/submit",
            json={
                "source_reference": "https://example.com/paper.pdf",
                "source_type": "url",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["source_type"] == "url"


@pytest.mark.asyncio
async def test_submit_extraction_invalid_source_type(async_client) -> None:
    """400 when source_type is not one of the accepted values."""
    response = await async_client.post(
        "/api/v4/extraction/submit",
        json={
            "source_reference": "some-ref",
            "source_type": "invalid_type",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid source_type" in body["error"]


@pytest.mark.asyncio
async def test_submit_extraction_empty_source_reference(async_client) -> None:
    """400 when source_reference is whitespace-only."""
    response = await async_client.post(
        "/api/v4/extraction/submit",
        json={
            "source_reference": "   ",
            "source_type": "doi",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "empty" in body["error"].lower()


@pytest.mark.asyncio
async def test_submit_extraction_invalid_doi_format(async_client) -> None:
    """400 when source_type is doi but source_reference is not a valid DOI."""
    response = await async_client.post(
        "/api/v4/extraction/submit",
        json={
            "source_reference": "not-a-valid-doi",
            "source_type": "doi",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "DOI" in body["error"] or "doi" in body["error"].lower()


@pytest.mark.asyncio
async def test_submit_extraction_valid_doi_formats(async_client) -> None:
    """Various valid DOI formats are accepted (no 400 from DOI check)."""
    valid_dois = [
        "10.1016/j.nucengdes.2020.110756",
        "10.1016/j.test.2024.12345",
        "10.1234/some.identifier",
        "10.12345/with-dash",
    ]
    job = _make_job()

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ):
        for doi in valid_dois:
            response = await async_client.post(
                "/api/v4/extraction/submit",
                json={
                    "source_reference": doi,
                    "source_type": "doi",
                },
            )
            assert response.status_code == 202, (
                f"DOI '{doi}' should be accepted but got {response.status_code}"
            )


@pytest.mark.asyncio
async def test_submit_extraction_doi_check_skipped_for_non_doi(async_client) -> None:
    """DOI format check is skipped when source_type is not 'doi'."""
    job = _make_job(source_type="file")

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ):
        # A non-DOI-like string is fine for file source_type
        response = await async_client.post(
            "/api/v4/extraction/submit",
            json={
                "source_reference": "some-internal-ref",
                "source_type": "file",
            },
        )

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_submit_extraction_pipeline_failure(async_client) -> None:
    """202 even when the pipeline fails — job returned with failed status."""
    job = _make_job(
        status=JobStatus.FAILED,
        error_message="LLM API timeout",
    )

    with patch(
        "nfm_db.api.v4.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=job,
    ):
        response = await async_client.post(
            "/api/v4/extraction/submit",
            json={
                "source_reference": "10.1016/j.fail.2024",
                "source_type": "doi",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["error_message"] == "LLM API timeout"


@pytest.mark.asyncio
async def test_submit_extraction_missing_body(async_client) -> None:
    """422 when request body is missing."""
    response = await async_client.post("/api/v4/extraction/submit")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /api/v4/extraction/{job_id}/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_running(async_client) -> None:
    """Happy path: returns status with progress for a running job."""
    job = _make_job(status=JobStatus.RUNNING)
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/status",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == job.job_id
        assert data["source_type"] == "doi"
        assert data["status"] == "running"
        assert "progress" in data
        progress = data["progress"]
        assert progress["current_step"] == "running"
        assert isinstance(progress["steps_completed"], list)
        assert isinstance(progress["steps_remaining"], list)
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_status_completed(async_client) -> None:
    """Returns completed status with full progress."""
    job = _make_job(
        status=JobStatus.COMPLETED,
        extracted_count=10,
        staged_count=8,
        rejected_count=2,
    )
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/status",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"
        assert data["extracted_count"] == 10
        assert data["staged_count"] == 8
        assert data["rejected_count"] == 2
        progress = data["progress"]
        assert progress["current_step"] == "completed"
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_status_queued(async_client) -> None:
    """Returns queued status — first step, all steps remaining."""
    job = _make_job(status=JobStatus.QUEUED)
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/status",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "queued"
        progress = data["progress"]
        assert progress["current_step"] == "queued"
        assert len(progress["steps_completed"]) == 0
        assert len(progress["steps_remaining"]) > 0
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_status_failed(async_client) -> None:
    """Returns failed status with error message."""
    job = _make_job(
        status=JobStatus.FAILED,
        error_message="Pipeline crash",
    )
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/status",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "failed"
        assert data["error_message"] == "Pipeline crash"
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_status_not_found(async_client) -> None:
    """404 when job_id does not exist."""
    response = await async_client.get(
        f"/api/v4/extraction/{uuid.uuid4()!s}/status",
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert "not found" in body["error"]


# ---------------------------------------------------------------------------
# 3. GET /api/v4/extraction/{job_id}/result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_result_completed(async_client) -> None:
    """Happy path: returns results for a completed job."""
    job = _make_job(
        status=JobStatus.COMPLETED,
        extracted_count=3,
    )
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[
            {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
                "source": "10.1016/j.test",
                "source_doi": None,
                "uncertainty": 0.01,
                "temperature": 300.0,
                "source_file": "/data/paper.md",
                "composition": "UO2",
                "element": "U",
                "property_category": "structural",
                "context": None,
                "confidence": "high",
                "staging_status": "approved",
                "cache_level": "L1",
            },
        ],
    ):
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_status"] == "completed"
    assert data["total_extracted"] == 3
    assert len(data["properties"]) == 1
    prop = data["properties"][0]
    assert prop["property"] == "lattice_constant"
    assert prop["value"] == "5.47"
    assert prop["unit"] == "angstrom"
    assert prop["confidence"] == "high"
    assert body["meta"]["total"] == 1
    assert body["meta"]["page"] == 1

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_not_found(async_client) -> None:
    """404 when job_id does not exist."""
    response = await async_client.get(
        f"/api/v4/extraction/{uuid.uuid4()!s}/result",
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False


@pytest.mark.asyncio
async def test_get_result_not_completed(async_client) -> None:
    """409 when job exists but is not in completed status."""
    job = _make_job(status=JobStatus.RUNNING)
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
        )

        assert response.status_code == 409
        body = response.json()
        assert body["success"] is False
        assert "not 'completed'" in body["error"]
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_with_confidence_filter(async_client) -> None:
    """Results filtered by confidence query parameter."""
    job = _make_job(status=JobStatus.COMPLETED, extracted_count=3)
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[
            {"property_name": "prop_a", "confidence": "high", "value": 1.0, "unit": "m"},
            {"property_name": "prop_b", "confidence": "medium", "value": 2.0, "unit": "kg"},
            {"property_name": "prop_c", "confidence": "low", "value": 3.0, "unit": "s"},
        ],
    ):
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
            params={"confidence": "high"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["properties"]) == 1
    meta = response.json()["meta"]
    assert meta["confidence_filter"] == "high"

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_invalid_confidence(async_client) -> None:
    """400 when confidence filter value is invalid."""
    job = _make_job(status=JobStatus.COMPLETED)
    _job_store[job.job_id] = job

    try:
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
            params={"confidence": "invalid"},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Invalid confidence" in body["error"]
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_with_pagination(async_client) -> None:
    """Pagination parameters are honored in the response."""
    job = _make_job(status=JobStatus.COMPLETED, extracted_count=5)
    _job_store[job.job_id] = job

    props = [
        {"property_name": f"prop_{i}", "confidence": "high", "value": float(i), "unit": "m"}
        for i in range(5)
    ]

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=props,
    ):
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
            params={"page": 2, "limit": 2},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["properties"]) == 2
    meta = response.json()["meta"]
    assert meta["page"] == 2
    assert meta["limit"] == 2
    assert meta["total"] == 5

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_with_category_filter(async_client) -> None:
    """Results filtered by property_category query parameter."""
    job = _make_job(status=JobStatus.COMPLETED, extracted_count=3)
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[
            {"property_name": "prop_a", "property_category": "structural", "confidence": "high", "value": 1.0, "unit": "m"},
            {"property_name": "prop_b", "property_category": "thermal", "confidence": "high", "value": 2.0, "unit": "W/(m·K)"},
            {"property_name": "prop_c", "property_category": "structural", "confidence": "high", "value": 3.0, "unit": "GPa"},
        ],
    ):
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
            params={"property_category": "thermal"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["properties"]) == 1
    assert data["properties"][0]["property_category"] == "thermal"
    meta = response.json()["meta"]
    assert meta["category_filter"] == "thermal"

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_get_result_exclude_figures_and_tables(async_client) -> None:
    """include_figures=False and include_tables=False returns empty multimodal arrays,
    regardless of whether the job has figures/tables data."""
    job = _make_job(
        status=JobStatus.COMPLETED,
        extracted_count=1,
    )
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[],
    ):
        response = await async_client.get(
            f"/api/v4/extraction/{job.job_id}/result",
            params={"include_figures": "false", "include_tables": "false"},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["figures"] == []
    assert data["tables"] == []

    _cleanup_job(job.job_id)


# ---------------------------------------------------------------------------
# 4. GET /api/v4/properties/{material_system}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_properties_empty(async_client) -> None:
    """Returns empty list when no properties exist for the material system."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["material_system"] == "UO2"
    assert data["total_count"] == 0
    assert data["properties"] == []
    meta = body["meta"]
    assert meta["total"] == 0
    assert meta["page"] == 1


@pytest.mark.asyncio
async def test_browse_properties_with_pagination(async_client) -> None:
    """Pagination params are reflected in the response meta."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={"page": 3, "limit": 10},
    )

    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["page"] == 3
    assert meta["limit"] == 10


@pytest.mark.asyncio
async def test_browse_properties_invalid_sort_by(async_client) -> None:
    """400 when sort_by is not one of the valid fields."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={"sort_by": "invalid_field"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid sort_by" in body["error"]


@pytest.mark.asyncio
async def test_browse_properties_invalid_sort_order(async_client) -> None:
    """400 when sort_order is not asc or desc."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={"sort_order": "random"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid sort_order" in body["error"]


@pytest.mark.asyncio
async def test_browse_properties_invalid_confidence(async_client) -> None:
    """400 when confidence filter is invalid."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={"confidence": "superb"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid confidence" in body["error"]


@pytest.mark.asyncio
async def test_browse_properties_invalid_staging_status(async_client) -> None:
    """400 when staging_status filter is invalid."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={"staging_status": "invalid"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert "Invalid staging_status" in body["error"]


@pytest.mark.asyncio
async def test_browse_properties_valid_sort_fields(async_client) -> None:
    """All valid sort_by values are accepted."""
    for field in ("property", "temperature", "confidence", "created_at"):
        response = await async_client.get(
            "/api/v4/properties/UO2",
            params={"sort_by": field},
        )
        assert response.status_code == 200, f"sort_by={field} should return 200"


@pytest.mark.asyncio
async def test_browse_properties_filter_params_in_meta(async_client) -> None:
    """Filter parameters are echoed in the meta.filters object."""
    response = await async_client.get(
        "/api/v4/properties/UO2",
        params={
            "property_category": "thermal",
            "confidence": "high",
            "phase": "FCC",
            "temperature_min": 200,
            "temperature_max": 1000,
            "staging_status": "approved",
        },
    )

    assert response.status_code == 200
    meta = response.json()["meta"]
    filters = meta["filters"]
    assert filters["property_category"] == "thermal"
    assert filters["confidence"] == "high"
    assert filters["phase"] == "FCC"
    assert filters["temperature_min"] == 200
    assert filters["temperature_max"] == 1000
    assert filters["staging_status"] == "approved"


# ---------------------------------------------------------------------------
# 5. POST /api/v4/extraction/{job_id}/validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_completed_job(async_client) -> None:
    """Happy path: validates a completed job, returns 202 with validation summary."""
    job = _make_job(
        status=JobStatus.COMPLETED,
        extracted_count=3,
    )
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[
            {"property_name": "prop_a", "confidence": "high", "value": 1.0, "unit": "m"},
            {"property_name": "prop_b", "confidence": "medium", "value": 2.0, "unit": "kg"},
            {"property_name": "prop_c", "confidence": "low", "value": 3.0, "unit": "s"},
        ],
    ):
        response = await async_client.post(
            f"/api/v4/extraction/{job.job_id}/validate",
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == job.job_id
    assert data["total_properties"] == 3
    assert data["auto_approved"] == 1  # high confidence
    assert data["sent_to_review"] == 1  # medium confidence
    assert data["flagged"] == 1  # low confidence
    assert data["validation_id"].startswith("val-")
    assert data["review_url"] is not None

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_validate_with_auto_approve_false(async_client) -> None:
    """When auto_approve=False, high-confidence items are not auto-approved."""
    job = _make_job(status=JobStatus.COMPLETED)
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[
            {"property_name": "prop_a", "confidence": "high", "value": 1.0, "unit": "m"},
            {"property_name": "prop_b", "confidence": "medium", "value": 2.0, "unit": "kg"},
        ],
    ):
        response = await async_client.post(
            f"/api/v4/extraction/{job.job_id}/validate",
            json={"auto_approve": False},
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["auto_approved"] == 0
    assert data["sent_to_review"] == 1  # medium

    _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_validate_not_found(async_client) -> None:
    """404 when job_id does not exist."""
    response = await async_client.post(
        f"/api/v4/extraction/{uuid.uuid4()!s}/validate",
    )

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False


@pytest.mark.asyncio
async def test_validate_not_completed(async_client) -> None:
    """409 when job is not in completed status."""
    job = _make_job(status=JobStatus.RUNNING)
    _job_store[job.job_id] = job

    try:
        response = await async_client.post(
            f"/api/v4/extraction/{job.job_id}/validate",
        )

        assert response.status_code == 409
        body = response.json()
        assert body["success"] is False
        assert "not 'completed'" in body["error"]
    finally:
        _cleanup_job(job.job_id)


@pytest.mark.asyncio
async def test_validate_empty_properties(async_client) -> None:
    """Validation on completed job with zero properties returns zero counts."""
    job = _make_job(status=JobStatus.COMPLETED)
    _job_store[job.job_id] = job

    with patch(
        "nfm_db.api.v4.extraction._get_job_properties",
        new_callable=AsyncMock,
        return_value=[],
    ):
        response = await async_client.post(
            f"/api/v4/extraction/{job.job_id}/validate",
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["total_properties"] == 0
    assert data["auto_approved"] == 0
    assert data["sent_to_review"] == 0
    assert data["flagged"] == 0

    _cleanup_job(job.job_id)


# ---------------------------------------------------------------------------
# 6. GET /api/v4/material-systems
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_systems_empty(async_client) -> None:
    """Returns empty list when no material systems have extracted data."""
    response = await async_client.get("/api/v4/material-systems")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["material_systems"] == []
    meta = body["meta"]
    assert meta["total"] == 0


@pytest.mark.asyncio
async def test_list_material_systems_with_category_filter(async_client) -> None:
    """Category filter is accepted even when no systems exist."""
    response = await async_client.get(
        "/api/v4/material-systems",
        params={"category": "structural"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["material_systems"] == []


@pytest.mark.asyncio
async def test_list_material_systems_has_pending_review(async_client) -> None:
    """has_pending_review filter is accepted."""
    response = await async_client.get(
        "/api/v4/material-systems",
        params={"has_pending_review": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["material_systems"] == []
