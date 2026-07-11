"""Integration tests for /api/v1/extraction endpoints."""

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
# POST /api/v1/extraction/trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_extraction_success(async_client) -> None:
    """Happy path: valid DOI source triggers extraction and returns 202."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="10.1016/j.nucengdes.2020.110756",
        source_type="doi",
        status=JobStatus.COMPLETED,
        extracted_count=3,
        staged_count=2,
        rejected_count=1,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ) as mock_trigger:
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "10.1016/j.nucengdes.2020.110756",
                "source_type": "doi",
            },
        )

    mock_trigger.assert_awaited_once()
    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == fake_job.job_id
    assert data["source_reference"] == "10.1016/j.nucengdes.2020.110756"
    assert data["source_type"] == "doi"
    assert data["status"] == "completed"
    assert "message" in data


@pytest.mark.asyncio
async def test_trigger_extraction_with_element_systems(async_client) -> None:
    """Trigger extraction with optional element_systems filter."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="10.1016/j.nucengdes.2020.110756",
        source_type="doi",
        element_systems=["U", "Zr"],
        status=JobStatus.COMPLETED,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ) as mock_trigger:
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "10.1016/j.nucengdes.2020.110756",
                "source_type": "doi",
                "element_systems": ["U", "Zr"],
                "cache_level": "L1",
                "max_confidence": "high",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True

    call_kwargs = mock_trigger.call_args[1]
    assert call_kwargs["element_systems"] == ["U", "Zr"]
    assert call_kwargs["cache_level"] == "L1"
    assert call_kwargs["max_confidence"] == "high"


@pytest.mark.asyncio
async def test_trigger_extraction_url_source(async_client) -> None:
    """Trigger extraction with url source_type."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="https://example.com/paper.pdf",
        source_type="url",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ):
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "https://example.com/paper.pdf",
                "source_type": "url",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["source_type"] == "url"
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_trigger_extraction_file_source(async_client) -> None:
    """Trigger extraction with file source_type."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="/data/papers/UO2_props.md",
        source_type="file",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ):
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "/data/papers/UO2_props.md",
                "source_type": "file",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["source_type"] == "file"


@pytest.mark.asyncio
async def test_trigger_extraction_internal_id_source(async_client) -> None:
    """Trigger extraction with internal_id source_type."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="DOC-2024-001",
        source_type="internal_id",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ):
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "DOC-2024-001",
                "source_type": "internal_id",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["source_type"] == "internal_id"


@pytest.mark.asyncio
async def test_trigger_extraction_invalid_source_type(async_client) -> None:
    """400 when source_type is not one of the accepted values."""
    response = await async_client.post(
        "/api/v1/extraction/trigger",
        json={
            "source_reference": "some-ref",
            "source_type": "invalid_type",
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["detail"] is not None
    assert "Invalid source_type" in body["detail"]


@pytest.mark.asyncio
async def test_trigger_extraction_empty_source_reference(async_client) -> None:
    """422 when source_reference is empty (Pydantic min_length=1)."""
    response = await async_client.post(
        "/api/v1/extraction/trigger",
        json={
            "source_reference": "",
            "source_type": "doi",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_trigger_extraction_missing_body(async_client) -> None:
    """422 when request body is missing."""
    response = await async_client.post("/api/v1/extraction/trigger")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_trigger_extraction_pipeline_error(async_client) -> None:
    """202 when pipeline fails but the job is returned with failed status."""
    fake_job = ExtractionJob(
        job_id=str(uuid.uuid4()),
        source_reference="10.1016/j.nucengdes.2020.110756",
        source_type="doi",
        status=JobStatus.FAILED,
        error_message="LLM API timeout",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    with patch(
        "nfm_db.api.v1.extraction.trigger_extraction",
        new_callable=AsyncMock,
        return_value=fake_job,
    ):
        response = await async_client.post(
            "/api/v1/extraction/trigger",
            json={
                "source_reference": "10.1016/j.nucengdes.2020.110756",
                "source_type": "doi",
            },
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/status/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_extraction_status_success(async_client) -> None:
    """Happy path: returns status for an existing job."""
    job_id = str(uuid.uuid4())
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/j.example.2024.001",
        source_type="doi",
        status=JobStatus.RUNNING,
        extracted_count=5,
        staged_count=3,
        rejected_count=1,
        error_message=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
    )

    try:
        response = await async_client.get(
            f"/api/v1/extraction/status/{job_id}",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["job_id"] == job_id
        assert data["source_reference"] == "10.1016/j.example.2024.001"
        assert data["source_type"] == "doi"
        assert data["status"] == "running"
        assert data["extracted_count"] == 5
        assert data["staged_count"] == 3
        assert data["rejected_count"] == 1
        assert data["error_message"] is None
    finally:
        _job_store.pop(job_id, None)


@pytest.mark.asyncio
async def test_get_extraction_status_completed(async_client) -> None:
    """Returns completed status with all timestamps populated."""
    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="/data/paper.md",
        source_type="file",
        status=JobStatus.COMPLETED,
        extracted_count=10,
        staged_count=8,
        rejected_count=2,
        created_at=now,
        started_at=now,
        completed_at=now,
    )

    try:
        response = await async_client.get(
            f"/api/v1/extraction/status/{job_id}",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "completed"
        assert data["created_at"] is not None
        assert data["started_at"] is not None
        assert data["completed_at"] is not None
    finally:
        _job_store.pop(job_id, None)


@pytest.mark.asyncio
async def test_get_extraction_status_failed(async_client) -> None:
    """Returns failed status with error_message populated."""
    job_id = str(uuid.uuid4())
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/j.bad.2024",
        source_type="doi",
        status=JobStatus.FAILED,
        error_message="Connection refused",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    try:
        response = await async_client.get(
            f"/api/v1/extraction/status/{job_id}",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "failed"
        assert data["error_message"] == "Connection refused"
    finally:
        _job_store.pop(job_id, None)


@pytest.mark.asyncio
async def test_get_extraction_status_not_found(async_client) -> None:
    """404 when job_id does not exist."""
    random_id = str(uuid.uuid4())

    response = await async_client.get(
        f"/api/v1/extraction/status/{random_id}",
    )

    assert response.status_code == 404
    body = response.json()
    assert "not found" in body["detail"]


@pytest.mark.asyncio
async def test_get_extraction_status_invalid_uuid(async_client) -> None:
    """422 when job_id is not a valid UUID."""
    response = await async_client.get(
        "/api/v1/extraction/status/not-a-uuid",
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_extraction_status_queued(async_client) -> None:
    """Returns queued status (initial state, no started_at)."""
    job_id = str(uuid.uuid4())
    _job_store[job_id] = ExtractionJob(
        job_id=job_id,
        source_reference="10.1016/j.queue.2024",
        source_type="doi",
        status=JobStatus.QUEUED,
        created_at=datetime.now(UTC),
    )

    try:
        response = await async_client.get(
            f"/api/v1/extraction/status/{job_id}",
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "queued"
        assert data["extracted_count"] == 0
        assert data["staged_count"] == 0
        assert data["rejected_count"] == 0
        assert data["started_at"] is None
        assert data["completed_at"] is None
    finally:
        _job_store.pop(job_id, None)
