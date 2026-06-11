"""Staging model for reference gap-fill ingestion pipeline.

Per NFM-54 design Section 1.2: every incoming reference_value lands
in this staging table first, passes quality gates, then gets promoted
to the normalized NFMD property_measurements table.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    DoublePrecision,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class Confidence(str, enum.Enum):
    """Confidence level for a reference value."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class StagingStatus(str, enum.Enum):
    """Review workflow status for a staging record."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED = "promoted"


class CacheLevel(str, enum.Enum):
    """NFM reference cache level the data originated from."""

    L1 = "L1"
    L2 = "L2"
    L3A = "L3A"
    L3B = "L3B"


class RefGapFillStaging(TimestampMixin, Base):
    """Staging table for reference gap-fill ingestion.

    Decouples gap-fill ingestion from the normalized NFMD schema.
    Incoming reference values land here first, pass quality gates,
    then get promoted to property_measurements.
    """

    __tablename__ = "_ref_gap_fill_staging"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Source fields (verbatim from nfm-ref-gapfill) ---

    element_system: Mapped[str] = mapped_column(String(50), nullable=False)
    phase: Mapped[str | None] = mapped_column(String(50), nullable=True)
    property_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(DoublePrecision, nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    source_doi: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uncertainty: Mapped[float | None] = mapped_column(
        DoublePrecision, nullable=True,
    )
    temperature: Mapped[float | None] = mapped_column(
        DoublePrecision, nullable=True,
    )

    # --- Quality gate columns ---

    confidence: Mapped[Confidence] = mapped_column(
        Enum(Confidence, name="confidence_enum"),
        nullable=False,
        default=Confidence.MEDIUM,
    )
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    range_validated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    # --- Review workflow ---

    status: Mapped[StagingStatus] = mapped_column(
        Enum(StagingStatus, name="staging_status_enum"),
        nullable=False,
        default=StagingStatus.PENDING,
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # --- Promotion tracking ---

    promoted_to_pm_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # --- Metadata ---

    cache_level: Mapped[CacheLevel | None] = mapped_column(
        Enum(CacheLevel, name="cache_level_enum"),
        nullable=True,
    )
    fill_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )

    # --- Indexes (defined in Alembic migration) ---

    __table_args__ = (
        Index("idx_staging_status", "status"),
        Index(
            "idx_staging_element_phase_prop",
            "element_system",
            "phase",
            "property_name",
        ),
        Index("idx_staging_dedup", "dedup_hash"),
        Index(
            "idx_staging_needs_review",
            "status",
            postgresql_where="status = 'pending'",
        ),
        Index("idx_staging_fill_batch", "fill_batch_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<RefGapFillStaging id={self.id!s} "
            f"element={self.element_system!r} "
            f"prop={self.property_name!r} "
            f"status={self.status.value}>"
        )
