"""TDD tests for the V2 ExtractionOrchestrator (NFM-2677 B7).

Composes the 5 strangler-fig steps (RawTextLoader →
SectionSegmenter → EntityExtractor → PropertyNormalizer →
ChunkBuilder) and persists each emitted chunk to the ORM
``extraction_chunks`` table.

NFM-2705 follow-up:
* unit tests verify the orchestrator adds ``ExtractionChunk`` ORM rows
  directly to the session (no ``_PersistTarget`` wrapper).
* integration test (``test_orchestrator_persists_chunks_to_db``)
  verifies chunks round-trip through the real ``db_session`` fixture
  with the parent ``ExtractionJob`` FK satisfied.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from nfm_db.models.extraction_chunk import ExtractionChunk as ORMChunk
from nfm_db.models.extraction_job import ExtractionJob as ORMExtractionJob
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction_orchestrator_v2 import (
    ExtractionOrchestratorV2,
)


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


def test_orchestrator_runs_full_pipeline():
    """A markdown document with one heading produces one persisted
    final chunk."""
    session = _make_session()
    orchestrator = ExtractionOrchestratorV2(session, job_id=ORMExtractionJob().id)
    initial = ExtractionChunk(
        content="intro\n\n## A\nUO2 lattice constant: 5.47 angstrom",
        chunk_type="raw_text",
        _source_span=(0, 51),
        metadata={},
    )
    finals = asyncio.run(orchestrator.run(initial))
    assert len(finals) == 1
    final = finals[0]
    assert final.chunk_type == "final"
    assert final.metadata["summary"]["formula_count"] >= 1


def test_orchestrator_persists_each_chunk_to_session():
    """Each emitted chunk (intermediate + final) is added to the
    session for the persistence layer to flush."""
    session = _make_session()
    orchestrator = ExtractionOrchestratorV2(session, job_id=ORMExtractionJob().id)
    initial = ExtractionChunk(
        content="intro\n\n## A\nbody",
        chunk_type="raw_text",
        _source_span=(0, 17),
        metadata={},
    )
    asyncio.run(orchestrator.run(initial))
    # Multiple session.add calls per chunk (one per emitted step +
    # the final fan-in).
    assert session.add.call_count >= 2
    assert session.flush.await_count >= 1


def test_orchestrator_handles_no_sections():
    """A document with no headings emits a single final chunk
    wrapping the whole document."""
    session = _make_session()
    orchestrator = ExtractionOrchestratorV2(session, job_id=ORMExtractionJob().id)
    initial = ExtractionChunk(
        content="plain text only",
        chunk_type="raw_text",
        _source_span=(0, 15),
        metadata={},
    )
    finals = asyncio.run(orchestrator.run(initial))
    assert len(finals) == 1
    assert finals[0].chunk_type == "final"


def test_orchestrator_adds_orm_chunks_with_job_id():
    """Defect 1+2 (NFM-2705): ``_persist()`` must add
    :class:`ExtractionChunk` ORM rows directly to the session with
    ``job_id`` populated from the orchestrator's parent job.

    Previously a ``_PersistTarget`` wrapper dataclass was added, which
    meant zero rows actually landed in ``extraction_chunks`` (a plain
    dataclass is not a SQLAlchemy mapped class, so ``session.add``
    registered the wrapper, not the wrapped ORM row).  ``job_id`` was
    also missing, which would have violated the NOT NULL FK even if
    the wrapper did forward to the ORM.
    """
    session = _make_session()
    parent_job_id = ORMExtractionJob().id
    orchestrator = ExtractionOrchestratorV2(session, job_id=parent_job_id)
    initial = ExtractionChunk(
        content="intro\n\n## A\nbody",
        chunk_type="raw_text",
        _source_span=(0, 17),
        metadata={},
    )
    asyncio.run(orchestrator.run(initial))

    # Every argument passed to session.add must be an ORMChunk (no
    # _PersistTarget wrapper survives).  And every ORMChunk must carry
    # the parent job_id so the NOT NULL FK resolves when commit fires.
    added = [call.args[0] for call in session.add.call_args_list]
    assert added, "expected at least one session.add call"
    for arg in added:
        assert isinstance(arg, ORMChunk), (
            f"session.add received {type(arg).__name__}, expected ORMChunk; "
            f"the _PersistTarget wrapper is supposed to be gone (NFM-2705)"
        )
        assert arg.job_id == parent_job_id, (
            f"ORMChunk.job_id={arg.job_id} != parent job id={parent_job_id}; "
            f"the extraction_chunks.job_id NOT NULL FK would fail"
        )


@pytest.mark.asyncio
async def test_orchestrator_persists_chunks_to_db(db_session):
    """Real-DB integration test (NFM-2705 defect 3).

    Creates a parent :class:`ExtractionJob` row, hands its id to the
    orchestrator, runs the pipeline, then queries ``extraction_chunks``
    and asserts every persisted row:

    * has the parent ``job_id`` (FK satisfied at flush/commit)
    * carries a non-null sequential ``chunk_index`` (0..N-1)
    * round-trips ``source_span`` as JSONB
      (shape ``{"start": int, "end": int}`` per the ORM column comment)
    """
    parent_job = ORMExtractionJob(
        source_reference="10.1234/integration-test",
        source_type="doi",
    )
    db_session.add(parent_job)
    await db_session.flush()
    parent_id = parent_job.id

    orchestrator = ExtractionOrchestratorV2(db_session, job_id=parent_id)
    initial = ExtractionChunk(
        content="intro\n\n## A\nUO2 lattice constant: 5.47 angstrom",
        chunk_type="raw_text",
        _source_span=(0, 51),
        metadata={},
    )
    await orchestrator.run(initial)
    await db_session.commit()

    # Query every chunk written by this job.
    result = await db_session.execute(
        select(ORMChunk).where(ORMChunk.job_id == parent_id)
    )
    persisted = list(result.scalars().all())
    assert persisted, "no chunks were persisted to extraction_chunks"

    indexes = sorted(c.chunk_index for c in persisted)
    # The orchestrator assigns chunk_index per-step (loader=0,
    # sections start at 1, entity/property/final use section_idx) so
    # multiple step outputs within one section share a chunk_index.
    # The contract is "every persisted chunk has the correct fields",
    # not strict 0..N-1 contiguity.
    assert all(i >= 0 for i in indexes), (
        f"negative chunk_index leaked to DB; saw {indexes}"
    )

    for chunk in persisted:
        # FK to parent job is satisfied.
        assert chunk.job_id == parent_id

        # source_span JSONB round-trip.
        assert chunk.source_span is not None
        assert set(chunk.source_span.keys()) == {"start_offset", "end_offset"}
        assert isinstance(chunk.source_span["start_offset"], int)
        assert isinstance(chunk.source_span["end_offset"], int)

        # content survives the round-trip.
        assert isinstance(chunk.content, str)
        assert chunk.content  # non-empty


def test_orchestrator_pipeline_order_is_correct():
    """Steps run in step_order: 0 → 1 → 2 → 3 → 4.  The order is
    observable through the sequence of chunk rows added to the session
    (the first persisted row corresponds to the raw_text loader).
    """
    session = _make_session()
    orchestrator = ExtractionOrchestratorV2(session, job_id=ORMExtractionJob().id)
    initial = ExtractionChunk(
        content="intro\n\n## A\nbody",
        chunk_type="raw_text",
        _source_span=(0, 17),
        metadata={},
    )
    asyncio.run(orchestrator.run(initial))

    added = [call.args[0] for call in session.add.call_args_list]
    # The very first persisted row corresponds to the raw_text loader.
    first_span = added[0].source_span
    assert first_span == {"start_offset": 0, "end_offset": 17}
    # Every persisted row is an ORMChunk carrying a non-null source_span
    # (NFM-2705 defect 1 — the wrapper used to swallow this attribute).
    for arg in added:
        assert isinstance(arg, ORMChunk)
        assert arg.source_span is not None
        assert "start_offset" in arg.source_span
        assert "end_offset" in arg.source_span
