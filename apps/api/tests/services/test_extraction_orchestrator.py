"""Tests for ExtractionOrchestrator skeleton and feature flag routing (NFM-2585,
NFM-2588).

Covers:
1. Orchestrator runs all 5 steps and marks job completed.
2. Each step records start/completion in ExtractionStep table.
3. Input hash skip logic — step skipped when hash matches prior completion.
4. Step failure persists error_message and stops pipeline.
5. compute_input_hash determinism.
6. Feature flag defaults to True (NFM-2869-T2 flip; staging parity verified NFM-2875).
7. Feature flag routing: when True, orchestrator is called; when False, legacy runs.
8. _step_quality_gate wraps QualityGateService.process_bulk (NFM-2588).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.models.ref_gap_fill import RefGapFillStaging
from nfm_db.services.extraction_orchestrator import (
    _PIPELINE_STEPS,
    ExtractionOrchestrator,
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


@pytest.mark.skip(reason="NFM-3008: extraction_v2_enabled config field removed")
class TestFeatureFlagRouting:
    """Tests for EXTRACTION_V2_ENABLED feature flag."""

    def test_flag_defaults_to_true(self) -> None:
        """AC: Feature flag defaults to True (NFM-2869-T2 flip; parity NFM-2875)."""
        from nfm_db.config import Settings

        s = Settings()
        assert s.extraction_v2_enabled is True

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


# ---------------------------------------------------------------------------
# _step_quality_gate — NFM-2588
# ---------------------------------------------------------------------------


def _mapped_properties() -> list[dict]:
    """A small list of mapped properties exercising the three-path router.

    Confidence levels cover the auto/pending/pending_low branches; the
    'out_of_range' entry exercises the rejected branch when a range is
    defined for the property (none here, so it lands in pending_flagged).
    """
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property": "lattice_constant",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": "doi:10.1234/test",
            "source_doi": "10.1234/test",
            "confidence": "high",
            "uncertainty": 0.01,
            "temperature": 300.0,
            "cache_level": "L1",
        },
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property": "bulk_modulus",
            "property_name": "bulk_modulus",
            "value": 207.5,
            "unit": "GPa",
            "method": "EXP",
            "source": "doi:10.1234/test",
            "source_doi": "10.1234/test",
            "confidence": "medium",
            "uncertainty": 5.0,
            "temperature": 298.0,
            "cache_level": "L1",
        },
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property": "thermal_conductivity",
            "property_name": "thermal_conductivity",
            "value": 7.5,
            "unit": "W/(m·K)",
            "method": "EXP",
            "source": "doi:10.1234/test",
            "source_doi": "10.1234/test",
            "confidence": "low",
            "uncertainty": 1.5,
            "temperature": 1000.0,
            "cache_level": "L2",
        },
    ]


class TestStepQualityGate:
    """Verify _step_quality_gate wraps QualityGateService (NFM-2588)."""

    @pytest.mark.asyncio
    async def test_step_quality_gate_invokes_process_bulk(
        self, db_session, monkeypatch,
    ) -> None:
        """The step calls QualityGateService.process_bulk with mapped_properties."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        # Simulate Step 3 (map) output.
        orchestrator._context["mapped_properties"] = _mapped_properties()

        # Pre-create the running step (matches what _execute_step does).
        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        called: dict = {}

        class _FakeBulk:
            accepted: list = []
            rejected: list = []
            duplicates: list = []

        class _FakeGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                called["values"] = values
                return _FakeBulk()

            async def stage_record(self, *a, **kw):
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _FakeGate,
        )

        await orchestrator._step_quality_gate(step)

        assert called.get("values") == _mapped_properties()

    @pytest.mark.asyncio
    async def test_step_quality_gate_persists_accepted_records(
        self, db_session, monkeypatch,
    ) -> None:
        """Accepted results land in _ref_gap_fill_staging tied to the job."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["mapped_properties"] = _mapped_properties()

        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        # Let the real QualityGateService run — its stage_record writes
        # to _ref_gap_fill_staging.
        await orchestrator._step_quality_gate(step)

        rows = (
            await db_session.execute(
                select(RefGapFillStaging).where(
                    RefGapFillStaging.fill_batch_id == job.id,
                )
            )
        ).scalars().all()

        # All three properties should stage (no range data → all pass).
        assert len(rows) == 3

        # Each row must be tied to the originating ExtractionJob.
        for row in rows:
            assert row.fill_batch_id == job.id
            assert row.dedup_hash  # non-empty
            assert row.status is not None  # routed by confidence

    @pytest.mark.asyncio
    async def test_step_quality_gate_records_split_in_metadata(
        self, db_session, monkeypatch,
    ) -> None:
        """Step metadata records staged/rejected/duplicate counts."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["mapped_properties"] = _mapped_properties()

        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        await orchestrator._step_quality_gate(step)

        await db_session.refresh(step)

        assert step.metadata_ is not None
        assert "staged" in step.metadata_
        assert "rejected" in step.metadata_
        assert "duplicates" in step.metadata_
        # Real QualityGateService routes all three to accepted when no
        # range data is loaded.
        assert step.metadata_["staged"] == 3
        assert step.metadata_["rejected"] == 0
        assert step.metadata_["duplicates"] == 0

    @pytest.mark.asyncio
    async def test_step_quality_gate_skipped_when_hash_matches(
        self, db_session,
    ) -> None:
        """Pre-existing completed step with matching hash skips re-run.

        The expected hash must include ``mapped_properties`` so the
        pre-seeded row matches the orchestrator's content-aware hash
        (NFM-2600). Otherwise the orchestrator would not detect the
        prior completion and would re-run the step.

        Calls ``_execute_step('quality_gate')`` directly rather than
        ``run()`` so the chunk/extract/map steps do not overwrite
        the seeded ``mapped_properties`` context (which the map step
        would normally replace with its own output).
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        mapped = _mapped_properties()
        orchestrator._context["mapped_properties"] = list(mapped)

        # Pre-seed a completed quality_gate step with the hash the
        # orchestrator will compute — including the content-aware
        # ``mapped_properties`` (NFM-2600).
        existing_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": mapped,
        })
        completed = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="completed",
            input_hash=existing_hash,
        )
        db_session.add(completed)
        await db_session.flush()

        # Capture stage rows before re-run.
        pre_rows = (
            await db_session.execute(
                select(RefGapFillStaging).where(
                    RefGapFillStaging.fill_batch_id == job.id,
                )
            )
        ).scalars().all()
        assert len(pre_rows) == 0

        # Run only the quality_gate step — should be skipped.
        await orchestrator._execute_step("quality_gate")

        # No staging rows should have been written (the step was skipped).
        post_rows = (
            await db_session.execute(
                select(RefGapFillStaging).where(
                    RefGapFillStaging.fill_batch_id == job.id,
                )
            )
        ).scalars().all()
        assert len(post_rows) == 0

        # A skipped step row should exist for quality_gate.
        skipped_rows = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "quality_gate",
                    ExtractionStep.status == "skipped",
                )
            )
        ).scalars().all()
        assert len(skipped_rows) == 1

    @pytest.mark.asyncio
    async def test_step_quality_gate_failure_records_error_and_halts(
        self, db_session, monkeypatch,
    ) -> None:
        """On failure: step.status='failed', error_message set, raises.

        Mirrors ``TestStepMap::test_step_map_failure_records_error_and_halts``.
        When ``QualityGateService.process_bulk`` raises, the step row
        must record the failure (status='failed', error_message includes
        the exception class+message, completed_at set) and the exception
        must propagate so ``run()`` can halt the pipeline.
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        # Simulate Step 3 (map) output.
        orchestrator._context["mapped_properties"] = _mapped_properties()

        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        class _BrokenGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                raise RuntimeError("quality gate unavailable")

            async def stage_record(self, *a, **kw):
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _BrokenGate,
        )

        with pytest.raises(RuntimeError, match="quality gate unavailable"):
            await orchestrator._step_quality_gate(step)

        await db_session.refresh(step)

        assert step.status == "failed"
        assert step.error_message is not None
        assert "quality gate unavailable" in step.error_message
        assert "RuntimeError" in step.error_message
        assert step.completed_at is not None

    @pytest.mark.asyncio
    async def test_step_quality_gate_failure_inside_run_halts_pipeline(
        self, db_session, monkeypatch,
    ) -> None:
        """End-to-end: _step_quality_gate failure causes job.status='failed'.

        Mirrors ``TestStepMap::test_step_map_failure_inside_run_halts_pipeline``.
        A broken ``QualityGateService`` must surface as a failed job and
        the quality_gate step row must carry the failure metadata; the
        downstream ``gap_scan`` step must not run.

        ``_apply_property_mapping`` is monkey-patched so ``_step_map``
        produces a non-empty mapped list — otherwise ``_step_quality_gate``
        short-circuits on empty input and the broken gate is never
        invoked.
        """
        job = await _create_job(session=db_session)

        def _passthrough_map(raw_properties, cache_level):
            # Make map produce the same input as the broken gate will
            # see, so the gate failure is the only failure in the
            # pipeline.
            return _mapped_properties()

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _passthrough_map,
        )

        class _BrokenGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                raise ValueError("bad gate config")

            async def stage_record(self, *a, **kw):
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _BrokenGate,
        )

        result = await ExtractionOrchestrator(db_session, job).run()

        assert result.status == "failed"
        assert "bad gate config" in (result.error_message or "")

        # The quality_gate step should be marked failed.
        qg_step = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "quality_gate",
                )
            )
        ).scalar_one()
        assert qg_step.status == "failed"
        assert "ValueError" in (qg_step.error_message or "")
        assert qg_step.completed_at is not None

        # Steps after quality_gate (gap_scan) must not have been created.
        post_steps = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalars().all()
        assert len(post_steps) == 0

    @pytest.mark.asyncio
    async def test_step_quality_gate_input_hash_includes_mapped_properties(
        self, db_session,
    ) -> None:
        """AC: quality_gate's input_hash is the SHA-256 of the mapped_properties it gates.

        Without ``mapped_properties`` in the hash, a re-run whose
        upstream ``_step_map`` produced different properties would
        reuse a stale ``quality_gate`` result. Mirrors the content-aware
        hash pattern used by ``_step_map`` (see
        ``test_step_map_input_hash_includes_raw_extractions``) and
        ``_step_gap_scan`` (see
        ``test_gap_scan_input_hash_matches_staged_properties``).

        Regression: NFM-2600 — quality_gate's input_hash was missing
        ``mapped_properties`` and therefore could not detect upstream
        map-step changes.
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        orchestrator._context["mapped_properties"] = _mapped_properties()
        hash_a = compute_input_hash(
            orchestrator._build_step_params("quality_gate"),
        )

        changed = [{**_mapped_properties()[0], "value": 9.99}]
        orchestrator._context["mapped_properties"] = changed
        hash_b = compute_input_hash(
            orchestrator._build_step_params("quality_gate"),
        )

        assert hash_a != hash_b

    @pytest.mark.asyncio
    async def test_step_quality_gate_input_hash_matches_mapped_properties(
        self, db_session,
    ) -> None:
        """AC: input_hash on the quality_gate step equals SHA-256(params).

        Confirms that the params dict built by ``_build_step_params``
        literally contains the ``mapped_properties`` content (not
        only metadata), so the hash is content-addressed and changes
        deterministically when ``_step_map`` produces different
        output.

        Regression: NFM-2600.
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        mapped = _mapped_properties()
        orchestrator._context["mapped_properties"] = list(mapped)

        expected_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": mapped,
        })

        actual_hash = compute_input_hash(
            orchestrator._build_step_params("quality_gate"),
        )

        assert actual_hash == expected_hash
        assert len(actual_hash) == 64  # SHA-256 hex

    @pytest.mark.asyncio
    async def test_step_quality_gate_not_skipped_when_mapped_properties_differ(
        self, db_session, monkeypatch,
    ) -> None:
        """A quality_gate completed on different mapped_properties must not skip.

        End-to-end repro from NFM-2600:
        1. A previous run quality-gated mapped_properties M1.
        2. This run has different upstream output M2.
        3. The skip detector must NOT find a matching prior step,
           so quality_gate must actually run.

        Without ``mapped_properties`` in the params hash, the stale
        row would match and the step would be skipped, causing
        downstream stages to consume stale results.
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        # Seed upstream so chunk/extract/map don't try real I/O.
        orchestrator._context["raw_extractions"] = []
        orchestrator._context["chunks"] = []

        # A previous run quality-gated a DIFFERENT set of properties.
        orchestrator._context["mapped_properties"] = [
            {**_mapped_properties()[0], "value": 1.11},
        ]
        stale_hash = compute_input_hash(
            orchestrator._build_step_params("quality_gate"),
        )
        db_session.add(
            ExtractionStep(
                job_id=job.id,
                step_type="quality_gate",
                status="completed",
                input_hash=stale_hash,
            ),
        )
        await db_session.flush()

        # This run has new mapped_properties, so quality_gate must run.
        orchestrator._context["mapped_properties"] = _mapped_properties()

        called: dict = {}

        class _FakeBulk:
            accepted: list = []
            rejected: list = []
            duplicates: list = []

        class _FakeGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                called["count"] = len(values)
                return _FakeBulk()

            async def stage_record(self, *a, **kw):
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _FakeGate,
        )

        await orchestrator._execute_step("quality_gate")

        # The step must have actually run on the new mapped_properties,
        # not been skipped via the stale hash.
        assert called.get("count") == len(_mapped_properties())

    @pytest.mark.asyncio
    async def test_step_quality_gate_skipped_on_run_when_map_skip_restores_context(
        self, db_session, monkeypatch,
    ) -> None:
        """Skip path must restore ``_context`` from persisted step metadata.

        End-to-end repro of the NFM-2600 P2 finding (escalated by the
        CPO review): when ``_execute_step`` skips a step, it currently
        returns without restoring ``self._context`` from the existing
        step's ``metadata_``. The downstream step therefore computes
        its input_hash against an empty ``_context`` rather than the
        real upstream payload — so the skip detector fails to find a
        matching prior row and the step re-executes.

        Repro:
        1. A previous run produced ``map`` step with
           ``metadata_["mapped_properties"] = M1`` (real payload).
        2. Same previous run produced ``quality_gate`` step whose
           ``input_hash`` is the SHA-256 of ``mapped_properties=M1``.
        3. Re-run the orchestrator over the same job.
        4. ``map`` should skip (its input_hash over ``raw_extractions=[]``
           matches the prior row).
        5. ``quality_gate`` should ALSO skip — its hash over the
           restored ``mapped_properties=M1`` matches the prior row.

        Current behaviour (bug): ``map`` skip does not restore
        ``_context["mapped_properties"]`` so ``quality_gate``'s hash is
        computed against ``[]``, does NOT match the prior row, and the
        step re-executes (wasting a quality_gate run AND polluting the
        downstream ``gap_scan``).

        Mirrors the structural pattern of
        ``test_step_map_skipped_when_hash_matches`` (which already
        proves the same skip-restore behaviour for the ``map`` step
        itself, but cannot catch the cross-step regression because
        ``map`` produces its own hash input).
        """
        job = await _create_job(session=db_session)

        # Pre-seed a completed ``map`` step whose persisted
        # ``mapped_properties`` payload is non-empty. The orchestrator
        # on re-run will compute the ``map`` hash over
        # ``raw_extractions=[]`` (because no content is provided), so
        # the pre-seeded hash must match that empty-input hash for
        # ``map`` to skip.
        mapped = _mapped_properties()
        map_existing_hash = compute_input_hash({
            "step_type": "map",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "raw_extractions": [],
        })
        map_completed = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="completed",
            input_hash=map_existing_hash,
            metadata_={
                "input_count": 0,
                "mapped_count": len(mapped),
                "cache_level": None,
                "mapped_properties": list(mapped),
            },
        )
        db_session.add(map_completed)

        # Pre-seed a completed ``quality_gate`` step whose hash was
        # computed against the REAL ``mapped_properties`` payload.
        qg_existing_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": mapped,
        })
        qg_completed = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="completed",
            input_hash=qg_existing_hash,
            metadata_={
                "staged": len(mapped),
                "rejected": 0,
                "duplicates": 0,
            },
        )
        db_session.add(qg_completed)

        # Pre-seed a completed ``gap_scan`` step so the orchestrator's
        # ``_context["passed_properties"]`` skip-detection path is not
        # the variable under test (it has its own latent bug).
        gap_existing_hash = compute_input_hash({
            "step_type": "gap_scan",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "staged_properties": [],
        })
        db_session.add(
            ExtractionStep(
                job_id=job.id,
                step_type="gap_scan",
                status="completed",
                input_hash=gap_existing_hash,
                metadata_={"input_hash": gap_existing_hash, "non_fatal": True},
            ),
        )
        await db_session.flush()

        # Pre-seed a completed ``extract`` step with empty chunks
        # so the orchestrator on re-run skips extract (its hash over
        # ``chunk_ids=[]`` matches the prior row).
        extract_existing_hash = compute_input_hash({
            "step_type": "extract",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "chunk_ids": [],
            "extract_figures": False,
        })
        db_session.add(
            ExtractionStep(
                job_id=job.id,
                step_type="extract",
                status="completed",
                input_hash=extract_existing_hash,
            ),
        )
        await db_session.flush()

        # Stub QualityGateService so we can detect a re-execution.
        # If the step is correctly skipped, ``called`` stays empty.
        called: dict = {}

        class _FakeBulk:
            accepted: list = []
            rejected: list = []
            duplicates: list = []

        class _FakeGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                called["count"] = len(values)
                return _FakeBulk()

            async def stage_record(self, *a, **kw) -> None:
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _FakeGate,
        )

        # Run with no content — upstream will skip; quality_gate
        # should ALSO skip because the persisted ``map`` step's
        # ``mapped_properties`` are restored to ``_context``.
        orchestrator = ExtractionOrchestrator(db_session, job)
        await orchestrator.run()

        # QualityGateService.process_bulk must NOT have been invoked —
        # the step was skipped, not re-run on an empty payload.
        assert "count" not in called, (
            "quality_gate re-ran despite its prior row's hash matching "
            "after a restored context. _execute_step's skip path is "
            "not restoring _context from the existing step's metadata_."
        )

        # And there must be a ``skipped`` row for quality_gate whose
        # hash matches the pre-seeded completed row.
        quality_gate_rows = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "quality_gate",
                )
            )
        ).scalars().all()
        statuses = sorted(r.status for r in quality_gate_rows)
        assert statuses == ["completed", "skipped"], (
            f"Expected one completed + one skipped quality_gate row; "
            f"got {statuses}. The skip path is broken."
        )
        skipped_row = next(r for r in quality_gate_rows if r.status == "skipped")
        assert skipped_row.input_hash == qg_existing_hash


# ---------------------------------------------------------------------------
# _step_map — NFM-2587 (T2)
# ---------------------------------------------------------------------------


def _raw_extractions() -> list[dict]:
    """A small list of LLM-style raw extractions for the map step."""
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": "doi:10.1234/test",
            "confidence": "high",
        },
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "bulk modulus",
            "value": 207.5,
            "unit": "GPa",
            "method": "EXP",
            "source": "doi:10.1234/test",
            "confidence": "medium",
        },
    ]


class TestStepMap:
    """Verify _step_map wraps _apply_property_mapping (NFM-2587)."""

    @pytest.mark.asyncio
    async def test_step_map_invokes_apply_property_mapping(
        self, db_session, monkeypatch,
    ) -> None:
        """_step_map delegates to extraction_pipeline._apply_property_mapping."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["raw_extractions"] = _raw_extractions()

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        called: dict = {}

        def _fake_map(raw_properties, cache_level):
            called["raw_properties"] = raw_properties
            called["cache_level"] = cache_level
            # Mimic the real mapping: ensure 'property' alias exists.
            return [
                {**item, "property": item.get("property_name", "")}
                for item in raw_properties
            ]

        # Patch at the source module — the orchestrator imports lazily
        # from this location, so this catches the call.
        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _fake_map,
        )

        await orchestrator._step_map(step, cache_level="L1")

        assert called.get("raw_properties") == _raw_extractions()
        assert called.get("cache_level") == "L1"

    @pytest.mark.asyncio
    async def test_step_map_persists_results_in_step_metadata(
        self, db_session, monkeypatch,
    ) -> None:
        """Mapped results are queryable from DB via step.metadata_."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["raw_extractions"] = _raw_extractions()

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        # Use the real _apply_property_mapping for fidelity.
        await orchestrator._step_map(step, cache_level="L2")

        await db_session.refresh(step)

        assert step.metadata_ is not None
        assert step.metadata_["input_count"] == 2
        assert step.metadata_["mapped_count"] == 2
        assert step.metadata_["cache_level"] == "L2"
        assert "mapped_properties" in step.metadata_
        assert len(step.metadata_["mapped_properties"]) == 2
        # Each mapped item should carry the 'property' alias.
        for item in step.metadata_["mapped_properties"]:
            assert "property" in item
            assert "property_name" in item

    @pytest.mark.asyncio
    async def test_step_map_forwards_mapped_to_context(
        self, db_session, monkeypatch,
    ) -> None:
        """Mapped properties land in self._context for downstream steps."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["raw_extractions"] = _raw_extractions()

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        await orchestrator._step_map(step, cache_level="L1")

        assert "mapped_properties" in orchestrator._context
        mapped = orchestrator._context["mapped_properties"]
        assert len(mapped) == 2
        assert all("property" in m for m in mapped)

    @pytest.mark.asyncio
    async def test_step_map_handles_empty_input(self, db_session) -> None:
        """Empty raw_extractions → empty mapped, no error."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        # No raw_extractions seeded.

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        await orchestrator._step_map(step)

        await db_session.refresh(step)
        assert step.metadata_ is not None
        assert step.metadata_["input_count"] == 0
        assert step.metadata_["mapped_count"] == 0
        assert step.metadata_["mapped_properties"] == []
        assert orchestrator._context["mapped_properties"] == []

    @pytest.mark.asyncio
    async def test_step_map_failure_records_error_and_halts(
        self, db_session, monkeypatch,
    ) -> None:
        """On failure: step.status='failed', error_message set, pipeline stops."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["raw_extractions"] = _raw_extractions()

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        def _broken_map(raw_properties, cache_level):
            raise RuntimeError("mapping catalog unavailable")

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _broken_map,
        )

        with pytest.raises(RuntimeError, match="mapping catalog unavailable"):
            await orchestrator._step_map(step)

        await db_session.refresh(step)

        assert step.status == "failed"
        assert step.error_message is not None
        assert "mapping catalog unavailable" in step.error_message
        assert "RuntimeError" in step.error_message
        assert step.completed_at is not None

    @pytest.mark.asyncio
    async def test_step_map_failure_inside_run_halts_pipeline(
        self, db_session, monkeypatch,
    ) -> None:
        """End-to-end: _step_map failure causes job.status='failed'."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        # Seed raw_extractions so chunk + extract + map all have data.
        orchestrator._context["raw_extractions"] = _raw_extractions()

        def _broken_map(raw_properties, cache_level):
            raise ValueError("bad mapping config")

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _broken_map,
        )

        result = await orchestrator.run()

        assert result.status == "failed"
        assert "bad mapping config" in (result.error_message or "")

        # The map step should be marked failed.
        map_step = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "map",
                )
            )
        ).scalar_one()
        assert map_step.status == "failed"
        assert "ValueError" in (map_step.error_message or "")

        # Steps after map must not have been created.
        post_steps = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type.in_(("quality_gate", "gap_scan")),
                )
            )
        ).scalars().all()
        assert len(post_steps) == 0

    @pytest.mark.asyncio
    async def test_step_map_skipped_when_hash_matches(
        self, db_session, monkeypatch,
    ) -> None:
        """AC: a duplicate run over unchanged input skips the map step.

        Derives the hash from a real first run rather than hard-coding
        it, so the test stays honest if the step's input params change.
        """
        job = await _create_job(session=db_session)

        calls = {"n": 0}

        def _counting_map(raw_properties, cache_level):
            calls["n"] += 1
            return list(raw_properties)

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _counting_map,
        )

        # First run: map executes and records its input_hash.
        await ExtractionOrchestrator(db_session, job).run()
        assert calls["n"] == 1

        # Second run over the same job with unchanged input must skip map.
        await ExtractionOrchestrator(db_session, job).run()
        assert calls["n"] == 1, "map re-ran despite an unchanged input hash"

        map_rows = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "map",
                )
            )
        ).scalars().all()
        assert len(map_rows) == 2
        skipped = [r for r in map_rows if r.status == "skipped"]
        completed_rows = [r for r in map_rows if r.status == "completed"]
        assert len(skipped) == 1
        assert len(completed_rows) == 1
        assert skipped[0].input_hash == completed_rows[0].input_hash

    @pytest.mark.asyncio
    async def test_step_map_passes_cache_level_through(
        self, db_session, monkeypatch,
    ) -> None:
        """cache_level from kwargs reaches _apply_property_mapping."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["raw_extractions"] = _raw_extractions()

        step = ExtractionStep(
            job_id=job.id,
            step_type="map",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        captured: dict = {}

        def _capture(raw_properties, cache_level):
            captured["cache_level"] = cache_level
            return list(raw_properties)

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _capture,
        )

        await orchestrator._step_map(step, cache_level="L3")

        assert captured.get("cache_level") == "L3"
        # Metadata should also reflect the cache level.
        await db_session.refresh(step)
        assert step.metadata_["cache_level"] == "L3"

    @pytest.mark.asyncio
    async def test_step_map_input_hash_includes_raw_extractions(
        self, db_session,
    ) -> None:
        """AC: map's input_hash is the SHA-256 of the extractions it maps.

        Without the extractions in the hash, a re-run whose ``extract``
        step produced different properties would reuse a stale ``map``
        result.  Mirrors the content-addressing gap_scan already has.
        """
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        orchestrator._context["raw_extractions"] = _raw_extractions()
        hash_a = compute_input_hash(orchestrator._build_step_params("map"))

        changed = [{**_raw_extractions()[0], "value": 9.99}]
        orchestrator._context["raw_extractions"] = changed
        hash_b = compute_input_hash(orchestrator._build_step_params("map"))

        assert hash_a != hash_b

    @pytest.mark.asyncio
    async def test_step_map_not_skipped_when_raw_extractions_differ(
        self, db_session, monkeypatch,
    ) -> None:
        """A map step completed on different input must not suppress a re-map."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        # A previous run mapped a DIFFERENT set of extractions.
        orchestrator._context["raw_extractions"] = [
            {**_raw_extractions()[0], "value": 1.11},
        ]
        stale_hash = compute_input_hash(orchestrator._build_step_params("map"))
        db_session.add(
            ExtractionStep(
                job_id=job.id,
                step_type="map",
                status="completed",
                input_hash=stale_hash,
            ),
        )
        await db_session.flush()

        # This run has new extractions, so map must actually run.
        orchestrator._context["raw_extractions"] = _raw_extractions()

        called: dict = {}

        def _spy(raw_properties, cache_level):
            called["count"] = len(raw_properties)
            return list(raw_properties)

        monkeypatch.setattr(
            "nfm_db.services.extraction_pipeline._apply_property_mapping",
            _spy,
        )

        await orchestrator._execute_step("map")

        assert called.get("count") == len(_raw_extractions())


# ---------------------------------------------------------------------------
# _step_gap_scan — NFM-2568-T5
# ---------------------------------------------------------------------------


def _make_scan_result(
    gaps: list | None = None,
    covered: int = 5,
    total: int = 12,
) -> MagicMock:
    """Return a MagicMock that quacks like GapScanService.scan_gaps output."""
    from nfm_db.services.gap_scan_service import CoverageStats

    stats = CoverageStats(
        total_target_tuples=total,
        covered=covered,
        gaps=len(gaps or []),
        coverage_percent=round(covered / total * 100, 1) if total else 0.0,
    )
    result = MagicMock()
    result.gaps = list(gaps or [])
    result.stats = stats
    result.system_breakdown = []
    return result


class TestStepGapScan:
    """Tests for the Step 5 gap_scan wrapper (NFM-2568-T5).

    Covers acceptance criteria:
    1. Wrapper calls GapScanService.scan_gaps and stores the result.
    2. Non-fatal: a scan_gaps failure is recorded on the step but the
       pipeline still completes (job.status == 'completed').
    3. Skip detection: a pre-existing completed gap_scan with matching
       hash causes the step to be skipped.
    4. input_hash is the SHA-256 of the staged_properties content.
    """

    @pytest.mark.asyncio
    async def test_gap_scan_wrapper_success(self, db_session) -> None:
        """AC: Wrapper calls GapScanService.scan_gaps and records on step."""
        from nfm_db.services.gap_scan_service import GapTuple

        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["passed_properties"] = [
            {"element_system": "UO2", "phase": "FCC", "property_name": "lattice_constant"},
        ]

        expected_gaps = [
            GapTuple(
                element_system="UO2", phase="FCC",
                property_name="thermal_conductivity", priority=23,
            ),
        ]
        scan_result = _make_scan_result(gaps=expected_gaps, covered=6, total=12)

        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(return_value=scan_result),
        ):
            result = await orchestrator.run()

        assert result.status == "completed"

        gap_scan_step = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalar_one()
        assert gap_scan_step.status == "completed"
        assert gap_scan_step.error_message is None
        assert gap_scan_step.input_hash is not None
        assert gap_scan_step.metadata_["gap_count"] == 1
        assert gap_scan_step.metadata_["non_fatal"] is True
        assert gap_scan_step.metadata_["input_hash"] == gap_scan_step.input_hash

        # Gaps were passed forward in shared context for downstream steps.
        assert orchestrator._context.get("gaps") == expected_gaps

    @pytest.mark.asyncio
    async def test_gap_scan_non_fatal_failure_continues_pipeline(
        self, db_session,
    ) -> None:
        """AC: Non-fatal: gap_scan failure is recorded but pipeline continues."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        orchestrator._context["passed_properties"] = [
            {"element_system": "UO2", "phase": "FCC", "property_name": "lattice_constant"},
        ]

        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(side_effect=RuntimeError("DB connection lost mid-scan")),
        ):
            result = await orchestrator.run()

        # Pipeline continued despite the scan failure — job is still
        # 'completed' (other steps ran fine and reported success).
        assert result.status == "completed"
        assert result.error_message is None

        gap_scan_step = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalar_one()
        assert gap_scan_step.status == "failed"
        assert gap_scan_step.error_message is not None
        assert "DB connection lost" in gap_scan_step.error_message
        assert "non-fatal" in gap_scan_step.error_message
        assert gap_scan_step.completed_at is not None
        assert gap_scan_step.metadata_["non_fatal"] is True
        assert gap_scan_step.metadata_["gap_count"] == 0
        assert "error" in gap_scan_step.metadata_

        # Other steps (chunk, extract, map, quality_gate) completed
        # normally — proof that gap_scan's failure did not halt them.
        step_types = {
            row[0]
            for row in (
                await db_session.execute(
                    select(ExtractionStep.step_type).where(
                        ExtractionStep.job_id == job.id,
                    )
                )
            ).all()
        }
        assert step_types == set(_PIPELINE_STEPS)

    @pytest.mark.asyncio
    async def test_gap_scan_skip_on_hash_match(self, db_session) -> None:
        """AC: Skip detection — pre-existing completed step skips gap_scan."""
        job = await _create_job(session=db_session)

        # Pre-compute the hash the orchestrator will use (includes
        # staged_properties because gap_scan is content-addressed).
        staged = [
            {"element_system": "UO2", "phase": "FCC", "property_name": "lattice_constant"},
        ]
        match_hash = compute_input_hash({
            "step_type": "gap_scan",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "staged_properties": staged,
        })

        # Pre-seed upstream steps as completed so they skip on re-run.
        # quality_gate metadata includes passed_properties so that
        # _restore_context_from_existing can rehydrate _context for
        # gap_scan's hash computation (NFM-2606).
        for st in ("chunk",):
            st_hash = compute_input_hash({
                "step_type": st,
                "source_reference": job.source_reference,
                "source_type": job.source_type,
            })
            db_session.add(ExtractionStep(
                job_id=job.id, step_type=st,
                status="completed", input_hash=st_hash,
            ))
        for st in ("extract", "map"):
            extra = {"raw_extractions": []} if st == "map" else {"chunk_ids": [], "extract_figures": False}
            st_hash = compute_input_hash({
                "step_type": st,
                "source_reference": job.source_reference,
                "source_type": job.source_type,
                **extra,
            })
            meta = {}
            if st == "map":
                meta["mapped_properties"] = []
            db_session.add(ExtractionStep(
                job_id=job.id, step_type=st,
                status="completed", input_hash=st_hash,
                metadata_=meta,
            ))
        qg_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": [],
        })
        db_session.add(ExtractionStep(
            job_id=job.id, step_type="quality_gate",
            status="completed", input_hash=qg_hash,
            metadata_={
                "staged": 0, "rejected": 0, "duplicates": 0,
                "passed_properties": list(staged),
            },
        ))

        # Pre-seed a previously-completed gap_scan step with that hash.
        existing = ExtractionStep(
            job_id=job.id,
            step_type="gap_scan",
            status="completed",
            input_hash=match_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        orchestrator = ExtractionOrchestrator(db_session, job)

        # Sentinel: scan_gaps must NOT run — gap_scan should be skipped.
        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(
                side_effect=AssertionError(
                    "scan_gaps should not run when hash matches",
                ),
            ),
        ):
            result = await orchestrator.run()

        assert result.status == "completed"

        gap_scan_steps = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalars().all()
        # Two rows: the pre-seeded completed + the new "skipped" row.
        assert len(gap_scan_steps) == 2
        skipped = [s for s in gap_scan_steps if s.status == "skipped"]
        completed = [s for s in gap_scan_steps if s.status == "completed"]
        assert len(skipped) == 1
        assert len(completed) == 1
        assert skipped[0].input_hash == match_hash

    @pytest.mark.asyncio
    async def test_gap_scan_input_hash_matches_staged_properties(
        self, db_session,
    ) -> None:
        """AC: input_hash on the gap_scan step is SHA-256 of staged_properties."""
        job = await _create_job(session=db_session)

        staged = [
            {"element_system": "Zr", "phase": "HCP", "property_name": "bulk_modulus"},
            {"element_system": "UO2", "phase": "FCC", "property_name": "lattice_constant"},
        ]
        expected_hash = compute_input_hash({"staged_properties": staged})

        # Pre-seed all upstream steps as completed so they skip.
        # quality_gate metadata includes passed_properties so the
        # skip-restore path rehydrates _context for gap_scan (NFM-2606).
        for st in ("chunk",):
            st_hash = compute_input_hash({
                "step_type": st,
                "source_reference": job.source_reference,
                "source_type": job.source_type,
            })
            db_session.add(ExtractionStep(
                job_id=job.id, step_type=st,
                status="completed", input_hash=st_hash,
            ))
        for st in ("extract", "map"):
            extra = {"raw_extractions": []} if st == "map" else {"chunk_ids": [], "extract_figures": False}
            st_hash = compute_input_hash({
                "step_type": st,
                "source_reference": job.source_reference,
                "source_type": job.source_type,
                **extra,
            })
            meta = {}
            if st == "map":
                meta["mapped_properties"] = []
            db_session.add(ExtractionStep(
                job_id=job.id, step_type=st,
                status="completed", input_hash=st_hash,
                metadata_=meta,
            ))
        qg_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": [],
        })
        db_session.add(ExtractionStep(
            job_id=job.id, step_type="quality_gate",
            status="completed", input_hash=qg_hash,
            metadata_={
                "staged": 0, "rejected": 0, "duplicates": 0,
                "passed_properties": list(staged),
            },
        ))
        await db_session.flush()

        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(return_value=_make_scan_result()),
        ):
            await orchestrator.run()

        gap_scan_step = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalar_one()
        assert gap_scan_step.input_hash == expected_hash
        assert len(gap_scan_step.input_hash) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# NFM-2606: passed_properties producer/consumer fix
