"""Tests for ExtractionOrchestrator._step_chunk and ._step_extract (NFM-2589).

Covers:
1. _step_chunk persists ExtractionChunk records with source_span.
2. _step_chunk uses the new chunker module.
3. _step_extract wraps ontofuel_extract per chunk and persists raw results.
4. Input hash composition for both steps.
5. Skip logic: pre-seeded completed steps with matching hash are skipped.
6. Failure isolation: chunk failure does not corrupt extract step state.
7. ExtractionStep status is recorded by _execute_step for both steps.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from nfm_db.models.extraction_chunk import ExtractionChunk
from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.services.extraction_orchestrator import (
    ExtractionOrchestrator,
    compute_input_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_job(
    *,
    session: Any,
    source_reference: str = "doi:10.1234/test",
    source_type: str = "doi",
) -> ExtractionJob:
    job = ExtractionJob(
        source_reference=source_reference,
        source_type=source_type,
    )
    session.add(job)
    await session.flush()
    return job


async def _get_step(
    session: Any,
    job_id: uuid.UUID,
    step_type: str,
) -> ExtractionStep | None:
    stmt = (
        select(ExtractionStep)
        .where(
            ExtractionStep.job_id == job_id,
            ExtractionStep.step_type == step_type,
        )
        .order_by(ExtractionStep.started_at)
    )
    return (await session.execute(stmt)).scalars().first()


async def _fake_ontofuel_extract(
    source_reference: str,
    source_type: str,
    element_systems: list[str] | None = None,
    db: Any = None,
) -> list[dict[str, Any]]:
    """Return a deterministic stub result so tests are reproducible."""
    return [
        {
            "property_name": "lattice_constant",
            "value": 5.47,
            "element_system": "UO2",
            "confidence": "high",
        },
    ]


# ---------------------------------------------------------------------------
# Step 1 — _step_chunk
# ---------------------------------------------------------------------------


class TestStepChunk:
    """Behaviour contract for the chunking step (NFM-2589)."""

    @pytest.mark.asyncio
    async def test_persists_extraction_chunks(self, db_session: Any) -> None:
        """AC: _step_chunk persists ExtractionChunk records."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        content = "First paragraph about UO2.\n\nSecond paragraph about MOX."
        await orchestrator._step_chunk(
            step=ExtractionStep(
                job_id=job.id,
                step_type="chunk",
                status="running",
                input_hash="placeholder",
            ),
            content=content,
            source_type=job.source_type,
        )

        stmt = select(ExtractionChunk).where(ExtractionChunk.job_id == job.id)
        chunks = (await db_session.execute(stmt)).scalars().all()
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.job_id == job.id
            assert isinstance(chunk.chunk_index, int)
            assert chunk.content is not None and len(chunk.content) > 0

    @pytest.mark.asyncio
    async def test_chunks_have_source_span(self, db_session: Any) -> None:
        """AC: Each chunk has source_span for traceability."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        content = "Para one.\n\nPara two.\n\nPara three."
        await orchestrator._step_chunk(
            step=ExtractionStep(
                job_id=job.id,
                step_type="chunk",
                status="running",
                input_hash="placeholder",
            ),
            content=content,
            source_type=job.source_type,
        )

        stmt = select(ExtractionChunk).where(ExtractionChunk.job_id == job.id)
        chunks = (await db_session.execute(stmt)).scalars().all()
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.source_span is not None
            assert "start" in chunk.source_span
            assert "end" in chunk.source_span
            start, end = chunk.source_span["start"], chunk.source_span["end"]
            assert content[start:end] == chunk.content, (
                f"chunk content does not match source_span: {chunk.content!r}"
            )

    @pytest.mark.asyncio
    async def test_uses_new_chunker_module(self, db_session: Any) -> None:
        """AC: Uses chunker.chunk_text from NFM-2567 (not _chunk_content)."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        content = "X" * 12_000 + "\n\n" + "Y" * 12_000

        from nfm_db.services import chunker as chunker_module
        original = chunker_module.chunk_text
        calls: list[str] = []

        def tracking_chunk_text(text, **kwargs):
            calls.append(text)
            return original(text, **kwargs)

        with patch.object(chunker_module, "chunk_text", tracking_chunk_text):
            await orchestrator._step_chunk(
                step=ExtractionStep(
                    job_id=job.id,
                    step_type="chunk",
                    status="running",
                    input_hash="placeholder",
                ),
                content=content,
                source_type=job.source_type,
            )
        assert len(calls) >= 1, "Expected chunker.chunk_text to be invoked"

    @pytest.mark.asyncio
    async def test_stores_chunks_in_context(self, db_session: Any) -> None:
        """AC: Chunks are stored in orchestrator context for downstream steps."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        await orchestrator._step_chunk(
            step=ExtractionStep(
                job_id=job.id,
                step_type="chunk",
                status="running",
                input_hash="placeholder",
            ),
            content="Alpha. Beta. Gamma.",
            source_type=job.source_type,
        )
        assert "chunks" in orchestrator._context
        chunks = orchestrator._context["chunks"]
        assert isinstance(chunks, list)
        assert all(isinstance(c, ExtractionChunk) for c in chunks)
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_chunk_step_hash_includes_content(
        self, db_session: Any,
    ) -> None:
        """AC: input_hash = SHA256(content + source_type)."""
        job = await _create_job(
            session=db_session, source_type="file",
        )
        orchestrator = ExtractionOrchestrator(db_session, job)

        content = "Some content"
        params = orchestrator._build_step_params(
            "chunk",
            content=content,
            source_type=job.source_type,
        )
        expected_hash = compute_input_hash(params)

        existing = ExtractionStep(
            job_id=job.id,
            step_type="chunk",
            status="completed",
            input_hash=expected_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        await orchestrator._execute_step(
            "chunk",
            content=content,
            source_type=job.source_type,
        )

        stmt = select(ExtractionStep).where(
            ExtractionStep.job_id == job.id,
            ExtractionStep.step_type == "chunk",
        )
        steps = (await db_session.execute(stmt)).scalars().all()
        assert len(steps) == 2
        skipped = [s for s in steps if s.status == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].input_hash == expected_hash

    @pytest.mark.asyncio
    async def test_chunk_step_skip_creates_no_new_chunks(
        self, db_session: Any,
    ) -> None:
        """AC: Skip logic for chunk step is independent of extract step."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        content = "Skip me."
        params = orchestrator._build_step_params(
            "chunk", content=content, source_type=job.source_type,
        )
        known_hash = compute_input_hash(params)

        existing = ExtractionStep(
            job_id=job.id,
            step_type="chunk",
            status="completed",
            input_hash=known_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        await orchestrator._execute_step(
            "chunk", content=content, source_type=job.source_type,
        )

        stmt = select(ExtractionChunk).where(ExtractionChunk.job_id == job.id)
        chunks = (await db_session.execute(stmt)).scalars().all()
        assert len(chunks) == 0


# ---------------------------------------------------------------------------
# Step 2 — _step_extract
# ---------------------------------------------------------------------------


class TestStepExtract:
    """Behaviour contract for the extraction step (NFM-2589)."""

    @pytest.mark.asyncio
    async def test_wraps_ontofuel_extract_per_chunk(
        self, db_session: Any,
    ) -> None:
        """AC: _step_extract wraps ontofuel_extract per chunk."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        chunk_a = ExtractionChunk(
            job_id=job.id, chunk_index=0,
            content="chunk A", source_span={"start": 0, "end": 7},
        )
        chunk_b = ExtractionChunk(
            job_id=job.id, chunk_index=1,
            content="chunk B", source_span={"start": 7, "end": 14},
        )
        db_session.add_all([chunk_a, chunk_b])
        await db_session.flush()
        orchestrator._context["chunks"] = [chunk_a, chunk_b]

        mocked = MagicMock(side_effect=_fake_ontofuel_extract)
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=mocked,
        ):
            await orchestrator._step_extract(
                step=ExtractionStep(
                    job_id=job.id,
                    step_type="extract",
                    status="running",
                    input_hash="placeholder",
                ),
                element_systems=["UO2"],
            )
        assert mocked.call_count == 2

    @pytest.mark.asyncio
    async def test_persists_raw_extraction_results(
        self, db_session: Any,
    ) -> None:
        """AC: Raw extraction results are persisted per chunk."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        chunk = ExtractionChunk(
            job_id=job.id, chunk_index=0,
            content="content", source_span={"start": 0, "end": 7},
        )
        db_session.add(chunk)
        await db_session.flush()
        orchestrator._context["chunks"] = [chunk]

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator._step_extract(
                step=ExtractionStep(
                    job_id=job.id,
                    step_type="extract",
                    status="running",
                    input_hash="placeholder",
                ),
            )

        assert "raw_extractions" in orchestrator._context
        results = orchestrator._context["raw_extractions"]
        assert isinstance(results, list)
        assert len(results) >= 1
        for r in results:
            assert "chunk_id" in r
            assert r["chunk_id"] == chunk.id

    @pytest.mark.asyncio
    async def test_extract_step_hash_includes_chunk_ids(
        self, db_session: Any,
    ) -> None:
        """AC: extract input_hash = SHA256(chunk_ids + element_systems + params)."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        chunk = ExtractionChunk(
            job_id=job.id, chunk_index=0,
            content="c", source_span={"start": 0, "end": 1},
        )
        db_session.add(chunk)
        await db_session.flush()
        # Pre-populate context as if _step_chunk had run.
        orchestrator._context["chunks"] = [chunk]

        element_systems = ["UO2", "MOX"]
        cache_level = "L2"
        params = orchestrator._build_step_params(
            "extract",
            element_systems=element_systems,
            cache_level=cache_level,
        )
        expected_hash = compute_input_hash(params)

        existing = ExtractionStep(
            job_id=job.id,
            step_type="extract",
            status="completed",
            input_hash=expected_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        await orchestrator._execute_step(
            "extract",
            element_systems=element_systems,
            cache_level=cache_level,
        )

        stmt = select(ExtractionStep).where(
            ExtractionStep.job_id == job.id,
            ExtractionStep.step_type == "extract",
        )
        steps = (await db_session.execute(stmt)).scalars().all()
        skipped = [s for s in steps if s.status == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].input_hash == expected_hash

    @pytest.mark.asyncio
    async def test_extract_step_skip_does_not_call_extractor(
        self, db_session: Any,
    ) -> None:
        """AC: Skip logic for extract step is independent of chunk step."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        chunk = ExtractionChunk(
            job_id=job.id, chunk_index=0,
            content="c", source_span={"start": 0, "end": 1},
        )
        db_session.add(chunk)
        await db_session.flush()
        orchestrator._context["chunks"] = [chunk]

        params = orchestrator._build_step_params(
            "extract",
            element_systems=["UO2"],
            cache_level="L1",
        )
        known_hash = compute_input_hash(params)

        existing = ExtractionStep(
            job_id=job.id,
            step_type="extract",
            status="completed",
            input_hash=known_hash,
        )
        db_session.add(existing)
        await db_session.flush()

        mocked = MagicMock(side_effect=_fake_ontofuel_extract)
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=mocked,
        ):
            await orchestrator._execute_step(
                "extract",
                element_systems=["UO2"],
                cache_level="L1",
            )
        assert mocked.call_count == 0, (
            "ontofuel_extract must not be invoked when step is skipped"
        )


