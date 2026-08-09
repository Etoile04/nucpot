"""Unit tests for SectionSegmenter — NFM-2682.

Covers:

- ExtractionStep Protocol conformance (structural, no inheritance).
- Multiple sections from markdown headings.
- Numbered sections (``1. Title``, ``2) Title``).
- Empty input produces an empty section list.
- Provenance spans are valid (``text[span.start:span.end] == content``).
- Idempotent: same input yields identical output.
- Paragraph-only fallback when no structural markers exist.
- Single-chunk fallback for plain text with no markers at all.
- ``chunk_type`` and ``step_order`` metadata in outputs.
"""

from __future__ import annotations

import pytest

from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    is_extraction_step,
)

# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSectionSegmenterProtocol:
    """SectionSegmenter satisfies the ExtractionStep Protocol."""

    def test_satisfies_extraction_step_protocol(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        assert isinstance(step, ExtractionStep)
        assert is_extraction_step(step)

    def test_step_type_is_chunk(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        assert SectionSegmenter.step_type == "chunk"

    def test_input_keys_includes_raw_text(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        assert "raw_text" in SectionSegmenter.input_keys


# ---------------------------------------------------------------------------
# Execution — core splitting behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSectionSegmenterExecution:
    """SectionSegmenter.execute() splits text into logical sections."""

    @pytest.mark.asyncio
    async def test_multiple_sections_from_markdown_headings(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "# Introduction\nThis is the intro.\n\n# Methods\nThe methods."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        assert result.skipped is False
        sections = result.outputs["sections"]
        assert len(sections) >= 2
        # Sections must cover the original text contiguously.
        all_content = "".join(s["content"] for s in sections)
        assert all_content == text

    @pytest.mark.asyncio
    async def test_numbered_sections(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "1. First section\nContent here.\n\n2. Second section\nMore content."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        assert len(sections) >= 2
        all_content = "".join(s["content"] for s in sections)
        assert all_content == text

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list(self) -> None:
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        ctx = StepContext(job_id="job-1", values={"raw_text": ""})

        result = await step.execute(ctx)

        assert result.outputs["sections"] == []
        assert result.skipped is False

    @pytest.mark.asyncio
    async def test_provenance_spans_valid(self) -> None:
        """Each section's source_span must satisfy text[start:end] == content."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "# Introduction\nIntro text.\n\n# Methods\nMethods text."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        for section in sections:
            span = section["source_span"]
            content = section["content"]
            assert text[span["start"] : span["end"]] == content

    @pytest.mark.asyncio
    async def test_idempotent(self) -> None:
        """Re-segmenting the same input yields the same chunk list."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "# Section A\nContent A.\n\n# Section B\nContent B."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result1 = await step.execute(ctx)
        result2 = await step.execute(ctx)

        assert result1.outputs["sections"] == result2.outputs["sections"]

    @pytest.mark.asyncio
    async def test_paragraph_fallback(self) -> None:
        """When no headings/numbers, fall back to blank-line paragraphs."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        assert len(sections) >= 2
        all_content = "".join(s["content"] for s in sections)
        assert all_content == text

    @pytest.mark.asyncio
    async def test_single_chunk_no_markers(self) -> None:
        """Plain text with no structural markers → one chunk."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "Just a plain block of text with no structural markers."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        assert len(sections) == 1
        assert sections[0]["content"] == text
        assert sections[0]["source_span"] == {
            "start": 0,
            "end": len(text),
        }

    @pytest.mark.asyncio
    async def test_chunk_type_and_step_order_in_outputs(self) -> None:
        """Outputs carry chunk_type='section' and step_order=2."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "# Section\nContent."
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        assert result.outputs["chunk_type"] == "section"
        assert result.outputs["step_order"] == 2

    @pytest.mark.asyncio
    async def test_produced_keys(self) -> None:
        """StepResult reports 'sections' as a produced key."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        ctx = StepContext(job_id="job-1", values={"raw_text": "hello"})

        result = await step.execute(ctx)

        assert "sections" in result.produced_keys

    @pytest.mark.asyncio
    async def test_does_not_mutate_context(self) -> None:
        """execute() must not mutate the input StepContext."""
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        ctx = StepContext(job_id="job-1", values={"raw_text": "# A\nB"})
        original_values = dict(ctx.values)

        await step.execute(ctx)

        assert ctx.values == original_values


# ---------------------------------------------------------------------------
# Regression tests — CR #1 (boundary \n provenance) and CR #2 (numbered
# over-match) on PR #730.  These tests fail on the original implementation
# and lock in the fix.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSectionSegmenterCRRegressions:
    """Regression tests for Code Review findings on PR #730."""

    @pytest.mark.asyncio
    async def test_boundary_section_does_not_start_with_newline(self) -> None:
        """CR #1: a heading-initiated section must not start with the
        boundary ``\\n`` (it semantically belongs to the previous section
        as the paragraph separator, not to the new heading's content).
        """
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "para 1\n# Heading\nstuff"
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        assert len(sections) == 2
        # The second section is the heading; it must not start with \n.
        assert not sections[1]["content"].startswith("\n")
        # And the heading's content should begin with the heading marker.
        assert sections[1]["content"].startswith("# Heading")

    @pytest.mark.asyncio
    async def test_boundary_section_starts_at_heading_marker(self) -> None:
        """CR #1 (provenance): the heading section's source_span.start
        must point at the ``#`` character, not at the preceding ``\\n``.
        """
        from nfm_db.pipeline.section_segmenter import SectionSegmenter

        step = SectionSegmenter()
        text = "para 1\n# Heading\nstuff"
        ctx = StepContext(job_id="job-1", values={"raw_text": text})

        result = await step.execute(ctx)

        sections = result.outputs["sections"]
        # Confirm the heading-section start aligns with the '#' in text.
        hash_pos = text.index("#")
        assert sections[1]["source_span"]["start"] == hash_pos

    @pytest.mark.asyncio
    async def test_year_like_number_at_line_start_not_a_boundary(self) -> None:
        """CR #2: a 4-digit year at line start (e.g. ``2023. We did X``)
        must NOT be treated as a numbered section.  ``d{1,3}`` (≤ 3
        digits) is the key constraint: a 4-digit year fails to match the
        numbered-section pattern, so the document falls through to
        paragraph splitting.
        """
        from nfm_db.pipeline.section_segmenter import _SECTION_BOUNDARY

        text = "\n2023. We did important work this year."
        matches = list(_SECTION_BOUNDARY.finditer(text))
        assert matches == [], (
            f"_SECTION_BOUNDARY should not match year-like numbers, "
            f"got: {[text[m.start():m.end()] for m in matches]}"
        )

    @pytest.mark.asyncio
    async def test_lowercase_numbered_line_not_a_boundary(self) -> None:
        """CR #2: a numbered line whose title starts with a lowercase
        letter (e.g. ``1. the new normal``) is prose, not a section
        header.  The ``[A-Z]`` constraint on the title's first char
        blocks this.
        """
        from nfm_db.pipeline.section_segmenter import _SECTION_BOUNDARY

        text = "\n1. the new normal in 2024."
        matches = list(_SECTION_BOUNDARY.finditer(text))
        assert matches == [], (
            f"_SECTION_BOUNDARY should not match lowercase-titled lines, "
            f"got: {[text[m.start():m.end()] for m in matches]}"
        )

    @pytest.mark.asyncio
    async def test_numbered_section_with_capital_title_still_matches(self) -> None:
        """Sanity check that the tightened regex still recognises the
        cases it is meant to recognise (``1. Introduction``,
        ``2) Methods``).
        """
        from nfm_db.pipeline.section_segmenter import _SECTION_BOUNDARY

        for text in ("\n1. Introduction", "\n2) Methods", "\n10. Results"):
            matches = list(_SECTION_BOUNDARY.finditer(text))
            assert matches, f"expected boundary match in {text!r}"
