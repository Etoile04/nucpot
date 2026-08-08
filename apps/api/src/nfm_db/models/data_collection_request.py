"""DataCollectionRequest ORM model (NFM-2619).

Represents a request for manual or automated data collection to fill
a coverage gap in the NFMD knowledge base.  Each row targets a specific
(entity_type, property, material_system) triple within an ontology version.

Status lifecycle: open → in_progress → completed | declined.

A composite unique constraint on (ontology_version_id, entity_type, property,
material_system) prevents duplicate collection requests for the same triple.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin

# Allowed status values.
DATA_COLLECTION_REQUEST_STATUSES: tuple[str, ...] = (
    "open",
    "in_progress",
    "completed",
    "declined",
)

# Allowed source_preference values.
SOURCE_PREFERENCES: tuple[str, ...] = (
    "literature",
    "dft",
    "external_db",
    "any",
)

# Allowed dispatch_status values (added via migration 050).
DISPATCH_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "success",
    "failed",
)


class DataCollectionRequest(TimestampMixin, Base):
    """A data-collection request tied to an ontology coverage gap (NFM-2619).

    Targets a specific (entity_type, property, material_system) triple
    within an ontology version.  Tracks urgency, source preference, and
    lifecycle status.
    """

    __tablename__ = "data_collection_requests"

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

    # --- Request identity ---
    entity_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Entity type, e.g. NuclearMaterial, Isotope.",
    )
    property: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Property name, e.g. thermal_conductivity, density.",
    )
    material_system: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Material system, e.g. UO2, Zr, U.",
    )

    # --- Priority / preference ---
    urgency: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Higher = more urgently needed.",
    )
    source_preference: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="any",
        comment="literature | dft | external_db | any",
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="open",
        comment="open | in_progress | completed | declined",
    )

    # --- Timestamps ---
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="When the request was created.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the request reached a terminal status.",
    )

    # --- Flexible metadata ---
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
        comment="Flexible metadata bag.",
    )

    # --- Dispatch tracking (added via migration 050) ---
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the DCR was dispatched to a fill path.",
    )
    dispatched_path: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Resolved fill path: literature | dft | external_db | cascade.",
    )
    dispatch_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="pending | running | success | failed.",
    )
    result_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="External job/task reference (celery ID, etc.).",
    )

    __table_args__ = (
        Index(
            "ix_dcr_ov_entity_prop_material",
            "ontology_version_id",
            "entity_type",
            "property",
            "material_system",
            unique=True,
        ),
        Index(
            "ix_dcr_status",
            "status",
        ),
        Index(
            "ix_dcr_urgency_desc",
            "urgency",
        ),
        Index(
            "ix_dcr_material_system",
            "material_system",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<DataCollectionRequest id={self.id!s} "
            f"entity={self.entity_type!r} prop={self.property!r} "
            f"material={self.material_system!r} status={self.status!r}>"
        )
