"""SectionSegmenter — Step 2 of the V2 extraction pipeline (NFM-2682).

Splits normalized text into logical sections based on structural
markers (markdown headings, numbered sections, blank-line
paragraphs).  Each section is emitted as a dict with ``content``
and ``source_span`` provenance into the original text.

Conforms to the :class:`nfm_db.pipeline.extraction_step.ExtractionStep`
Protocol via structural match — no base-class inheritance required.
"""

from __future__ import annotations

import re
from typing import Any

from nfm_db.pipeline.extraction_step import (
    StepContext,
    StepResult,
    validate_step_type,
)

__all__ = ["SectionSegmenter"]

# ---------------------------------------------------------------------------
# Section-boundary patterns
# ---------------------------------------------------------------------------

# Matches ``\\n`` immediately before:
#   - Markdown headings (``# Title``, ``## Sub``, …)
#   - Numbered sections (``1. Title``, ``2) Title``, …)
#
# Numbered-section pattern is constrained on two axes (CR #2 on PR #730):
#   1. ``\\d{1,3}`` — at most 3 digits, so 4-digit years (``2023.``) at
#      line start are NOT treated as section headers.
#   2. ``[A-Z]`` — the title's first character must be uppercase, so a
#      lowercase-prefixed numbered line (``1. the new normal``) is read
#      as prose, not a header.
_SECTION_BOUNDARY = re.compile(
    r"\n(?=#{1,6}\s)"
    r"|\n(?=\d{1,3}[.)]\s+[A-Z])"
)

# ---------------------------------------------------------------------------
# SectionSegmenter
# ---------------------------------------------------------------------------


class SectionSegmenter:
    """Split normalized text into logical sections.

    Structural conformance to :class:`ExtractionStep` Protocol
    (``step_type``, ``input_keys``, ``async execute``).  The step reads
    ``raw_text`` from the :class:`StepContext`, segments it, and returns
    one section descriptor per section in ``StepResult.outputs``.

    Segmentation strategy (in priority order):

    1. **Structural markers** — markdown headings (``# …``) and numbered
       sections (``1. …``, ``2) …``).
    2. **Paragraph fallback** — blank-line boundaries (``\\n\\n``) when no
       structural markers are found.
    3. **Single chunk** — the entire text as one section when neither
       strategy yields boundaries.
    """

    step_type: str = "chunk"
    input_keys: tuple[str, ...] = ("raw_text",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Split ``context["raw_text"]`` into sections.

        Returns a :class:`StepResult` with:

        - ``produced_keys=("sections",)``
        - ``outputs["sections"]`` — list of dicts, each with
          ``content`` (str) and ``source_span`` (``{start, end}``).
        - ``outputs["chunk_type"]`` — always ``"section"``.
        - ``outputs["step_order"]`` — always ``2``.
        """
        validate_step_type(self.step_type)

        raw_text: str = context.get("raw_text", "")

        if not raw_text:
            return StepResult(
                produced_keys=("sections",),
                outputs={
                    "sections": [],
                    "chunk_type": "section",
                    "step_order": 2,
                },
            )

        sections = _segment(raw_text)

        return StepResult(
            produced_keys=("sections",),
            outputs={
                "sections": sections,
                "chunk_type": "section",
                "step_order": 2,
            },
        )


# ---------------------------------------------------------------------------
# Module-level helpers (stateless, testable without instantiating the class)
# ---------------------------------------------------------------------------


def _segment(text: str) -> list[dict[str, Any]]:
    """Split *text* into sections with source-span provenance.

    Strategy cascade:
      1. Structural markers (headings + numbered sections).
      2. Paragraph boundaries (``\\n\\n``).
      3. Single chunk (the whole text).
    """
    sections = _split_by_boundaries(text, _SECTION_BOUNDARY)

    if not sections:
        sections = _split_by_paragraphs(text)

    if not sections:
        sections = [
            {
                "content": text,
                "source_span": {"start": 0, "end": len(text)},
            }
        ]

    return sections


def _split_by_boundaries(
    text: str,
    pattern: re.Pattern[str],
) -> list[dict[str, Any]]:
    """Split *text* at regex boundary positions.

    Boundary matches are positioned at the ``\\n`` that precedes a
    structural marker.  Sections are contiguous: the concatenation of
    all ``content`` fields exactly reproduces *text*.

    Provenance handling (CR #1 on PR #730): the boundary ``\\n`` is the
    paragraph separator, so it belongs to the *previous* section's
    trailing content, NOT to the new heading's leading content.  We
    therefore end each section at ``m.end()`` and start the next section
    at ``m.end()`` (one past the ``\\n``).

    Returns an empty list when *pattern* finds no matches.
    """
    matches = list(pattern.finditer(text))

    if not matches:
        return []

    sections: list[dict[str, Any]] = []

    # Text before the first boundary (includes the boundary \n).
    start, end = 0, matches[0].end()
    sections.append(
        {"content": text[start:end], "source_span": {"start": start, "end": end}}
    )

    # Each boundary-initiated section (starts AFTER the boundary \n).
    for i, m in enumerate(matches):
        start = m.end()
        end = (
            matches[i + 1].end() if i + 1 < len(matches) else len(text)
        )
        sections.append(
            {"content": text[start:end], "source_span": {"start": start, "end": end}}
        )

    return sections


def _split_by_paragraphs(text: str) -> list[dict[str, Any]]:
    """Split *text* at blank-line boundaries (``\\n\\n``).

    Uses ``re.split`` with manual cursor tracking (same approach as
    :func:`nfm_db.services.chunker._segment_paragraphs`) to ensure
    sections are contiguous: the concatenation of all ``content``
    fields exactly reproduces *text*.

    Returns an empty list when no blank lines exist.
    """
    parts = re.split(r"\n\n", text)

    if len(parts) <= 1:
        return []

    sections: list[dict[str, Any]] = []
    cursor = 0

    for i, part in enumerate(parts):
        end = cursor + len(part)
        delimiter = 2 if i < len(parts) - 1 else 0
        end_with_delim = end + delimiter
        content = text[cursor:end_with_delim]
        sections.append(
            {"content": content, "source_span": {"start": cursor, "end": end_with_delim}}
        )
        cursor = end_with_delim

    return sections
