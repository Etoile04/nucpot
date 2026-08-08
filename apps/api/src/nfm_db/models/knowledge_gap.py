"""KnowledgeGap ORM model.

Represents a gap in extracted knowledge for a given ontology version.
When a new ontology version re-requests extraction, gaps that were previously
marked ``wont_fix`` can be automatically reopened if the new extraction
finds data (NFM-2582).

Status lifecycle: open → in_progress → resolved | wont_fix.
Auto-reopen: wont_fix → open (triggered by new extraction).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin


class GapStatus(str, enum.Enum):
    """Lifecycle status for a knowledge gap."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class GapType(str, enum.Enum):
    """Type of knowledge gap — mirrors extraction result item_type."""

    PROPERTY = "property"
    ENTITY = "entity"
    RELATION = "relation"


# Allowed status values (plain strings for DB column, enum for Python logic).
KNOWLEDGE_GAP_STATUSES: tuple[str, ...] = tuple(s.value for s in GapStatus)
KNOWLEDGE_GAP_TYPES: tuple[str, ...] = tuple(s.value for s in GapType)


class KnowledgeGap(TimestampMixin, Base):
    """A gap in extracted knowledge for a given ontology version (NFM-2582).

    Each row represents a missing data point that was identified during a
    gap scan but could not be filled.  The ``target_key`` field identifies
    *what* is missing (e.g. ``"UO2/FCC/thermal_conductivity"`` for a property
    gap or a relation key like ``"UO2:HAS_PROPERTY:thermal_conductivity"``).

    When a new ontology version triggers re-extraction and the extraction
    finds data for a ``wont_fix`` gap, the service in
    ``nfm_db.services.gap_reopen_service`` sets the status back to ``open``
    with an audit note.
    """

    __tablename__ = "knowledge_gaps"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Gap identity ---
    gap_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment=(
            "property | entity | relation — see KNOWLEDGE_GAP_TYPES."
        ),
    )
    target_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment=(
            "Canonical key identifying the missing data point "
            "(e.g. element_system/phase/property_name)."
        ),
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GapStatus.OPEN.value,
        comment=(
            "open | in_progress | resolved | wont_fix — see "
            "KNOWLEDGE_GAP_STATUSES."
        ),
    )

    # --- Ontology version ---
    ontology_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("ontology_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Ontology version in which this gap was identified.",
    )

    # --- Audit trail ---
    audit_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last audit note (e.g. auto-reopen reason, manual close reason).",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when status was last set to resolved or wont_fix.",
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        comment="User who resolved / marked wont_fix.",
    )

    # --- Flexible metadata ---
    metadata_: Mapped[dict | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment=(
            "Arbitrary gap metadata (e.g. extraction job id, "
            "matching criteria). Trailing underscore avoids "
            "SQLAlchemy MetaData name collision."
        ),
    )

    __table_args__ = (
        Index(
            "idx_kg_status",
            "status",
        ),
        Index(
            "idx_kg_type_target",
            "gap_type",
            "target_key",
            unique=True,
        ),
        Index(
            "idx_kg_ontology_version",
            "ontology_version_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeGap id={self.id!s} "
            f"type={self.gap_type!r} key={self.target_key!r} "
            f"status={self.status!r}>"
        )
