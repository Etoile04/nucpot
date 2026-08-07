"""Self-developed text chunker with ``source_span`` tracking (NFM-2567-T3).

Replaces the paragraph-only ``_chunk_content()`` in ``extraction_pipeline.py``
with a strategy-based chunker that records character offsets for every chunk,
enabling the review UI to highlight source locations.

Design inspired by LightRAG's ``split_by_chunk_size()`` pattern — no external
dependency is imported.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CHUNKER_V2_AVAILABLE",
    "ExtractionChunkData",
    "chunk_text",
]

# Module-level feature flag — checked by callers to decide whether to use
# the new chunker (Phase 1B wires this into extraction_pipeline.py).
CHUNKER_V2_AVAILABLE = True

StrategyName = Literal["paragraph", "sentence", "section"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionChunkData:
    """Immutable record for a single text chunk with provenance metadata.

    Attributes:
        content: The chunk text (substring of the original document).
        source_span: Character-level offsets ``{start, end}`` into the
            original text.  ``text[start:end] == content`` is guaranteed.
        token_estimate: Rough token count using a chars-to-tokens heuristic
            (``len(content) // 4``).  No external tokenizer dependency.
    """

    content: str
    source_span: dict[str, int]
    token_estimate: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_chunk(text: str, start: int, end: int) -> ExtractionChunkData:
    """Build an ``ExtractionChunkData`` from a text slice."""
    return ExtractionChunkData(
        content=text[start:end],
        source_span={"start": start, "end": end},
        token_estimate=(end - start) // 4,
    )


def _chunk_with_spans(
    text: str,
    segments: list[tuple[int, int]],
    max_chars: int,
) -> list[ExtractionChunkData]:
    """Merge consecutive segments into chunks respecting *max_chars*."""
    chunks: list[ExtractionChunkData] = []
    acc_start: int | None = None
    acc_end: int = 0

    for seg_start, seg_end in segments:
        seg_len = seg_end - seg_start

        if seg_len > max_chars:
            # Flush accumulator first
            if acc_start is not None:
                chunks.append(_make_chunk(text, acc_start, acc_end))
                acc_start = None
                acc_end = 0
            # Hard-split the oversized segment
            chunks.extend(_hard_split_region(text, seg_start, seg_end, max_chars))
            continue

        candidate_start = acc_start if acc_start is not None else seg_start
        candidate_end = seg_end

        if candidate_end - candidate_start <= max_chars:
            acc_start = candidate_start
            acc_end = candidate_end
        else:
            # Flush accumulator
            if acc_start is not None:
                chunks.append(_make_chunk(text, acc_start, acc_end))
            # Start new accumulator with this segment
            acc_start = seg_start
            acc_end = seg_end

    # Flush remaining
    if acc_start is not None:
        chunks.append(_make_chunk(text, acc_start, acc_end))

    return chunks if chunks else [_make_chunk(text, 0, len(text))]


def _hard_split_region(
    text: str,
    region_start: int,
    region_end: int,
    max_chars: int,
) -> list[ExtractionChunkData]:
    """Hard-split a text region [region_start, region_end) into max_chars pieces.

    Tries to break at sentence boundaries (``. ``, ``! ``, ``? ``).
    Falls back to character-level cut when no suitable boundary exists.
    """
    chunks: list[ExtractionChunkData] = []
    cursor = region_start

    while cursor < region_end:
        remaining = region_end - cursor
        if remaining <= max_chars:
            chunks.append(_make_chunk(text, cursor, region_end))
            break

        window_end = cursor + max_chars
        window = text[cursor:window_end]

        # Find the last sentence boundary in the latter half of the window.
        best: int = -1
        for m in re.finditer(r"[.!?]\s", window):
            if m.start() >= max_chars // 2:
                best = m.end()  # split after the space
            elif best == -1:
                # Keep the earliest boundary as fallback (before the latter half)
                best = m.end()

        if best > 0 and best < max_chars:
            split_at = cursor + best
            chunks.append(_make_chunk(text, cursor, split_at))
            cursor = split_at
        else:
            # Hard cut at max_chars
            chunks.append(_make_chunk(text, cursor, window_end))
            cursor = window_end

    return chunks


# ---------------------------------------------------------------------------
# Segmenters — each returns a list of (start, end) tuples
# ---------------------------------------------------------------------------


def _segment_paragraphs(text: str) -> list[tuple[int, int]]:
    """Split on double newlines (``\\n\\n``).

    The delimiter (``\\n\\n``) is included at the *end* of each segment
    so that concatenated chunks reproduce the original text exactly.
    """
    segments: list[tuple[int, int]] = []
    parts = re.split(r"\n\n", text)
    cursor = 0
    for i, part in enumerate(parts):
        end = cursor + len(part)
        # Include the "\n\n" delimiter after each segment (except the last)
        delimiter = 2 if i < len(parts) - 1 else 0
        segments.append((cursor, end + delimiter))
        cursor = end + delimiter
    return segments


def _segment_sentences(text: str) -> list[tuple[int, int]]:
    """Split on sentence boundaries (``. ``, ``! ``, ``? ``).

    The punctuation mark AND trailing whitespace are included at the end
    of each segment so that concatenated chunks reproduce the original
    text exactly.
    """
    segments: list[tuple[int, int]] = []
    last_end = 0
    for m in re.finditer(r"[.!?]\s+", text):
        end = m.end()  # include trailing whitespace for contiguity
        if end > last_end:
            segments.append((last_end, end))
        last_end = end
    if last_end < len(text):
        segments.append((last_end, len(text)))
    return segments if segments else [(0, len(text))]


def _segment_sections(text: str) -> list[tuple[int, int]]:
    """Split on markdown headings (``# ``, ``## ``, ``### ``, etc.)."""
    pattern = re.compile(r"\n(?=#{1,6}\s)")
    parts = list(pattern.finditer(text))

    if not parts:
        return [(0, len(text))]

    segments: list[tuple[int, int]] = [(0, parts[0].start())]
    for i, m in enumerate(parts):
        start = m.start()  # include the \n before heading
        end = parts[i + 1].start() if i + 1 < len(parts) else len(text)
        segments.append((start, end))

    return segments


_SEGMENTERS: dict[StrategyName, Callable[[str], list[tuple[int, int]]]] = {
    "paragraph": _segment_paragraphs,
    "sentence": _segment_sentences,
    "section": _segment_sections,
}

_VALID_STRATEGIES = frozenset(_SEGMENTERS.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    strategy: StrategyName = "paragraph",
    max_chars: int = 20_000,
) -> list[ExtractionChunkData]:
    """Split *text* into chunks with source-span tracking.

    Each chunk records the exact character offset (``start``, ``end``) in
    the original text, enabling downstream consumers (e.g. the review UI)
    to highlight the source location.

    Args:
        text: The full document text to chunk.
        strategy: One of ``"paragraph"`` (split on ``\\n\\n``),
            ``"sentence"`` (split on ``. ``, ``! ``, ``? ``), or
            ``"section"`` (split on markdown headings).
        max_chars: Maximum characters per chunk.  When a single segment
            exceeds this, it is hard-split at the nearest sentence
            boundary (or character boundary as a last resort).

    Returns:
        A list of :class:`ExtractionChunkData` instances.  At least one
        chunk is always returned (even for empty *text*).  Chunks cover
        the original text contiguously with no gaps or overlaps.

    Raises:
        ValueError: If *strategy* is not one of the supported values.
    """
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"Unknown strategy {strategy!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_STRATEGIES))}"
        )

    if not text or len(text) <= max_chars:
        return [_make_chunk(text, 0, len(text))]

    segmenter = _SEGMENTERS[strategy]
    segments = segmenter(text)
    return _chunk_with_spans(text, segments, max_chars)
