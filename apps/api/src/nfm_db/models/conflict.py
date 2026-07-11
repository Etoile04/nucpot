"""Conflict record ORM model for multi-source fusion (NFM-839 B3.2).

Tracks property value conflicts detected across multiple literature sources
and records resolution decisions for auditability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin


class ConflictStatus(StrEnum):
    """Lifecycle states for a conflict record."""

    PENDING = "pending"
    AUTO_RESOLVED = "auto_resolved"
    MANUALLY_RESOLVED = "manually_resolved"
    ESCALATED = "escalated"
    DISMISSED = "dismissed"


class ResolutionStrategy(StrEnum):
    """Available conflict resolution strategies (ADR-NFM-817-3)."""

    NEWEST = "newest"
    CONFIDENCE = "confidence"
    CONSENSUS = "consensus"
    MANUAL = "manual"


class ConflictRecord(TimestampMixin, Base):
    """A detected conflict between property values from different sources.

    Created by the fusion pipeline when the same material/property pair has
    two or more distinct values reported across different data sources.
    """

    __tablename__ = "conflict_records"
    __table_args__ = (
        Index("idx_conflicts_material", "material_id"),
        Index("idx_conflicts_status", "status"),
        Index("idx_conflicts_material_prop", "material_id", "property_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"),
    )
    property_type: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(50),
        default=ConflictStatus.PENDING,
    )
    resolution_strategy: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    conflicting_values: Mapped[list[dict[str, Any]] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )
    resolved_value: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )
    resolution_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<ConflictRecord id={self.id!s} "
            f"material={self.material_id!s} "
            f"prop={self.property_type!r} "
            f"status={self.status!r}>"
        )
