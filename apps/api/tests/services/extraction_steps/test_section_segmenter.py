"""TDD tests for the SectionSegmenter step (NFM-2677 B3).

Strangler-fig pipeline decomposition — Step 2: SectionSegmenter.
Splits normalized raw text into logical sections (one per ``## ``
heading boundary or blank-line paragraph break).  Each emitted
section is its own ``ExtractionChunk`` with ``chunk_type='section'``
and a precise ``_source_span`` anchored to the original document.

The step is a 1→N fan-out.  The orchestrator (B7) discovers fan-out
steps via the ``execute_many`` method, so the segmenter must
implement it explicitly while keeping ``execute`` available as the
single-chunk interface defined by the ABC.
"""

from __future__ import annotations

# RED: this import will fail until B3 ships.
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.section_segmenter import (
    SectionSegmenter,
)


def test_section_segmenter_step_name():
    assert SectionSegmenter().step_name == "section_segmenter"


def test_section_segmenter_step_order_is_one():
    assert SectionSegmenter().step_order == 1


def test_section_segmenter_splits_on_heading_boundaries():
    """Markdown ``## `` headings split the input into discrete sections."""
    step = SectionSegmenter()
    src = ExtractionChunk(
        content=(
            "intro paragraph one\n"
            "\n"
            "## Background\n"
            "background paragraph\n"
            "\n"
            "## Methods\n"
            "methods paragraph"
        ),
        chunk_type="raw_text",
        _source_span=(0, 88),
        metadata={"normalized": True},
    )
    sections = step.execute_many(src)
    assert [s.content for s in sections] == [
        "intro paragraph one",
        "## Background\nbackground paragraph",
        "## Methods\nmethods paragraph",
    ]


def test_section_segmenter_assigns_correct_source_spans():
    """Each section's ``_source_span`` points back into the input chunk.

    Spans are computed as ``(start, start + len(body))`` so they
    describe the exact bytes the section body occupies in the
    original document — the trailing blank line that separates the
    section from the next one is excluded from BOTH spans.
    """
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="AAA\n\n## BBB\nBBB-body\n\n## CCC\nCCC-body",
        chunk_type="raw_text",
        _source_span=(0, 37),
        metadata={},
    )
    sections = step.execute_many(src)
    assert [s._source_span for s in sections] == [(0, 3), (5, 20), (22, 37)]
    # Every span is inside the source span.
    parent_start, parent_end = src._source_span
    for s in sections:
        start, end = s._source_span
        assert parent_start <= start <= end <= parent_end


def test_section_segmenter_marks_chunks_as_section_type():
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="para one\n\npara two",
        chunk_type="raw_text",
        _source_span=(0, 18),
        metadata={},
    )
    sections = step.execute_many(src)
    assert all(s.chunk_type == "section" for s in sections)


def test_section_segmenter_records_section_index_in_metadata():
    """Section index in metadata drives downstream re-runs to land
    each section in the same slot — even if the parent chunk's
    ``_source_span`` drifts across runs."""
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="intro\n\n## A\nA-body\n\n## B\nB-body\n\n## C\nC-body",
        chunk_type="raw_text",
        _source_span=(0, 47),
        metadata={},
    )
    sections = step.execute_many(src)
    indices = [s.metadata.get("section_index") for s in sections]
    assert indices == [0, 1, 2, 3]


def test_section_segmenter_links_children_to_parent():
    """Each section's ``parent_chunk_id`` should reference its upstream
    raw_text chunk for lineage stitching."""
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="x\n\ny",
        chunk_type="raw_text",
        _source_span=(0, 4),
        metadata={},
        parent_chunk_id="root-raw",
    )
    sections = step.execute_many(src)
    assert all(s.parent_chunk_id == "root-raw" for s in sections)


def test_section_segmenter_is_idempotent():
    """Re-segmenting an already-segmented input — where each section
    has its own span — must produce sections whose content equals the
    input content (degenerate 1-section case)."""
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="single-section body",
        chunk_type="raw_text",
        _source_span=(0, 19),
        metadata={},
    )
    once = step.execute_many(src)
    # The single-section case: no boundaries → 1 section.
    assert len(once) == 1
    assert once[0].content == "single-section body"


def test_section_segmenter_execute_returns_first_section():
    """The ABC contract — ``execute()`` returns a single chunk — must
    hold.  For fan-out steps, ``execute`` returns the first emitted
    section so the contract stays uniform; the orchestrator uses
    ``execute_many`` to fan-out."""
    step = SectionSegmenter()
    src = ExtractionChunk(
        content="alpha\n\n## beta\nbeta-body",
        chunk_type="raw_text",
        _source_span=(0, 23),
        metadata={},
    )
    single = step.execute(src)
    assert single.chunk_type == "section"
    assert single.content == "alpha"
