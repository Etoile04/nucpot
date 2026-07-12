"""Integration tests for extraction API endpoints (NFM-66).

Tests for POST /trigger and GET /status/{job_id}.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.services.extraction_pipeline import (
    JobStatus,
    _generate_job_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


# ---------------------------------------------------------------------------
# POST /api/v1/extraction/trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_extraction_success(db_session: AsyncSession) -> None:
    """Extraction trigger endpoint accepts a valid request and returns job_id."""
    payload = {
        "source_reference": "test_paper.md",
        "source_type": "file",
    }

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extraction/trigger",
            json=payload,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "job_id" in data
    assert data["status"] in ("completed", "partial", "queued")
    assert data["source_reference"] == "test_paper.md"


@pytest.mark.asyncio
async def test_trigger_with_optional_filters(db_session: AsyncSession) -> None:
    """Extraction trigger accepts element_systems and cache_level."""
    payload = {
        "source_reference": "file:/path/to/paper.pdf",
        "source_type": "file",
        "element_systems": ["U", "UO2"],
        "cache_level": "L1",
    }

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extraction/trigger",
            json=payload,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["status"] in ("completed", "partial", "queued")


@pytest.mark.asyncio
async def test_trigger_invalid_source_type(db_session: AsyncSession) -> None:
    """Extraction trigger rejects invalid source_type with 400 error."""
    payload = {
        "source_reference": "test_source",
        "source_type": "invalid_type",
    }

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extraction/trigger",
            json=payload,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Invalid source_type" in response.text or "invalid_type" in response.text.lower()


# ---------------------------------------------------------------------------
# GET /api/v1/extraction/status/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_extraction_status_success(db_session: AsyncSession) -> None:
    """Status endpoint returns job details for a valid job."""
    # First, manually create a job (simulate the trigger)
    job_id = _generate_job_id()

    from nfm_db.services.extraction_pipeline import ExtractionJob, _job_store

    job = ExtractionJob(
        job_id=job_id,
        source_reference="test_source",
        source_type="file",
        status=JobStatus.QUEUED,
        extracted_count=0,
        staged_count=0,
        rejected_count=0,
    )
    _job_store[job_id] = job

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/extraction/status/{job_id}")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["job_id"] == job_id
    assert data["status"] == "queued"
    assert data["extracted_count"] == 0
    assert data["staged_count"] == 0


@pytest.mark.asyncio
async def test_get_extraction_status_not_found() -> None:
    """Status endpoint returns 404 for non-existent job."""
    fake_id = str(uuid4())

    app.dependency_overrides[get_db] = _override_get_db(
        # Use a real session but we won't query it
        AsyncSession(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/extraction/status/{fake_id}")

    app.dependency_overrides.clear()

    assert response.status_code == 404
