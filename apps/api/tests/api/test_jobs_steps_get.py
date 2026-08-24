"""Contract tests for GET /api/v1/jobs/{job_id}/steps/{step_name} (NFM-3597).

Sibling C of NFM-3543 (Phase 1.5/1.6/1.7 step API + track_id reconciliation).
The endpoint returns the current state of a single pipeline step: status,
``track_id`` (durable identity for Sibling D rerun), artifacts, and
timestamps. ETag support allows cheap revalidation.

Distinct from NFM-2883's ``/extraction/jobs/{job_id}/steps/{step_name}``:
this route adds ``artifacts`` and ``error`` fields, renames
``completed_at`` → ``finished_at``, normalizes status to ``succeeded``,
and gates a 304 with an ETag header. The CTO-chosen route shortens the
URL prefix to ``/jobs/`` for the reconciliation rollout.

Sister sibling commits:
- NFM-3595-A: track_id UUID NOT NULL column on ``extraction_steps`` + index
- NFM-3596-B: orchestrator threads track_id into every step write

This file is the contract test; the route lives in
``nfm_db.api.v1.extraction``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep

# Sentinel distinguishing "caller omitted this timestamp" from an explicit None.
_UNSET = object()


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url(job_id: str, step_name: str) -> str:
    """Spec path ``GET /jobs/{id}/steps/{name}`` → API path with v1 prefix."""
    return f"/api/v1/jobs/{job_id}/steps/{step_name}"


async def _create_job_with_step(
    session: AsyncSession,
    *,
    step_type: str = "chunk",
    step_status: str = "completed",
    step_error: str | None = None,
    step_metadata: dict | None = None,
    job_track_id: str | None = None,
    started_at: datetime | object | None = _UNSET,
    completed_at: datetime | object | None = _UNSET,
) -> tuple[ExtractionJob, ExtractionStep]:
    """Create an ExtractionJob + ExtractionStep pair and flush.

    The ``track_id`` field on ExtractionStep is read defensively via
    ``getattr`` because NFM-3595's column is not on origin/main yet
    (the integration task NFM-3599 will merge it).
    """
    now = datetime.now(UTC)
    job = ExtractionJob(
        id=uuid4(),
        status="completed",
        source_reference="test-doi",
        source_type="doi",
        track_id=job_track_id,
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
        error_message=step_error,
        metadata_=step_metadata,
        started_at=_started,
        completed_at=_completed,
    )
    session.add(step)
    await session.flush()
    return job, step


# ---------------------------------------------------------------------------
# AC1 + AC2: 200 response with documented JSON shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_returns_documented_shape(db_session: AsyncSession) -> None:
    """200 response keys exactly match the spec envelope."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    expected_keys = {
        "job_id",
        "step_name",
        "status",
        "track_id",
        "artifacts",
        "started_at",
        "finished_at",
        "error",
    }
    assert set(data.keys()) == expected_keys


