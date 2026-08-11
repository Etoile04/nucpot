"""Tests for POST /api/v1/extraction/jobs/{job_id}/steps/{step_name}/rerun (NFM-2884).

Single-step rerun endpoint that:
- Validates step is in a terminal state (completed or failed).
- Resets step to ``pending`` and clears parent ``track_id``.
- Dispatches the step execution as a fire-and-forget background task.
- Returns 202 Accepted with the reset step snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

_UNSET = object()

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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
    return (
        f"/api/v1/extraction/jobs/{job_id}/steps/{step_name}/rerun"
    )


async def _create_job_with_step(
    session: AsyncSession,
    *,
    step_type: str = "chunk",
    step_status: str = "completed",
    track_id: str | None = None,
    started_at: datetime | None | object = _UNSET,
    completed_at: datetime | None | object = _UNSET,
    error_message: str | None = None,
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
    if track_id is not None:
        # Column added by NFM-2881; the model field exists on this
        # branch but the migration may not be applied in the test DB.
        job.track_id = track_id  # type: ignore[attr-defined]
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
        error_message=error_message,
    )
    session.add(step)
    await session.flush()
    return job, step


# ---------------------------------------------------------------------------
# AC1 + AC4: endpoint exists and returns 202 + reset snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_completed_step_resets_to_pending(
    db_session: AsyncSession,
) -> None:
    """Rerunning a completed step resets it to pending and returns 202."""
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    data = response.json()
    assert data["job_id"] == str(job.id)
    assert data["step_name"] == "chunk"
    assert data["status"] == "pending"
    assert data["dispatched"] is True
    assert data["started_at"] is None
    assert data["completed_at"] is None


@pytest.mark.asyncio
async def test_rerun_failed_step_resets_to_pending(
    db_session: AsyncSession,
) -> None:
    """Rerunning a failed step also resets it to pending."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="extract",
        step_status="failed",
        error_message="upstream timeout",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["step_name"] == "extract"


@pytest.mark.asyncio
async def test_rerun_persists_reset_state(db_session: AsyncSession) -> None:
    """After rerun, the step row in DB has status=pending and cleared fields."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="map",
        step_status="failed",
        error_message="validation error",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "map"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202

    # Verify the step row in DB reflects the reset state via a fresh query.
    result = await db_session.execute(
        select(ExtractionStep).where(
            ExtractionStep.job_id == job.id,
            ExtractionStep.step_type == "map",
        )
    )
    step_row = result.scalar_one()
    assert step_row.status == "pending"
    assert step_row.started_at is None
    assert step_row.completed_at is None
    assert step_row.error_message is None


# ---------------------------------------------------------------------------
# AC3: track_id is cleared on the parent job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_clears_parent_track_id(db_session: AsyncSession) -> None:
    """After rerun, the parent job's track_id is cleared (when present)."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="quality_gate",
        step_status="completed",
        track_id="lightrag-track-abc123",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "quality_gate"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202

    # The response envelope reports null track_id.
    assert response.json()["track_id"] is None

    # The persisted parent job also has cleared track_id (when the
    # track_id column exists; NFM-2881 migration).
    if hasattr(job, "track_id"):
        assert job.track_id is None


# ---------------------------------------------------------------------------
# AC5: 404 for missing job or step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_unknown_job_returns_404(db_session: AsyncSession) -> None:
    """Rerun against a non-existent job_id returns 404."""
    fake_id = str(uuid4())

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(fake_id, "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rerun_unknown_step_returns_404(db_session: AsyncSession) -> None:
    """Rerun with a step_name not in EXTRACTION_STEP_TYPES returns 404."""
    job, _ = await _create_job_with_step(db_session, step_type="chunk")

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "nonexistent_step"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rerun_missing_step_row_returns_404(
    db_session: AsyncSession,
) -> None:
    """Valid step_name but no ExtractionStep row for this job returns 404."""
    # Create a job with NO step row.
    job = ExtractionJob(
        id=uuid4(),
        status="completed",
        source_reference="test-doi",
        source_type="doi",
    )
    db_session.add(job)
    await db_session.flush()

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# AC6: 409 Conflict when step is currently running or non-terminal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_running_step_returns_409(db_session: AsyncSession) -> None:
    """Rerun of a step with status=running returns 409 Conflict."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="running"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409
    assert "running" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_rerun_pending_step_returns_409(db_session: AsyncSession) -> None:
    """Rerun of a step with status=pending returns 409 (only completed/failed allowed)."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="gap_scan",
        step_status="pending",
        started_at=None,
        completed_at=None,
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "gap_scan"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rerun_skipped_step_returns_409(db_session: AsyncSession) -> None:
    """Rerun of a step with status=skipped returns 409."""
    job, _ = await _create_job_with_step(
        db_session, step_type="map", step_status="skipped"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "map"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Response envelope: AC-4 contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_response_envelope_shape(db_session: AsyncSession) -> None:
    """Response envelope has the documented keys."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 202
    data = response.json()
    expected_keys = {
        "job_id",
        "step_name",
        "status",
        "track_id",
        "started_at",
        "completed_at",
        "dispatched",
    }
    assert set(data.keys()) == expected_keys