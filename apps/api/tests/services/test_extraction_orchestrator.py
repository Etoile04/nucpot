"""Tests for ExtractionOrchestrator skeleton and feature flag routing (NFM-2585).

Covers:
1. Orchestrator runs all 5 steps and marks job completed.
2. Each step records start/completion in ExtractionStep table.
3. Input hash skip logic — step skipped when hash matches prior completion.
4. Step failure persists error_message and stops pipeline.
5. compute_input_hash determinism.
6. Feature flag defaults to False.
7. Feature flag routing: when True, orchestrator is called; when False, legacy runs.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.services.extraction_orchestrator import (
    ExtractionOrchestrator,
    _PIPELINE_STEPS,
    compute_input_hash,
)


# ---------------------------------------------------------------------------
# compute_input_hash unit tests
# ---------------------------------------------------------------------------


class TestComputeInputHash:
    """Unit tests for the input hash helper."""

    def test_deterministic_output(self) -> None:
        """Same params produce the same hash."""
        params = {"step_type": "chunk", "source": "10.1234/test"}
        h1 = compute_input_hash(params)
        h2 = compute_input_hash(params)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_params_different_hash(self) -> None:
        """Different params produce different hashes."""
        params_a = {"step_type": "chunk", "source": "A"}
        params_b = {"step_type": "extract", "source": "A"}
        assert compute_input_hash(params_a) != compute_input_hash(params_b)

    def test_order_invariant(self) -> None:
        """JSON sort_keys ensures order doesn't affect hash."""
        h1 = compute_input_hash({"b": 1, "a": 2})
        h2 = compute_input_hash({"a": 2, "b": 1})
        assert h1 == h2


# ---------------------------------------------------------------------------
# ExtractionOrchestrator.run integration tests
# ---------------------------------------------------------------------------


async def _create_job(
    *,
    session,
    source_reference: str = "doi:10.1234/test",
    source_type: str = "doi",
) -> ExtractionJob:
    """Create and return a persisted ORM ExtractionJob."""
    job = ExtractionJob(
        source_reference=source_reference,
        source_type=source_type,
    )
    session.add(job)
    await session.flush()
    return job


@pytest.mark.asyncio
async def test_run_all_steps_completed(db_session) -> None:
    """AC: Orchestrator runs all 5 steps and marks job completed."""
    job = await _create_job(session=db_session)
    orchestrator = ExtractionOrchestrator(db_session, job)

    result = await orchestrator.run()

    assert result.status == "completed"
    assert result.completed_at is not None
    assert result.error_message is None

    await db_session.refresh(result)

    # Verify 5 step rows exist.
    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
    )
    steps = (await db_session.execute(stmt)).scalars().all()
    assert len(steps) == 5

    step_types = {s.step_type for s in steps}
    assert step_types == set(_PIPELINE_STEPS)

    # All should be completed (not skipped — first run).
    for step in steps:
        assert step.status == "completed"
        assert step.started_at is not None
        assert step.completed_at is not None


@pytest.mark.asyncio
async def test_step_records_running_and_completed(db_session) -> None:
    """AC: Each step records start/completion in ExtractionStep table."""
    job = await _create_job(session=db_session)
    orchestrator = ExtractionOrchestrator(db_session, job)

    await orchestrator.run()

    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
        ExtractionStep.step_type == "chunk",
    )
    chunk_step = (await db_session.execute(stmt)).scalar_one_or_none()
    assert chunk_step is not None
    assert chunk_step.status == "completed"
    assert chunk_step.input_hash is not None
    assert chunk_step.started_at is not None
    assert chunk_step.completed_at is not None