@pytest.mark.asyncio
async def test_get_step_status_succeeded_completed(db_session: AsyncSession) -> None:
    """Model status 'completed' is exposed as 'succeeded' per the spec enum."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_get_step_finished_at_is_completed_at(db_session: AsyncSession) -> None:
    """``finished_at`` mirrors the model's ``completed_at`` (rename)."""
    now = datetime.now(UTC)
    job, _ = await _create_job_with_step(
        db_session,
        step_type="map",
        step_status="completed",
        started_at=now,
        completed_at=now,
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "map"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["started_at"] is not None
    assert body["finished_at"] is not None
    assert body["finished_at"] == body["started_at"]


@pytest.mark.asyncio
async def test_get_step_pending_returns_null_timestamps(db_session: AsyncSession) -> None:
    """Pending step → null started_at and finished_at."""
    job, _ = await _create_job_with_step(
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
    body = response.json()
    assert body["status"] == "pending"
    assert body["started_at"] is None
    assert body["finished_at"] is None


@pytest.mark.asyncio
async def test_get_step_failed_returns_error(db_session: AsyncSession) -> None:
    """Failed step surfaces the error_message in the ``error`` field."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="quality_gate",
        step_status="failed",
        step_error="ontology mismatch on row 42",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "quality_gate"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "ontology mismatch on row 42"


@pytest.mark.asyncio
async def test_get_step_returns_empty_artifacts_by_default(
    db_session: AsyncSession,
) -> None:
    """Step without metadata artifacts surfaces an empty list (spec contract)."""
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="completed", step_metadata=None
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["artifacts"] == []


@pytest.mark.asyncio
async def test_get_step_returns_artifacts_from_metadata(
    db_session: AsyncSession,
) -> None:
    """Artifacts list under metadata_.artifacts is exposed verbatim."""
    artifacts = [
        {"key": "chunks.json", "url": "s3://bucket/chunks.json", "size_bytes": 12345},
        {"key": "metrics.csv", "url": "s3://bucket/metrics.csv", "size_bytes": 678},
    ]
    job, _ = await _create_job_with_step(
        db_session,
        step_type="extract",
        step_status="completed",
        step_metadata={"artifacts": artifacts},
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["artifacts"] == artifacts


# ---------------------------------------------------------------------------
# AC3: track_id propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_returns_job_track_id(db_session: AsyncSession) -> None:
    """track_id surfaces from ExtractionJob.track_id (NFM-2881 column)."""
    job, _ = await _create_job_with_step(
        db_session,
        step_type="chunk",
        step_status="completed",
        job_track_id="track-abc-123",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # Either the job track_id (no step column yet) or the step track_id
    # (post-NFM-3595 integration) — both acceptable per the spec wording
    # "the durable identity that ties rerun requests to the original step".
    assert response.json()["track_id"] == "track-abc-123"


@pytest.mark.asyncio
async def test_get_step_track_id_null_when_unset(db_session: AsyncSession) -> None:
    """track_id is null when neither job nor step carries one."""
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="pending", job_track_id=None
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["track_id"] is None


# ---------------------------------------------------------------------------
# AC4: 404 — single shape for both job-not-found and step-not-found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_returns_step_not_found_for_unknown_job(
    db_session: AsyncSession,
) -> None:
    """404 body uses the unified ``step_not_found`` shape (no existence leak)."""
    fake_job_id = str(uuid4())

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(fake_job_id, "chunk"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
    body = response.json()
    assert body == {
        "error": "step_not_found",
        "job_id": fake_job_id,
        "step_name": "chunk",
    }


@pytest.mark.asyncio
async def test_get_step_returns_step_not_found_for_unknown_step(
    db_session: AsyncSession,
) -> None:
    """404 body for unknown step matches the same shape as unknown job."""
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "nonexistent_step"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 404
    body = response.json()
    assert body == {
        "error": "step_not_found",
        "job_id": str(job.id),
        "step_name": "nonexistent_step",
    }


@pytest.mark.asyncio
async def test_get_step_404_does_not_leak_existence(
    db_session: AsyncSession,
) -> None:
    """Unknown-job and unknown-step responses must use the same shape."""
    fake_job_id = str(uuid4())
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r_unknown_job = await client.get(_url(fake_job_id, "chunk"))
        r_unknown_step = await client.get(_url(str(job.id), "does_not_exist"))

    app.dependency_overrides.pop(get_db, None)

    assert r_unknown_job.status_code == r_unknown_step.status_code == 404
    assert r_unknown_job.json()["error"] == r_unknown_step.json()["error"] == "step_not_found"


# ---------------------------------------------------------------------------
# AC5: ETag — If-None-Match returns 304 on match, 200 otherwise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_emits_etag_header(db_session: AsyncSession) -> None:
    """200 response carries an ETag header."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(_url(str(job.id), "extract"))

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.headers.get("ETag"), "ETag header is required"


@pytest.mark.asyncio
async def test_get_step_if_none_match_returns_304(db_session: AsyncSession) -> None:
    """If-None-Match matching the ETag returns 304 with no body."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(_url(str(job.id), "extract"))
        etag = first.headers["ETag"]
        cached = await client.get(
            _url(str(job.id), "extract"),
            headers={"If-None-Match": etag},
        )

    app.dependency_overrides.pop(get_db, None)

    assert first.status_code == 200
    assert cached.status_code == 304
    # 304 must have no body per RFC 7232 §4.1.
    assert cached.content == b""


@pytest.mark.asyncio
async def test_get_step_if_none_match_stale_returns_200(
    db_session: AsyncSession,
) -> None:
    """If-None-Match for a stale ETag still returns the fresh 200 body."""
    job, _ = await _create_job_with_step(
        db_session, step_type="extract", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            _url(str(job.id), "extract"),
            headers={"If-None-Match": '"deadbeef"'},
        )

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job.id)


# ---------------------------------------------------------------------------
# AC6: OpenAPI registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_step_registered_in_openapi(db_session: AsyncSession) -> None:
    """The route is visible in the OpenAPI schema at the documented path."""
    schema = app.openapi()
    paths = schema.get("paths", {})
    # The route registers as /api/v1/jobs/{job_id}/steps/{step_name}.
    assert "/api/v1/jobs/{job_id}/steps/{step_name}" in paths
    methods = paths["/api/v1/jobs/{job_id}/steps/{step_name}"]
    assert "get" in methods


# ---------------------------------------------------------------------------
# AC7: no regression — sibling endpoint from NFM-2883 still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sibling_extraction_jobs_endpoint_still_responds(
    db_session: AsyncSession,
) -> None:
    """NFM-2883 endpoint at ``/extraction/jobs/{job_id}/steps/{step_name}``
    continues to serve its existing contract (no regression from adding the
    new /jobs/ route alongside it)."""
    job, _ = await _create_job_with_step(
        db_session, step_type="chunk", step_status="completed"
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/extraction/jobs/{job.id}/steps/chunk"
        )

    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    # Existing endpoint keeps its "completed_at" key (not "finished_at")
    # and "completed" status string (not "succeeded") — verifies the two
    # routes coexist with distinct contracts.
    body = response.json()
    assert "completed_at" in body
    assert body["status"] == "completed"
