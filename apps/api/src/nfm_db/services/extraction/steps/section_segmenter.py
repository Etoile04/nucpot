"""Step 2 of the strangler-fig extraction pipeline (NFM-2677 B3).

Splits a normalized raw-text chunk into discrete sections.  Each
section is its own ``ExtractionChunk`` with ``chunk_type='section'``
and a ``_source_span`` that points back to the original document
bytes.  This step is a 1→N fan-out, exposed via ``execute_many``;
``execute`` returns the first emitted section to satisfy the single-
chunk contract defined by the ABC.
"""

from __future__ import annotations

import re

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep


def _find_boundaries(content: str) -> list[int]:
    """Return absolute offsets in *content* where each new section begins.

    A new section starts at offset 0 and at every position immediately
    after a blank line (``\\n\\n``) that precedes a markdown heading
    (line beginning with ``#``).  Boundaries are returned sorted.
    """
    boundaries: list[int] = [0]
    for match in re.finditer(r"\n\n(?=^#{1,6}\s)", content, re.MULTILINE):
        # New section begins right after the blank line, i.e. at the
        # start of the next line.  match.end() points one past the
        # second ``\n`` — which is the byte offset of the heading ``#``.
        boundaries.append(match.end())
    return sorted(set(boundaries))


class SectionSegmenter(ExtractionStep):
    """Step 2: split normalized raw text into logical sections.

    Boundaries are detected at markdown heading lines (when preceded
    by a blank line) so headings and the paragraphs beneath them are
    kept together.  Section spans are anchored to the parent chunk's
    ``_source_span`` so downstream steps can trace each section back
    to the original document.
    """

    @property
    def step_name(self) -> str:
        return "section_segmenter"

    @property
    def step_order(self) -> int:
        return 1

    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        """Return the first section.  Use ``execute_many`` for fan-out."""
        return self.execute_many(input_chunk)[0]

    def execute_many(
        self,
        input_chunk: ExtractionChunk,
    ) -> list[ExtractionChunk]:
        parent_start, _parent_end = input_chunk._source_span
        content = input_chunk.content
        boundaries = _find_boundaries(content)

        sections: list[ExtractionChunk] = []
        for index, start in enumerate(boundaries):
            end = (
                boundaries[index + 1]
                if index + 1 < len(boundaries)
                else len(content)
            )
            raw_body = content[start:end]
            # Strip trailing blank lines so the span describes only
            # the bytes the section body occupies.
            body = raw_body.rstrip("\n")
            if not body:
                continue
            span = (parent_start + start, parent_start + start + len(body))
            sections.append(
                ExtractionChunk(
                    content=body,
                    chunk_type="section",
                    _source_span=span,
                    metadata={
                        **input_chunk.metadata,
                        "section_index": index,
                        "section_count": len(boundaries),
                    },
                    parent_chunk_id=input_chunk.parent_chunk_id,
                )
            )
        return sections
