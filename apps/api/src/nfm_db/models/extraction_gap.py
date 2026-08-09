"""ExtractionGap ORM model (NFM-2697).

Represents a gap detected during ontology-driven extraction for a specific
entity type and property within an ontology version.

Each row identifies a missing data point that the extraction pipeline
could not fill.  Gaps are tied to:

- ``ontology_version`` (TEXT, e.g. ``"v2.1.0"``) — the semver string of the
  ontology schema that defines the expected entities/properties;
- ``literature_id`` (FK to ``literature.id``) — the source literature the
  gap was detected against (nullable during the NFM-2697-T1 backfill,
  will become NOT NULL once backfill completes);
- optionally ``chunk_id`` (FK to ``extraction_chunks.id``) — the source
  chunk being processed when the gap was detected.

Status lifecycle: open -> filling -> filled | wont_fix.

A composite unique constraint on
``(ontology_version, entity_type, property, literature_id, chunk_id)``
prevents duplicate gap records across the 5-tuple.  Application-level
deduplication is also enforced in ``GapScanService.scan_for_gaps`` because
SQLite's index handling does not always enforce this composite at the DB
layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.extraction_chunk import ExtractionChunk

# Allowed gap_status values.
EXTRACTION_GAP_STATUSES: tuple[str, ...] = (
    "open",
    "filling",
    "filled",
    "wont_fix",
)


class ExtractionGap(TimestampMixin, Base):
    """A gap detected during ontology-driven extraction (NFM-2697).

    Identifies a missing data point for a specific ``(entity_type,
    property)`` pair within an ontology version, scoped to a literature
    source and optionally linked to the extraction chunk that was being
    processed when the gap was found.
    """

    __tablename__ = "extraction_gaps"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Ontology version reference ---
    # TEXT semver string (e.g. "v2.1.0") rather than a UUID FK.  Gaps are
    # identified by which ontology schema defined the expected shape, not
    # by which concrete OntologyVersion row produced the schema.  This
    # also keeps dumps/imports deterministic across ontology re-rolls.
    ontology_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Ontology version that defines the expected schema, e.g. 'v2.1.0'.",
    )

    # --- Literature reference ---
    # TODO(NFM-2697-T1): flip to ``nullable=False`` once the T1 backfill
    # populates ``literature_id`` for every pre-existing
    # ``extraction_gaps`` row.  Until then, historical gaps created
    # before literature tracking existed carry NULL here.
    #
    # The column is intentionally declared without an inline FK so that
    # the test bootstrap (which strips dangling FKs against an
    # in-memory SQLite) does not need the not-yet-existing
    # ``literature`` table to be registered.  NFM-2697-T1 ships the
    # actual FK constraint via alembic and adds the literature ORM
    # mapping at the same time.
    literature_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        comment="Source literature the gap was detected against.",
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

    # --- Relationships ---
    # ``chunk`` resolves via FK to the existing ``extraction_chunks``
    # table; declared ``viewonly=True`` because the inverse
    # ``ExtractionChunk.gaps`` back-populates is intentionally not
    # introduced here (chunks are created by the chunk-builder step,
    # not by gap insertion).
    #
    # The NFM-2697 spec also asks for a ``literature`` relationship,
    # but the ``Literature`` ORM class is a deliverable of
    # NFM-2697-T1 (not yet present in the codebase).  When T1 lands,
    # add::
    #
    #     literature: Mapped["Literature | None"] = relationship(
    #         "Literature",
    #         foreign_keys=[literature_id],
    #         viewonly=True,
    #     )
    chunk: Mapped["ExtractionChunk | None"] = relationship(  # noqa: UP037
        "ExtractionChunk",
        foreign_keys=[chunk_id],
        viewonly=True,
        doc="Optional link to the extraction chunk being processed.",
    )

    __table_args__ = (
        # 5-tuple uniqueness — one gap per
        # (ontology_version, entity_type, property, literature_id,
        # chunk_id).  Application-level dedup in GapScanService covers
        # SQLite test paths that don't always enforce this composite at
        # the DB layer.
        Index(
            "ix_extraction_gaps_5tuple",
            "ontology_version",
            "entity_type",
            "property",
            "literature_id",
            "chunk_id",
            unique=True,
        ),
        # Status filtering.
        Index(
            "ix_extraction_gaps_gap_status",
            "gap_status",
        ),
        # Literature-based recall queries.
        Index(
            "ix_extraction_gaps_literature_id_ontology_version",
            "literature_id",
            "ontology_version",
        ),
        # Partial index on chunk_id so the planner can do a cheap seek
        # only for non-null chunk lookups; null rows are dominated by
        # legacy backfill entries without a chunk anchor.
        Index(
            "ix_extraction_gaps_chunk_id",
            "chunk_id",
            postgresql_where="chunk_id IS NOT NULL",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionGap id={self.id!s} "
            f"ov={self.ontology_version!r} "
            f"entity={self.entity_type!r} prop={self.property!r} "
            f"status={self.gap_status!r}>"
        )
