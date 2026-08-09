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
