"""TDD tests for the ChunkBuilder step (NFM-2677 B6).

Final step: assembles the per-section property chunk into a single
final chunk.  Sets ``chunk_type='final'`` and aggregates entity
counts into ``metadata['summary']``.
"""

from __future__ import annotations

# RED: this import will fail until B6 ships.
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.chunk_builder import (
    ChunkBuilder,
)


def test_chunk_builder_step_name():
    assert ChunkBuilder().step_name == "chunk_builder"


def test_chunk_builder_step_order_is_four():
    assert ChunkBuilder().step_order == 4


def test_chunk_builder_returns_final_chunk_type():
    step = ChunkBuilder()
    src = ExtractionChunk(
        content="final text",
        chunk_type="property",
        _source_span=(0, 10),
        metadata={},
    )
    out = step.execute(src)
    assert out.chunk_type == "final"


def test_chunk_builder_preserves_content_and_span():
    step = ChunkBuilder()
    src = ExtractionChunk(
        content="UO2 lattice: 5.47 Å",
        chunk_type="property",
        _source_span=(100, 122),
        metadata={},
    )
    out = step.execute(src)
    assert out.content == src.content
    assert out._source_span == (100, 122)


def test_chunk_builder_aggregates_entity_counts():
    """Per-chunk summary block counts formulas, properties, and
    measurements so downstream consumers can size the chunk."""
    step = ChunkBuilder()
    src = ExtractionChunk(
        content="x",
        chunk_type="property",
        _source_span=(0, 1),
        metadata={
            "entities": {
                "formulas": ["UO2", "U3O8"],
                "properties": ["lattice_constant", "melting_point"],
                "measurements": ["5.47 Å", "3120 K"],
            }
        },
    )
    out = step.execute(src)
    summary = out.metadata.get("summary", {})
    assert summary == {
        "formula_count": 2,
        "property_count": 2,
        "measurement_count": 2,
    }


def test_chunk_builder_handles_missing_entities_metadata():
    """A chunk without ``entities`` (e.g., from a no-match section)
    still produces a valid final chunk with zero-count summary."""
    step = ChunkBuilder()
    src = ExtractionChunk(
        content="no entities here",
        chunk_type="property",
        _source_span=(0, 17),
        metadata={},
    )
    out = step.execute(src)
    assert out.metadata["summary"] == {
        "formula_count": 0,
        "property_count": 0,
        "measurement_count": 0,
    }


def test_chunk_builder_is_idempotent():
    step = ChunkBuilder()
    src = ExtractionChunk(
        content="x",
        chunk_type="property",
        _source_span=(0, 1),
        metadata={
            "entities": {
                "formulas": ["UO2"],
                "properties": ["lattice_constant"],
                "measurements": ["5.47 Å"],
            }
        },
    )
    once = step.execute(src)
    twice = step.execute(once)
    assert once == twice
