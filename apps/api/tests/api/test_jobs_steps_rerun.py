"""Contract and idempotency tests for POST /jobs/{id}/steps/{name}/rerun.

NFM-3543-D (NFM-3598) — idempotent single-step rerun endpoint.

Covers:
- 202 happy path with a fresh ``track_id`` and ``Idempotent-Replayed: false``.
- Idempotent replay: same ``Idempotency-Key`` returns the original
  ``track_id`` with ``Idempotent-Replayed: true`` and no new step row.
- 404 on unknown ``job_id`` or unknown ``step_name``.
- 422 when the latest step is in a terminal-success state and
  ``force=False``.
- 202 with ``force=True`` overrides the succeeded guard.
- The ``Idempotency-Key`` header beats the ``client_request_id`` body
  field when both are supplied.
- The historical step's ``track_id`` is preserved on the original row.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES, ExtractionStep


def _make_step(
    job_id: UUID,
    step_type: str,
    *,
    status: str = "completed",
    track_id: UUID | None = None,
) -> ExtractionStep:
    """Build an ExtractionStep ORM row bound to *job_id* (unflushed)."""
    metadata: dict[str, Any] | None = None
    if track_id is not None:
        metadata = {"track_id": str(track_id)}
    return ExtractionStep(
        job_id=job_id,
        step_type=step_type,
        status=status,
        input_hash="deadbeef" * 8,
        metadata_=metadata,
    )


async def _make_job(
    session: AsyncSession,
    *,
    step_status: str = "completed",
    with_step: bool = True,
    historical_track_id: UUID | None = None,
) -> tuple[ExtractionJob, ExtractionStep | None]:
    """Persist an ExtractionJob + (optionally) one ExtractionStep row."""
    job = ExtractionJob(
        source_reference="ref://rerun-test",
        source_type="file",
        status=step_status if not with_step else "completed",
    )
    session.add(job)
    await session.flush()

    step: ExtractionStep | None = None
    if with_step:
        step = _make_step(
            job.id, "extract",
            status=step_status,
            track_id=historical_track_id,
        )
        session.add(step)
        await session.flush()

    await session.commit()
    return job, step


# ---------------------------------------------------------------------------
# 202 happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_returns_202_with_fresh_track_id(
    async_client, db_session: AsyncSession,
) -> None:
    """AC-1: 202 + new track_id on first rerun."""
    historical = uuid4()
    job, _ = await _make_job(db_session, historical_track_id=historical)
    # ``async_client`` shares the same DB override.

    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert UUID(body["job_id"]) == job.id
    assert body["step_name"] == "extract"
    new_track = UUID(body["track_id"])
    assert UUID(body["original_track_id"]) == historical
    assert new_track != historical  # fresh UUID
    assert response.headers["idempotent-replayed"] == "false"


@pytest.mark.asyncio
async def test_rerun_persists_new_step_row_with_track_id_in_metadata(
    async_client, db_session: AsyncSession,
) -> None:
    """The new step row carries the new track_id in metadata_."""
    job, original = await _make_job(db_session, historical_track_id=uuid4())

    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True},
    )
    assert response.status_code == 202, response.text
    new_track_id = UUID(response.json()["track_id"])

    # Two ExtractionStep rows now exist for (job, extract): the original
    # and the new rerun.
    rows = (
        await db_session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job.id,
                ExtractionStep.step_type == "extract",
            )
        )
    ).scalars().all()
    assert len(rows) == 2

    # Original's metadata_.track_id must NOT have changed.
    original_meta = original.metadata_ or {}
    assert original_meta.get("track_id") != str(new_track_id)

    # The new row carries the new track_id.
    new_rows = [r for r in rows if r.id != original.id]
    assert len(new_rows) == 1
    assert (new_rows[0].metadata_ or {}).get("track_id") == str(new_track_id)
    assert (new_rows[0].metadata_ or {}).get("rerun") is True


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_with_same_idempotency_key_returns_original(
    async_client, db_session: AsyncSession,
) -> None:
    """AC-3: duplicate request within 24h replays the original."""
    job, _ = await _make_job(db_session, historical_track_id=uuid4())

    first = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True},
        headers={"Idempotency-Key": "test-key-001"},
    )
    assert first.status_code == 202
    first_track = UUID(first.json()["track_id"])

    second = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True},
        headers={"Idempotency-Key": "test-key-001"},
    )
    assert second.status_code == 202, second.text
    assert UUID(second.json()["track_id"]) == first_track
    assert second.headers["idempotent-replayed"] == "true"

    # And no third step row was created by the replay.
    rows = (
        await db_session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job.id,
                ExtractionStep.step_type == "extract",
            )
        )
    ).scalars().all()
    assert len(rows) == 2  # 1 original + 1 rerun; the replay reused the rerun


@pytest.mark.asyncio
async def test_idempotency_key_header_beats_body_client_request_id(
    async_client, db_session: AsyncSession,
) -> None:
    """When both the header and body field are present, the header wins."""
    job, _ = await _make_job(db_session, historical_track_id=uuid4())

    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True, "client_request_id": "body-key-001"},
        headers={"Idempotency-Key": "header-key-001"},
    )
    assert response.status_code == 202, response.text

    # A second call with the body key (NOT the header key) should NOT
    # be treated as a replay of the first call — it should produce a new
    # rerun.
    response2 = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True, "client_request_id": "body-key-001"},
    )
    assert response2.status_code == 202
    assert UUID(response2.json()["track_id"]) != UUID(response.json()["track_id"])
    assert response2.headers["idempotent-replayed"] == "false"


# ---------------------------------------------------------------------------
# 404 / 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_returns_404_for_unknown_job(
    async_client, db_session: AsyncSession,
) -> None:
    """AC: 404 ``step_not_found`` when ``job_id`` is unknown."""
    response = await async_client.post(
        f"/api/v1/jobs/{uuid4()}/steps/extract/rerun",
        json={"force": True},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "step_not_found"


@pytest.mark.asyncio
async def test_rerun_returns_404_for_unknown_step(
    async_client, db_session: AsyncSession,
) -> None:
    """AC: 404 ``step_not_found`` when ``step_name`` is not in EXTRACTION_STEP_TYPES."""
    job, _ = await _make_job(db_session)
    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/not_a_step/rerun",
        json={"force": True},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "step_not_found"


@pytest.mark.asyncio
async def test_rerun_returns_422_when_latest_step_is_succeeded_without_force(
    async_client, db_session: AsyncSession,
) -> None:
    """AC: 422 ``step_succeeded`` when force=false on a completed step."""
    job, _ = await _make_job(db_session, step_status="completed")
    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error_code"] == "step_succeeded"


@pytest.mark.asyncio
async def test_rerun_with_force_true_bypasses_succeeded_guard(
    async_client, db_session: AsyncSession,
) -> None:
    """AC: ``force=true`` allows re-running a succeeded step."""
    job, _ = await _make_job(db_session, step_status="completed")
    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/extract/rerun",
        json={"force": True},
    )
    assert response.status_code == 202, response.text


# ---------------------------------------------------------------------------
# EXTRACTION_STEP_TYPES coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step_name", list(EXTRACTION_STEP_TYPES))
@pytest.mark.asyncio
async def test_rerun_accepts_all_extraction_step_types(
    async_client, db_session: AsyncSession, step_name: str,
) -> None:
    """AC: every member of EXTRACTION_STEP_TYPES is a valid step_name."""
    # No historical step → no ``completed`` guard → force can be False.
    job, _ = await _make_job(db_session, with_step=False)
    response = await async_client.post(
        f"/api/v1/jobs/{job.id}/steps/{step_name}/rerun",
        json={},
    )
    assert response.status_code == 202, (
        f"step_name={step_name!r}: {response.status_code} {response.text}"
    )
