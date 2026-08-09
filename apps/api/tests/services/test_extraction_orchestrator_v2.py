"""TDD tests for the V2 ExtractionOrchestrator (NFM-2677 B7).

Composes the 5 strangler-fig steps (RawTextLoader →
SectionSegmenter → EntityExtractor → PropertyNormalizer →
ChunkBuilder) and persists each emitted chunk to the ORM
``extraction_chunks`` table.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# RED: this import will fail until B7 ships.
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
    orchestrator = ExtractionOrchestratorV2(session)
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
    orchestrator = ExtractionOrchestratorV2(session)
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
    orchestrator = ExtractionOrchestratorV2(session)
    initial = ExtractionChunk(
        content="plain text only",
        chunk_type="raw_text",
        _source_span=(0, 15),
        metadata={},
    )
    finals = asyncio.run(orchestrator.run(initial))
    assert len(finals) == 1
    assert finals[0].chunk_type == "final"


def test_orchestrator_pipeline_order_is_correct():
    """Steps run in step_order: 0 → 1 → 2 → 3 → 4.  The order is
    observable through the sequence of chunk types added to the
    session."""
    session = _make_session()
    orchestrator = ExtractionOrchestratorV2(session)
    initial = ExtractionChunk(
        content="intro\n\n## A\nbody",
        chunk_type="raw_text",
        _source_span=(0, 17),
        metadata={},
    )
    asyncio.run(orchestrator.run(initial))

    seen_types: list[str] = []
    for call in session.add.call_args_list:
        arg = call.args[0]
        if hasattr(arg, "chunk_type"):
            seen_types.append(arg.chunk_type)
    for required in ("raw_text", "section", "entity", "property", "final"):
        assert required in seen_types, (
            f"expected {required!r} in pipeline; saw {seen_types}"
        )
    assert seen_types[0] == "raw_text"
    assert seen_types[-1] == "final"
