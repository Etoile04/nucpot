"""Step 5 of the strangler-fig extraction pipeline (NFM-2677 B6).

Final step: assembles a per-section property chunk into a final
chunk.  Sets ``chunk_type='final'`` and stamps an entity-count
summary onto ``metadata`` so downstream consumers can size the
chunk without re-walking the entities list.
"""

from __future__ import annotations

from typing import Any

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep


def _as_span_pair(span: Any) -> tuple[int, int]:
    """Normalize a source_span into a ``(start, end)`` int tuple.

    Tolerates three shapes emitted by upstream pipeline steps:

    * ``tuple[int, int]`` — the canonical NFM-2685 contract. Pass-through.
    * ``list[int]`` — coerced to tuple for hashability.
    * ``dict`` with ``"start"`` and ``"end"`` keys — emitted by the
      PR #730 SectionSegmenter integration. Naive ``tuple(dict)``
      yields the *keys* ``("start", "end")``; we must read by key.

    The returned tuple is suitable for ``ExtractionChunk._source_span``,
    which validates as a 2-tuple of non-negative ints.
    """
    if isinstance(span, tuple):
        return span
    if isinstance(span, list):
        return (span[0], span[1])
    if isinstance(span, dict):
        return (span["start"], span["end"])
    raise TypeError(
        f"_as_span_pair: unsupported source_span shape "
        f"{type(span).__name__!r}; expected tuple, list, or dict"
    )


class ChunkBuilder(ExtractionStep):
    """Step 5: assemble final chunk with entity-count summary."""

    @property
    def step_name(self) -> str:
        return "chunk_builder"

    @property
    def step_order(self) -> int:
        return 4

    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        entities = input_chunk.metadata.get("entities", {})
        summary = {
            "formula_count": len(entities.get("formulas", [])),
            "property_count": len(entities.get("properties", [])),
            "measurement_count": len(entities.get("measurements", [])),
        }
        # Defensive normalization (NFM-2740): the SectionSegmenter
        # integration (PR #730) hands us dict-shaped spans. The
        # ExtractionChunk constructor requires a tuple, so normalize
        # here before delegating to the validator.
        normalized_span = _as_span_pair(input_chunk._source_span)
        return ExtractionChunk(
            content=input_chunk.content,
            chunk_type="final",
            _source_span=normalized_span,
            metadata={**input_chunk.metadata, "summary": summary},
            parent_chunk_id=input_chunk.parent_chunk_id,
        )
