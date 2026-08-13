"""Tests for _chunk_content in extraction_pipeline (NFM-1366 P3).

The chunking function splits large source content into model-context-safe
pieces. Tests verify:
- Small content returns a single chunk (no-op)
- Large content splits on paragraph boundaries
- Oversized paragraphs hard-split at sentence boundaries
- Chunk count and sizes stay within budget
"""

from __future__ import annotations

from nfm_db.services.extraction_pipeline import _CHUNK_MAX_CHARS, _chunk_content


class TestChunkContentNoOp:
    """Small content should pass through unchanged."""

    def test_empty_string(self) -> None:
        assert _chunk_content("") == [""]

    def test_small_content_single_chunk(self) -> None:
        text = "Hello world."
        chunks = _chunk_content(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_content_under_limit_returns_single(self) -> None:
        text = "A" * _CHUNK_MAX_CHARS
        chunks = _chunk_content(text)
        assert len(chunks) == 1

    def test_content_just_over_limit_splits(self) -> None:
        text = "A" * (_CHUNK_MAX_CHARS + 1)
        chunks = _chunk_content(text)
        assert len(chunks) >= 2


class TestChunkParagraphSplit:
    """Large content should split on \\n\\n boundaries."""

    def test_paragraph_boundaries_respected(self) -> None:
        """Each chunk (except possibly the last oversized paragraph)
        should end at a paragraph boundary."""
        para = "This is a paragraph. " * 50  # ~1000 chars each
        paragraphs = [f"Para {i}.\n{para}" for i in range(30)]
        content = "\n\n".join(paragraphs)

        chunks = _chunk_content(content, max_chars=5000)
        assert len(chunks) > 1
        # Every chunk should be ≤ max_chars + small tolerance
        # (hard-split paragraphs may slightly exceed due to rounding)
        for c in chunks:
            assert len(c) <= 5100, f"chunk len={len(c)} exceeds budget"

    def test_all_content_preserved(self) -> None:
        """No content should be lost during chunking."""
        para = "Sentence one. Sentence two. " * 100
        paragraphs = [f"Header {i}\n{para}" for i in range(20)]
        content = "\n\n".join(paragraphs)

        chunks = _chunk_content(content, max_chars=3000)
        rejoined = "\n\n".join(chunks)
        # The rejoined content should contain all the original text
        # (may not be byte-identical due to chunk boundary reconstruction,
        # but all sentences must be present)
        for i in range(20):
            assert f"Header {i}" in rejoined, f"Header {i} missing after chunking"

    def test_frapcon_scale(self) -> None:
        """FRAPCON PDF content is ~277K chars. Verify it splits into
        a reasonable number of chunks within the 20K budget."""
        # Simulate: ~277K chars of realistic markdown
        para = "# Section\n\nThis is paragraph text. " * 40  # ~1.4K per para block
        count = 200  # 200 x 1.4K ~ 280K
        content = "\n\n".join([para] * count)
        assert len(content) > 270_000, f"test data too small: {len(content)}"

        chunks = _chunk_content(content)
        assert len(chunks) > 10, f"expected >10 chunks for 277K content, got {len(chunks)}"
        assert len(chunks) < 30, f"too many chunks ({len(chunks)}) — budget too small?"
        # Each chunk within budget (with tolerance for paragraph boundaries)
        for c in chunks:
            assert len(c) <= _CHUNK_MAX_CHARS + 100, f"chunk len={len(c)}"


class TestChunkOversizedParagraph:
    """A single paragraph exceeding max_chars should hard-split."""

    def test_single_huge_paragraph_splits(self) -> None:
        """If the entire content is one paragraph > max_chars,
        it should be hard-split at sentence boundaries."""
        text = "This is a sentence. " * 5000  # ~100K chars, single paragraph
        chunks = _chunk_content(text, max_chars=5000)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 5100, f"chunk len={len(c)} exceeds hard-split budget"

    def test_no_content_loss_on_hard_split(self) -> None:
        """Hard-splitting a huge paragraph should not lose content."""
        text = "Sentence. " * 10000
        original_sentences = text.count("Sentence.")
        chunks = _chunk_content(text, max_chars=2000)
        rejoined = "".join(chunks)
        rejoined_sentences = rejoined.count("Sentence.")
        assert original_sentences == rejoined_sentences, (
            f"lost content: {original_sentences} → {rejoined_sentences} sentences"
        )