# ---------------------------------------------------------------------------
# Step status & integration
# ---------------------------------------------------------------------------


class TestStepStatus:
    """ExtractionStep status is recorded by _execute_step."""

    @pytest.mark.asyncio
    async def test_chunk_step_marked_completed_after_execution(
        self, db_session: Any,
    ) -> None:
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        await orchestrator._execute_step(
            "chunk",
            content="hello world",
            source_type=job.source_type,
        )

        step = await _get_step(db_session, job.id, "chunk")
        assert step is not None
        assert step.status == "completed"
        assert step.started_at is not None
        assert step.completed_at is not None
        assert step.input_hash is not None

    @pytest.mark.asyncio
    async def test_extract_step_marked_completed_after_execution(
        self, db_session: Any,
    ) -> None:
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        chunk = ExtractionChunk(
            job_id=job.id, chunk_index=0,
            content="x", source_span={"start": 0, "end": 1},
        )
        db_session.add(chunk)
        await db_session.flush()
        orchestrator._context["chunks"] = [chunk]

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator._execute_step(
                "extract", element_systems=["UO2"],
            )

        step = await _get_step(db_session, job.id, "extract")
        assert step is not None
        assert step.status == "completed"


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    """A failure in the chunk step must not corrupt extract step state."""

    @pytest.mark.asyncio
    async def test_chunk_failure_does_not_create_extract_chunks(
        self, db_session: Any,
    ) -> None:
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch.object(
            orchestrator,
            "_step_chunk",
            side_effect=RuntimeError("chunker exploded"),
        ):
            result = await orchestrator.run(
                content="anything", source_type=job.source_type,
            )

        assert result.status == "failed"
        assert "chunker exploded" in result.error_message

        extract_step = await _get_step(db_session, job.id, "extract")
        assert extract_step is None

    @pytest.mark.asyncio
    async def test_chunk_failure_records_running_status(
        self, db_session: Any,
    ) -> None:
        """AC: When chunk step raises, the running step record persists."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch.object(
            orchestrator,
            "_step_chunk",
            side_effect=ValueError("boom"),
        ):
            await orchestrator.run(
                content="x", source_type=job.source_type,
            )

        chunk_step = await _get_step(db_session, job.id, "chunk")
        assert chunk_step is not None
        assert chunk_step.status in ("running", "failed")


# ---------------------------------------------------------------------------
# Chunk + extract integration
# ---------------------------------------------------------------------------


class TestChunkExtractIntegration:
    """End-to-end: chunk step feeds into extract step via _context."""

    @pytest.mark.asyncio
    async def test_extract_uses_chunks_from_context(
        self, db_session: Any,
    ) -> None:
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        await orchestrator._step_chunk(
            step=ExtractionStep(
                job_id=job.id,
                step_type="chunk",
                status="running",
                input_hash="placeholder",
            ),
            content="Alpha paragraph.\n\nBeta paragraph.",
            source_type=job.source_type,
        )
        assert "chunks" in orchestrator._context
        chunks = orchestrator._context["chunks"]

        mocked = MagicMock(side_effect=_fake_ontofuel_extract)
        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=mocked,
        ):
            await orchestrator._step_extract(
                step=ExtractionStep(
                    job_id=job.id,
                    step_type="extract",
                    status="running",
                    input_hash="placeholder",
                ),
            )

        assert mocked.call_count == len(chunks)
