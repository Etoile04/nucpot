"""V2 extraction orchestrator (NFM-2677 B7).

Composes the five strangler-fig steps into a single pipeline and
persists every emitted chunk to the ORM ``extraction_chunks``
table.  This is the entry point the V2 dispatch wrapper routes to
when ``EXTRACTION_PIPELINE_V2`` is enabled.

Pipeline shape::

    RawTextLoader  (fan-in: 1 chunk)
          │
          ▼
    SectionSegmenter  (fan-out: N section chunks)
          │
          ▼ (per section)
    EntityExtractor → PropertyNormalizer → ChunkBuilder
          │
          ▼
    list[ExtractionChunk]   (chunk_type="final")

Each emitted chunk is wrapped in a small ``_PersistTarget`` that
carries both the in-memory chunk_type and the ORM row fields, then
added to the session.  The orchestrator does not commit — it leaves
that to the caller's transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_chunk import ExtractionChunk as ORMChunk
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.chunk_builder import ChunkBuilder
from nfm_db.services.extraction.steps.entity_extractor import EntityExtractor
from nfm_db.services.extraction.steps.property_normalizer import (
    PropertyNormalizer,
)
from nfm_db.services.extraction.steps.raw_text_loader import RawTextLoader
from nfm_db.services.extraction.steps.section_segmenter import (
    SectionSegmenter,
)


@dataclass
class _PersistTarget:
    """Wraps an ORM row with the in-memory chunk_type for inspection.

    The orchestrator uses ``session.add(target)`` so the persistence
    layer can flush the row in the caller's transaction.  The
    ``chunk_type`` attribute is in-memory only — the ORM row already
    carries the same content via the ``content`` column, but the
    in-memory type drives routing decisions in the orchestrator.
    """

    row: ORMChunk
    chunk_type: str


class ExtractionOrchestratorV2:
    """Composes the 5 strangler-fig steps with ORM persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Materialize the step instances once.
        self._loader = RawTextLoader()
        self._segmenter = SectionSegmenter()
        self._extractor = EntityExtractor()
        self._normalizer = PropertyNormalizer()
        self._builder = ChunkBuilder()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self, initial_chunk: ExtractionChunk,
    ) -> list[ExtractionChunk]:
        """Execute the pipeline, persist every emitted chunk, return
        the list of final chunks (one per section)."""
        # Step 0: normalize raw text.
        normalized = self._loader.execute(initial_chunk)
        self._persist(normalized, index=0)

        # Step 1: split into sections (fan-out).
        sections = self._segmenter.execute_many(normalized)
        for section_idx, section in enumerate(sections):
            self._persist(section, index=section_idx + 1)

        # Steps 2-4: per-section chain.
        finals: list[ExtractionChunk] = []
        for section_idx, section in enumerate(sections):
            entity = self._extractor.execute(section)
            self._persist(
                entity, index=section_idx, step_name=self._extractor.step_name,
            )

            property_chunk = self._normalizer.execute(entity)
            self._persist(
                property_chunk,
                index=section_idx,
                step_name=self._normalizer.step_name,
            )

            final = self._builder.execute(property_chunk)
            self._persist(
                final, index=section_idx, step_name=self._builder.step_name,
            )
            finals.append(final)

        await self._session.flush()
        return finals

    # ------------------------------------------------------------------
    # Persistence helper
    # ------------------------------------------------------------------

    def _persist(
        self,
        chunk: ExtractionChunk,
        index: int,
        step_name: str | None = None,
    ) -> None:
        """Convert an in-memory ExtractionChunk to an ORM row, wrap
        it for inspection, and add it to the session."""
        row = ORMChunk(
            content=chunk.content,
            source_span={
                "start": chunk._source_span[0],
                "end": chunk._source_span[1],
            },
            chunk_index=index,
            token_count=None,
        )
        target = _PersistTarget(row=ORMChunk(
            content=chunk.content,
            source_span={"start": chunk._source_span[0], "end": chunk._source_span[1]},
            chunk_index=index,
            token_count=None,
        ), chunk_type=chunk.chunk_type)
        self._session.add(target)
