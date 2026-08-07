"""Tests for nfm_db.services.chunker — self-developed chunker with source_span tracking.

TDD: these tests are written BEFORE the implementation.
"""

from __future__ import annotations

import pytest

from nfm_db.services.chunker import (
    CHUNKER_V2_AVAILABLE,
    ExtractionChunkData,
    chunk_text,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_chunker_v2_available(self) -> None:
        assert CHUNKER_V2_AVAILABLE is True


# ---------------------------------------------------------------------------
# ExtractionChunkData dataclass
# ---------------------------------------------------------------------------


class TestExtractionChunkData:
    def test_frozen(self) -> None:
        chunk = ExtractionChunkData(
            content="hello",
            source_span={"start": 0, "end": 5},
            token_estimate=1,
        )
        with pytest.raises(AttributeError):
            chunk.content = "changed"  # type: ignore[misc]

    def test_fields(self) -> None:
        chunk = ExtractionChunkData(
            content="abc",
            source_span={"start": 10, "end": 13},
            token_estimate=0,
        )
        assert chunk.content == "abc"
        assert chunk.source_span == {"start": 10, "end": 13}
        assert chunk.token_estimate == 0

    def test_equality(self) -> None:
        a = ExtractionChunkData("x", {"start": 0, "end": 1}, 0)
        b = ExtractionChunkData("x", {"start": 0, "end": 1}, 0)
        assert a == b


# ---------------------------------------------------------------------------
# chunk_text — paragraph strategy (default)
# ---------------------------------------------------------------------------


class TestParagraphStrategy:
    def test_short_text_single_chunk(self) -> None:
        text = "Hello world"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world"
        assert chunks[0].source_span == {"start": 0, "end": 11}
        assert chunks[0].token_estimate == 11 // 4

    def test_two_paragraphs_within_limit(self) -> None:
        text = "First paragraph.\n\nSecond paragraph."
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0].content == text
        assert chunks[0].source_span == {"start": 0, "end": len(text)}

    def test_paragraphs_split_at_boundary(self) -> None:
        para_a = "A" * 12_000
        para_b = "B" * 12_000
        text = f"{para_a}\n\n{para_b}"
        chunks = chunk_text(text, max_chars=15_000)
        assert len(chunks) == 2
        # First chunk includes para_a + delimiter for contiguity
        assert chunks[0].source_span == {"start": 0, "end": 12_002}
        assert text[chunks[0].source_span["start"] : chunks[0].source_span["end"]] == chunks[0].content
        # Second chunk is para_b
        assert chunks[1].source_span == {"start": 12_002, "end": 12_002 + 12_000}
        assert chunks[1].content == para_b

    def test_source_span_contiguity(self) -> None:
        """Chunks must cover the entire text with no gaps or overlaps."""
        text = "Short.\n\n" + "M" * 25_000 + "\n\nEnd."
        chunks = chunk_text(text, max_chars=15_000)
        # Verify contiguous coverage
        assert chunks[0].source_span["start"] == 0
        for i in range(1, len(chunks)):
            assert chunks[i].source_span["start"] == chunks[i - 1].source_span["end"], (
                f"Gap between chunk {i - 1} and {i}"
            )
        assert chunks[-1].source_span["end"] == len(text)
        # Verify each chunk's content matches the span
        for chunk in chunks:
            assert text[chunk.source_span["start"] : chunk.source_span["end"]] == chunk.content

    def test_empty_text(self) -> None:
        chunks = chunk_text("")
        assert len(chunks) == 1
        assert chunks[0].content == ""
        assert chunks[0].source_span == {"start": 0, "end": 0}

    def test_single_newline_not_split(self) -> None:
        """Single newline should NOT split — only double newline."""
        text = "Line one\nLine two\nLine three"
        chunks = chunk_text(text)
        assert len(chunks) == 1
        assert "\n" in chunks[0].content

    def test_overflow_paragraph_hard_split(self) -> None:
        """A single paragraph exceeding max_chars should be hard-split."""
        giant = "X" * 30_000
        chunks = chunk_text(giant, max_chars=20_000)
        assert len(chunks) >= 2
        # Verify contiguity
        assert chunks[0].source_span["start"] == 0
        for i in range(1, len(chunks)):
            assert chunks[i].source_span["start"] == chunks[i - 1].source_span["end"]
        assert chunks[-1].source_span["end"] == 30_000
        total_content = "".join(c.content for c in chunks)
        assert total_content == giant


# ---------------------------------------------------------------------------
# chunk_text — sentence strategy
# ---------------------------------------------------------------------------


