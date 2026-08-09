"""Step 5 of the strangler-fig extraction pipeline (NFM-2677 B6).

Final step: assembles a per-section property chunk into a final
chunk.  Sets ``chunk_type='final'`` and stamps an entity-count
summary onto ``metadata`` so downstream consumers can size the
chunk without re-walking the entities list.
"""

from __future__ import annotations

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep


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
        return ExtractionChunk(
            content=input_chunk.content,
            chunk_type="final",
            _source_span=input_chunk._source_span,
            metadata={**input_chunk.metadata, "summary": summary},
            parent_chunk_id=input_chunk.parent_chunk_id,
        )
