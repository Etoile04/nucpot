"""Conflict record model — full implementation matching NFM-861 tests.

Stores conflicting property values from multiple sources with resolution metadata.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base


class ConflictRecord(Base):
    """A record of conflicting values for a property from multiple sources."""

    __tablename__ = "conflict_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("kg_nodes.id"))
    property_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("kg_nodes.id"))
    conflicting_values: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    strategy: Mapped[str] = mapped_column(String, default="manual")
    status: Mapped[str] = mapped_column(String, default="pending")
    resolution: Mapped[str | None] = mapped_column(String, default=None)
    resolved_value: Mapped[Any | None] = mapped_column(JSON, nullable=True, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    resolved_by: Mapped[str | None] = mapped_column(String, default=None)
    resolution_notes: Mapped[str | None] = mapped_column(String, default=None)
    source_values: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    property_type_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("property_types.id"), nullable=True
    )
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("materials.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    material_node = relationship("KGNode", foreign_keys=[material_node_id])
    property_node = relationship("KGNode", foreign_keys=[property_node_id])

    def __repr__(self) -> str:
        return f"ConflictRecord(strategy={self.strategy!r}, status={self.status!r})"
