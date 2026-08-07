"""ExtractionChunk ORM model.

Represents a single text chunk produced by the chunking step of an
extraction pipeline run (NFM-2567). Chunks are the atomic units fed
to downstream extraction and mapping steps.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin


class ExtractionChunk(TimestampMixin, Base):
    """A single text chunk from the chunking step (NFM-2567).

    Each chunk carries the extracted text, its source provenance,
    optional source-span offsets, and an estimated token count used
    for downstream batching.
    """

    __tablename__ = "extraction_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Parent job ---
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        comment="Parent extraction job that produced this chunk.",
    )

    # --- Source provenance ---
    source_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Source identifier (e.g. page number, section heading).",
    )

    # --- Content ---
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The chunk text produced by the chunker.",
    )

    # --- Source offsets ---
    source_span: Mapped[dict | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment='Source file offsets as {"start": int, "end": int}.',
    )

    # --- Ordering ---
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Sequential index of this chunk within the job.",
    )

    # --- Token estimation ---
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Estimated token count for downstream batching.",
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionChunk id={self.id!s} job_id={self.job_id!s} "
            f"index={self.chunk_index!r}>"
        )