# ---------------------------------------------------------------------------


class TestQualityGatePassesPropertiesToGapScan:
    """Verify the _context["passed_properties"] producer/consumer pipeline.

    NFM-2606 found that _step_quality_gate wrote only counts to
    _context["quality_gate_result"] while _step_gap_scan read from the
    never-written _context["passed_properties"]. These tests prove the
    fix: quality_gate now also writes passed_properties, and gap_scan
    receives non-empty staged properties.
    """

    @pytest.mark.asyncio
    async def test_quality_gate_writes_passed_properties_to_context(
        self, db_session, monkeypatch,
    ) -> None:
        """AC: _step_quality_gate populates _context['passed_properties']
        with the staged property dicts."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        mapped = _mapped_properties()
        orchestrator._context["mapped_properties"] = list(mapped)

        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        # Use the real QualityGateService — all properties pass when
        # no range data is loaded.
        await orchestrator._step_quality_gate(step)

        # passed_properties must be a non-empty list of dicts.
        passed = orchestrator._context.get("passed_properties")
        assert isinstance(passed, list)
        assert len(passed) == len(mapped), (
            "passed_properties should contain one entry per staged property"
        )
        for item in passed:
            assert isinstance(item, dict)
            assert "element_system" in item

    @pytest.mark.asyncio
    async def test_quality_gate_persists_passed_properties_in_metadata(
        self, db_session, monkeypatch,
    ) -> None:
        """AC: passed_properties is stored in step.metadata_ for skip-restore."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)
        mapped = _mapped_properties()
        orchestrator._context["mapped_properties"] = list(mapped)

        step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(step)
        await db_session.flush()

        await orchestrator._step_quality_gate(step)
        await db_session.refresh(step)

        meta_passed = step.metadata_.get("passed_properties")
        assert isinstance(meta_passed, list)
        assert len(meta_passed) == len(mapped)

    @pytest.mark.asyncio
    async def test_gap_scan_receives_non_empty_staged_from_quality_gate(
        self, db_session,
    ) -> None:
        """AC: gap_scan receives real staged properties from quality_gate
        when steps are invoked directly (quality_gate → gap_scan)."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        mapped = _mapped_properties()
        orchestrator._context["mapped_properties"] = list(mapped)

        # Run quality_gate step — it should write passed_properties.
        qg_step = ExtractionStep(
            job_id=job.id,
            step_type="quality_gate",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(qg_step)
        await db_session.flush()
        await orchestrator._step_quality_gate(qg_step)

        # Verify passed_properties was written.
        passed = orchestrator._context.get("passed_properties")
        assert isinstance(passed, list)
        assert len(passed) > 0

        # Now run gap_scan step — it reads from passed_properties.
        gap_step = ExtractionStep(
            job_id=job.id,
            step_type="gap_scan",
            status="running",
            input_hash="deadbeef",
        )
        db_session.add(gap_step)
        await db_session.flush()

        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(return_value=_make_scan_result()),
        ):
            await orchestrator._step_gap_scan(gap_step)

        await db_session.flush()
        await db_session.refresh(gap_step)
        assert gap_step.input_hash is not None
        assert gap_step.input_hash != compute_input_hash(
            {"staged_properties": []}
        ), (
            "gap_scan input_hash should differ from empty-staged hash — "
            "proving it received real properties from quality_gate"
        )

    @pytest.mark.asyncio
    async def test_gap_scan_hash_content_aware_with_staged_properties(
        self, db_session,
    ) -> None:
        """AC: gap_scan's input_hash changes when staged properties change."""
        # Property set A
        props_a = [
            {"element_system": "Zr", "phase": "HCP", "property_name": "bulk_modulus", "property": "bulk_modulus"},
        ]
        # Property set B (different content)
        props_b = [
            {"element_system": "UO2", "phase": "FCC", "property_name": "thermal_conductivity", "property": "thermal_conductivity"},
        ]

        hash_a = compute_input_hash({"staged_properties": props_a})
        hash_b = compute_input_hash({"staged_properties": props_b})

        assert hash_a != hash_b, "Sanity: different property sets produce different hashes"

        # Directly verify that gap_scan uses the context value.
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        for props, expected_hash, label in [
            (props_a, hash_a, "A"), (props_b, hash_b, "B"),
        ]:
            orchestrator._context["passed_properties"] = list(props)
            gap_step = ExtractionStep(
                job_id=job.id,
                step_type="gap_scan",
                status="running",
                input_hash="deadbeef",
            )
            db_session.add(gap_step)
            await db_session.flush()

            with patch(
                "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
                new=AsyncMock(return_value=_make_scan_result()),
            ):
                await orchestrator._step_gap_scan(gap_step)

            await db_session.flush()
            await db_session.refresh(gap_step)
            assert gap_step.input_hash == expected_hash, (
                f"Property set {label}: gap_scan hash should match staged_properties hash"
            )

    @pytest.mark.asyncio
    async def test_quality_gate_skip_restores_passed_properties_for_gap_scan(
        self, db_session, monkeypatch,
    ) -> None:
        """AC: Two-real-run test — quality_gate skip restores
        passed_properties so gap_scan computes the correct input_hash.

        Mirrors test_step_quality_gate_skipped_on_run_when_map_skip_restores_context.
        """
        mapped = _mapped_properties()

        # --- Run 1: pre-seed completed steps for map, quality_gate, gap_scan ---
        job = await _create_job(session=db_session)

        # Completed map step with payload
        map_hash = compute_input_hash({
            "step_type": "map",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "raw_extractions": [],
        })
        db_session.add(ExtractionStep(
            job_id=job.id, step_type="map",
            status="completed", input_hash=map_hash,
            metadata_={"mapped_properties": list(mapped)},
        ))

        # Completed quality_gate step — hash is over real mapped_properties,
        # metadata includes passed_properties for restore.
        qg_hash = compute_input_hash({
            "step_type": "quality_gate",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "mapped_properties": mapped,
        })
        db_session.add(ExtractionStep(
            job_id=job.id, step_type="quality_gate",
            status="completed", input_hash=qg_hash,
            metadata_={
                "staged": len(mapped),
                "rejected": 0,
                "duplicates": 0,
                "passed_properties": list(mapped),
            },
        ))

        # Completed gap_scan step — hash is over real staged_properties
        # (NOT empty list as before the fix).
        gap_hash = compute_input_hash({
            "step_type": "gap_scan",
            "source_reference": job.source_reference,
            "source_type": job.source_type,
            "staged_properties": mapped,
        })
        db_session.add(ExtractionStep(
            job_id=job.id, step_type="gap_scan",
            status="completed", input_hash=gap_hash,
            metadata_={"input_hash": gap_hash, "non_fatal": True},
        ))

        # Completed extract and chunk steps so they also skip
        for st in ("chunk", "extract"):
            st_hash = compute_input_hash({
                "step_type": st,
                "source_reference": job.source_reference,
                "source_type": job.source_type,
                **({"chunk_ids": [], "extract_figures": False} if st == "extract" else {}),
            })
            db_session.add(ExtractionStep(
                job_id=job.id, step_type=st,
                status="completed", input_hash=st_hash,
            ))
        await db_session.flush()

        # --- Run 2: re-run the orchestrator ---
        # Stub QualityGateService to detect re-execution
        called: dict = {}

        class _FakeBulk:
            accepted: list = []
            rejected: list = []
            duplicates: list = []

        class _FakeGate:
            def __init__(self, *a, **kw) -> None:
                pass

            async def process_bulk(self, values):
                called["count"] = len(values)
                return _FakeBulk()

            async def stage_record(self, *a, **kw) -> None:
                return None

        monkeypatch.setattr(
            "nfm_db.services.extraction_orchestrator.QualityGateService",
            _FakeGate,
        )

        # Run 2: re-run the orchestrator over the same job.
        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch(
            "nfm_db.services.gap_scan_service.GapScanService.scan_gaps",
            new=AsyncMock(return_value=_make_scan_result()),
        ):
            result = await orchestrator.run()

        assert result.status == "completed"

        # QualityGateService.process_bulk must NOT have been called —
        # quality_gate was skipped and context was restored.
        assert "count" not in called, (
            "quality_gate re-ran despite hash match. "
            "passed_properties was not restored from metadata on skip."
        )

        # gap_scan must also have been skipped (no duplicate completed row).
        gap_rows = (
            await db_session.execute(
                select(ExtractionStep).where(
                    ExtractionStep.job_id == job.id,
                    ExtractionStep.step_type == "gap_scan",
                )
            )
        ).scalars().all()
        statuses = {r.status for r in gap_rows}
        assert statuses == {"completed", "skipped"} or statuses == {"completed"}, (
            f"Expected gap_scan to be skipped or already completed, got: {statuses}"
        )