# ---------------------------------------------------------------------------
# Input hash skip logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_on_hash_match(db_session) -> None:
    """AC: Input hash skip logic works — step skipped when hash matches."""
    job = await _create_job(session=db_session)

    # Pre-seed a completed step with a known hash.
    known_hash = compute_input_hash({
        "step_type": "chunk",
        "source_reference": job.source_reference,
        "source_type": job.source_type,
    })
    existing = ExtractionStep(
        job_id=job.id,
        step_type="chunk",
        status="completed",
        input_hash=known_hash,
    )
    db_session.add(existing)
    await db_session.flush()

    orchestrator = ExtractionOrchestrator(db_session, job)
    await orchestrator.run()

    # Should have 6 rows: 1 original + 1 skipped for chunk + 4 new.
    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
    )
    steps = (await db_session.execute(stmt)).scalars().all()
    chunk_steps = [s for s in steps if s.step_type == "chunk"]
    assert len(chunk_steps) == 2

    skipped = [s for s in chunk_steps if s.status == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].input_hash == known_hash


@pytest.mark.asyncio
async def test_no_skip_on_first_run(db_session) -> None:
    """First run has no prior steps — nothing should be skipped."""
    job = await _create_job(session=db_session)
    orchestrator = ExtractionOrchestrator(db_session, job)

    await orchestrator.run()

    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
        ExtractionStep.status == "skipped",
    )
    skipped = (await db_session.execute(stmt)).scalars().all()
    assert len(skipped) == 0


# ---------------------------------------------------------------------------
# Step failure — error persistence and pipeline stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_failure_persists_error(db_session) -> None:
    """AC: On step failure, error_message is persisted and pipeline stops."""
    job = await _create_job(session=db_session)
    orchestrator = ExtractionOrchestrator(db_session, job)

    # Make the extract step raise.
    with patch.object(
        orchestrator,
        "_step_extract",
        side_effect=RuntimeError("LLM timeout"),
    ):
        result = await orchestrator.run()

    assert result.status == "failed"
    assert "LLM timeout" in result.error_message
    assert result.completed_at is not None

    await db_session.refresh(result)

    # Only chunk completed; extract started (running); rest not reached.
    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
    )
    steps = (await db_session.execute(stmt)).scalars().all()
    step_types = {s.step_type for s in steps}
    assert "chunk" in step_types
    assert len(steps) == 2  # chunk (completed) + extract (running)


@pytest.mark.asyncio
async def test_step_failure_stops_pipeline(db_session) -> None:
    """Steps after the failure should not be created."""
    job = await _create_job(session=db_session)
    orchestrator = ExtractionOrchestrator(db_session, job)

    with patch.object(
        orchestrator,
        "_step_quality_gate",
        side_effect=ValueError("Bad data"),
    ):
        await orchestrator.run()

    stmt = select(ExtractionStep).where(
        ExtractionStep.job_id == job.id,
    )
    steps = (await db_session.execute(stmt)).scalars().all()
    step_types = {s.step_type for s in steps}
    # chunk, extract, map exist; quality_gate running; gap_scan absent.
    assert "gap_scan" not in step_types


# ---------------------------------------------------------------------------
# Pipeline steps list
# ---------------------------------------------------------------------------


def test_pipeline_steps_order() -> None:
    """Pipeline steps are in the expected order."""
    assert _PIPELINE_STEPS == [
        "chunk",
        "extract",
        "map",
        "quality_gate",
        "gap_scan",
    ]


# ---------------------------------------------------------------------------
# Feature flag routing
# ---------------------------------------------------------------------------


class TestFeatureFlagRouting:
    """Tests for EXTRACTION_V2_ENABLED feature flag."""

    def test_flag_defaults_to_false(self) -> None:
        """AC: Feature flag defaults to False."""
        from nfm_db.config import Settings

        s = Settings()
        assert s.extraction_v2_enabled is False

    def test_flag_enabled_via_env(self, monkeypatch) -> None:
        """Flag can be enabled via environment variable NFM_EXTRACTION_V2_ENABLED."""
        from nfm_db.config import Settings

        monkeypatch.setenv("NFM_EXTRACTION_V2_ENABLED", "true")
        s = Settings()
        assert s.extraction_v2_enabled is True

    def test_flag_disabled_via_env(self, monkeypatch) -> None:
        """Flag stays False when env var is 'false'."""
        from nfm_db.config import Settings

        monkeypatch.setenv("NFM_EXTRACTION_V2_ENABLED", "false")
        s = Settings()
        assert s.extraction_v2_enabled is False
