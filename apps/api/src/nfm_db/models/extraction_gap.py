"""ExtractionGap ORM model.

Represents a gap detected during ontology-driven extraction for a specific
entity type and property within an ontology version (NFM-2575-T1).

Each row identifies a missing data point that the extraction pipeline
could not fill.  Gaps are tied to a specific ontology version (which
defines the schema of expected entities/properties) and optionally to
the source chunk that was being processed when the gap was detected.

Status lifecycle: open → filling → filled | wont_fix.

A composite unique constraint on (ontology_version_id, entity_type,
property) prevents duplicate gap records for the same ontology-entity-property
combination.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin

# Allowed gap_status values.
EXTRACTION_GAP_STATUSES: tuple[str, ...] = (
    "open",
    "filling",
    "filled",
    "wont_fix",
)


class ExtractionGap(TimestampMixin, Base):
    """A gap detected during ontology-driven extraction (NFM-2575-T1).

    Identifies a missing data point for a specific (entity_type, property)
    pair within an ontology version.  Optionally linked to the extraction
    chunk that was being processed when the gap was found.
    """

    __tablename__ = "extraction_gaps"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Ontology version reference ---
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("ontology_versions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Ontology version that defines the expected schema.",
    )

    # --- Gap identity ---
    entity_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Entity type, e.g. NuclearMaterial, Isotope.",
    )
    property: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Property name, e.g. density, half_life.",
    )

    # --- Source provenance ---
    source_reference: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Source identifier where the gap was detected.",
    )

    # --- Chunk reference ---
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("extraction_chunks.id", ondelete="SET NULL"),
        nullable=True,
        comment="Extraction chunk being processed when gap was found.",
    )

    # --- Status ---
    gap_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
        comment="open | filling | filled | wont_fix",
    )

    # --- Timestamps ---
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="When the gap was first detected.",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the gap was filled or marked wont_fix.",
    )

    __table_args__ = (
        Index(
            "ix_extraction_gaps_ov_entity_property",
            "ontology_version_id",
            "entity_type",
            "property",
            unique=True,
        ),
        Index(
            "ix_extraction_gaps_chunk_id",
            "chunk_id",
        ),
        Index(
            "ix_extraction_gaps_gap_status",
            "gap_status",
        ),
        Index(
            "ix_extraction_gaps_ontology_version_id",
            "ontology_version_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionGap id={self.id!s} "
            f"entity={self.entity_type!r} prop={self.property!r} "
            f"status={self.gap_status!r}>"
        )
