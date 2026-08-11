"""Tests for GET /api/v1/extraction/jobs/{job_id}/steps/{step_name} (NFM-2883).

Single-step status query endpoint returning step status, track_id, and
timestamps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

_UNSET = object()

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(job_id: str, step_name: str) -> str:
    return f"/api/v1/extraction/jobs/{job_id}/steps/{step_name}"


async def _create_job_with_step(
    session: AsyncSession,
    *,
    step_type: str = "chunk",
    step_status: str = "completed",
    started_at: datetime | None | object = _UNSET,
    completed_at: datetime | None | object = _UNSET,
) -> tuple[ExtractionJob, ExtractionStep]:
    """Create an ExtractionJob + ExtractionStep pair and flush."""
    now = datetime.now(timezone.utc)
    job = ExtractionJob(
        id=uuid4(),
        status="completed",
        source_reference="test-doi",
        source_type="doi",
        started_at=now,
        completed_at=now,
    )
    session.add(job)
    await session.flush()

    _started = now if started_at is _UNSET else started_at
    _completed = now if completed_at is _UNSET else completed_at

    step = ExtractionStep(
        id=uuid4(),
        job_id=job.id,
        step_type=step_type,
        status=step_status,
        started_at=_started,
        completed_at=_completed,
    )
    session.add(step)
    await session.flush()
    return job, step


# ---------------------------------------------------------------------------
# AC1 + AC2: endpoint returns step status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_status_completed(db_session: AsyncSession) -> None:
    """Endpoint returns completed step with correct fields."""
    job, step = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job.id)
    assert data["step_name"] == "extract"
    assert data["status"] == "completed"
    assert data["started_at"] is not None
    assert data["completed_at"] is not None


@pytest.mark.asyncio
async def test_get_step_status_pending(db_session: AsyncSession) -> None:
    """Endpoint returns pending step with null completed_at."""
    job, step = await _create_job_with_step(
        db_session,
        step_type="chunk",
        step_status="pending",
        started_at=None,
        completed_at=None,
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["started_at"] is None
    assert data["completed_at"] is None


@pytest.mark.asyncio
async def test_get_step_status_running(db_session: AsyncSession) -> None:
    """Endpoint returns running step."""
    job, step = await _create_job_with_step(
        db_session, step_type="map", step_status="running"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "map"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "running"


@pytest.mark.asyncio
async def test_get_step_status_failed(db_session: AsyncSession) -> None:
    """Endpoint returns failed step."""
    job, step = await _create_job_with_step(
        db_session, step_type="quality_gate", step_status="failed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "quality_gate"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


# ---------------------------------------------------------------------------
# AC3: track_id field (nullable, depends on NFM-2881)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_status_track_id_null(db_session: AsyncSession) -> None:
    """Endpoint returns track_id=null when job has no track_id."""
    job, step = await _create_job_with_step(db_session)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["track_id"] is None


# ---------------------------------------------------------------------------
# AC4: 404 for missing job_id or step_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_status_job_not_found(db_session: AsyncSession) -> None:
    """Endpoint returns 404 for unknown job_id."""
    fake_id = str(uuid4())

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(fake_id, "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_step_status_step_not_found(db_session: AsyncSession) -> None:
    """Endpoint returns 404 for valid job but unknown step_name."""
    job, _ = await _create_job_with_step(db_session, step_type="chunk")

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "nonexistent_step"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC5: response format envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_status_response_format(db_session: AsyncSession) -> None:
    """Response envelope matches spec: {job_id, step_name, status, track_id, started_at, completed_at}."""
    job, step = await _create_job_with_step(
        db_session,
        step_type="gap_scan",
        step_status="completed",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "gap_scan"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    expected_keys = {"job_id", "step_name", "status", "track_id", "started_at", "completed_at"}
    assert set(data.keys()) == expected_keys
