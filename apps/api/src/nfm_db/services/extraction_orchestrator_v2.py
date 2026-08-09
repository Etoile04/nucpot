"""V2 extraction orchestrator (NFM-2677 B7, NFM-2705 follow-up).

Composes the five strangler-fig steps into a single pipeline and
persists every emitted chunk to the ORM ``extraction_chunks``
table.  This is the entry point the V2 dispatch wrapper routes to
when ``EXTRACTION_PIPELINE_V2`` is enabled.

Pipeline shape::

    RawTextLoader  (fan-in: 1 chunk)
          |
          v
    SectionSegmenter  (fan-out: N section chunks)
          |
          v (per section)
    EntityExtractor -> PropertyNormalizer -> ChunkBuilder
          |
          v
    list[ExtractionChunk]   (chunk_type="final")

Each emitted chunk is converted to an :class:`ExtractionChunk` ORM row
and added to the session directly (no wrapper dataclass -- NFM-2705
defect 1).  Every row carries the parent ``job_id`` so the NOT NULL FK
to ``extraction_jobs`` is satisfied at flush (NFM-2705 defect 2).
The orchestrator does not commit -- it leaves that to the caller's
transaction boundary.
"""

from __future__ import annotations

import uuid

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


class ExtractionOrchestratorV2:
    """Composes the 5 strangler-fig steps with ORM persistence.

    The orchestrator must be constructed with the UUID of a *persisted*
    parent ``ExtractionJob`` -- every chunk row carries ``job_id`` so
    the ``extraction_chunks.job_id`` NOT NULL FK is satisfied at the
    first ``session.flush()``.
    """

    def __init__(
        self, session: AsyncSession, *, job_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._job_id = job_id
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
        """Convert an in-memory ``ExtractionChunk`` to an ORM row and
        add it to the session.

        The row carries ``job_id=self._job_id`` so the NOT NULL FK
        to ``extraction_jobs`` is satisfied at flush (NFM-2705 defect 2).
        ``source_span`` JSONB shape mirrors the ORM column comment:
        ``{"start": int, "end": int}``.
        """
        row = ORMChunk(
            content=chunk.content,
            source_span={
                "start": chunk._source_span[0],
                "end": chunk._source_span[1],
            },
            chunk_index=index,
            token_count=None,
            job_id=self._job_id,
        )
        self._session.add(row)
