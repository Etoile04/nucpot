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
    _as_span_pair,
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


# ---------------------------------------------------------------------------
# NFM-2740: defensive normalization of source_span shapes.
#
# PR #730 SectionSegmenter (unmerged) emits dict-shaped spans
# (``{"start": int, "end": int}``); ChunkBuilder must tolerate both
# tuple/list and dict input so the strangler-fig integration wiring
# does not regress on shape mismatch.
# ---------------------------------------------------------------------------


def test_as_span_pair_passthrough_tuple():
    """Existing tuple input (NFM-2685 contract) is preserved exactly."""
    assert _as_span_pair((100, 122)) == (100, 122)


def test_as_span_pair_accepts_list():
    """List input is normalized to tuple (same semantics, hashable)."""
    assert _as_span_pair([5, 17]) == (5, 17)


def test_as_span_pair_accepts_dict_start_end():
    """Dict input ``{"start": int, "end": int}`` is normalized to tuple.

    PR #730 SectionSegmenter emits this shape; ``tuple(dict)`` would
    yield ``("start", "end")`` (the keys), which silently corrupts
    every downstream consumer that treats the result as offsets.
    """
    assert _as_span_pair({"start": 0, "end": 42}) == (0, 42)


def test_as_span_pair_preserves_ordering_not_dict_insertion_order():
    """``_as_span_pair`` must read by key, not by iteration order."""
    # ``{"end": 99, "start": 0}`` — reversed insertion order.
    assert _as_span_pair({"end": 99, "start": 0}) == (0, 99)


def test_as_span_pair_zero_length_segment():
    """Zero-length segments are valid (start == end)."""
    assert _as_span_pair({"start": 7, "end": 7}) == (7, 7)


def test_chunk_builder_accepts_dict_source_span():
    """ChunkBuilder.execute tolerates dict-shaped _source_span input.

    Mirrors the post-integration contract: SectionSegmenter (PR #730)
    will emit dict spans, and ChunkBuilder must normalize before
    constructing the final ExtractionChunk.
    """
    step = ChunkBuilder()
    # Build a source chunk via ``object.__new__`` to bypass the
    # ExtractionChunk tuple validator — simulating the integration
    # boundary where SectionSegmenter hands us a dict.
    src = ExtractionChunk.__new__(ExtractionChunk)
    object.__setattr__(src, "content", "UO2 lattice: 5.47 Å")
    object.__setattr__(src, "chunk_type", "property")
    object.__setattr__(src, "_source_span", {"start": 100, "end": 122})
    object.__setattr__(src, "metadata", {})
    object.__setattr__(src, "parent_chunk_id", None)
    out = step.execute(src)
    assert out._source_span == (100, 122)
    assert out.chunk_type == "final"


def test_chunk_builder_accepts_list_source_span():
    """ChunkBuilder.execute tolerates list-shaped _source_span input."""
    step = ChunkBuilder()
    src = ExtractionChunk.__new__(ExtractionChunk)
    object.__setattr__(src, "content", "x")
    object.__setattr__(src, "chunk_type", "property")
    object.__setattr__(src, "_source_span", [0, 10])
    object.__setattr__(src, "metadata", {})
    object.__setattr__(src, "parent_chunk_id", None)
    out = step.execute(src)
    assert out._source_span == (0, 10)