class TestSentenceStrategy:
    def test_short_text_single_chunk(self) -> None:
        text = "Hello world."
        chunks = chunk_text(text, strategy="sentence")
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world."

    def test_split_on_sentence_boundaries(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        chunks = chunk_text(text, strategy="sentence", max_chars=30)
        assert len(chunks) >= 2
        # Verify contiguity
        assert chunks[0].source_span["start"] == 0
        for i in range(1, len(chunks)):
            assert chunks[i].source_span["start"] == chunks[i - 1].source_span["end"]
        assert chunks[-1].source_span["end"] == len(text)
        total = "".join(c.content for c in chunks)
        assert total == text

    def test_sentence_span_accuracy(self) -> None:
        text = "Alpha. Beta. Gamma."
        chunks = chunk_text(text, strategy="sentence", max_chars=10)
        for chunk in chunks:
            start = chunk.source_span["start"]
            end = chunk.source_span["end"]
            assert text[start:end] == chunk.content

    def test_exclamation_and_question_marks(self) -> None:
        text = "Wow! Really? Yes."
        chunks = chunk_text(text, strategy="sentence", max_chars=8)
        assert len(chunks) >= 2
        total = "".join(c.content for c in chunks)
        assert total == text


# ---------------------------------------------------------------------------
# chunk_text — section strategy
# ---------------------------------------------------------------------------


class TestSectionStrategy:
    def test_short_text_single_chunk(self) -> None:
        text = "# Heading\nSome content"
        chunks = chunk_text(text, strategy="section")
        assert len(chunks) == 1

    def test_split_on_headings(self) -> None:
        text = "# Section 1\nContent here.\n\n## Section 2\nMore content."
        chunks = chunk_text(text, strategy="section", max_chars=25)
        assert len(chunks) >= 2
        total = "".join(c.content for c in chunks)
        assert total == text

    def test_section_span_accuracy(self) -> None:
        text = "# A\nBody a.\n\n## B\nBody b."
        chunks = chunk_text(text, strategy="section", max_chars=15)
        for chunk in chunks:
            start = chunk.source_span["start"]
            end = chunk.source_span["end"]
            assert text[start:end] == chunk.content

    def test_heading_levels(self) -> None:
        """Should recognize #, ##, ###, etc."""
        text = "# H1\nc1\n\n## H2\nc2\n\n### H3\nc3"
        chunks = chunk_text(text, strategy="section", max_chars=10)
        assert len(chunks) >= 3
        total = "".join(c.content for c in chunks)
        assert total == text

    def test_section_contiguity(self) -> None:
        text = "# Intro\n" + "A" * 25_000 + "\n\n# Outro\nB"
        chunks = chunk_text(text, strategy="section", max_chars=15_000)
        assert chunks[0].source_span["start"] == 0
        for i in range(1, len(chunks)):
            assert chunks[i].source_span["start"] == chunks[i - 1].source_span["end"]
        assert chunks[-1].source_span["end"] == len(text)


# ---------------------------------------------------------------------------
# Cross-cutting: token_estimate
# ---------------------------------------------------------------------------


class TestTokenEstimate:
    def test_estimate_is_length_div_four(self) -> None:
        text = "A" * 100
        chunks = chunk_text(text)
        assert chunks[0].token_estimate == 100 // 4  # 25

    def test_estimate_for_multiple_chunks(self) -> None:
        text = "A" * 12_000 + "\n\n" + "B" * 12_000
        chunks = chunk_text(text, max_chars=15_000)
        assert len(chunks) == 2
        assert chunks[0].token_estimate == 12_000 // 4
        assert chunks[1].token_estimate == 12_000 // 4


# ---------------------------------------------------------------------------
# Cross-cutting: pure function
# ---------------------------------------------------------------------------


class TestPureFunction:
    def test_idempotent_calls(self) -> None:
        text = "Para one.\n\nPara two."
        result1 = chunk_text(text)
        result2 = chunk_text(text)
        assert result1 == result2

    def test_no_global_mutation(self) -> None:
        text = "A" * 25_000 + "\n\n" + "B" * 25_000
        _ = chunk_text(text, max_chars=15_000)
        # Second call should behave identically
        result = chunk_text(text, max_chars=15_000)
        total = "".join(c.content for c in result)
        assert total == text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_only_whitespace(self) -> None:
        chunks = chunk_text("   \n\n   ")
        assert len(chunks) >= 1

    def test_unicode_content(self) -> None:
        text = "材料数据\n\n核燃料信息"
        chunks = chunk_text(text)
        total = "".join(c.content for c in chunks)
        assert total == text
        for chunk in chunks:
            start = chunk.source_span["start"]
            end = chunk.source_span["end"]
            assert text[start:end] == chunk.content

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="strategy"):
            chunk_text("hello", strategy="nonexistent")

    def test_max_chars_one(self) -> None:
        text = "ABCDE"
        chunks = chunk_text(text, max_chars=1)
        assert all(len(c.content) >= 1 for c in chunks)
        total = "".join(c.content for c in chunks)
        assert total == text
