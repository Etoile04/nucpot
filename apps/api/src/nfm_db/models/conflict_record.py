"""Conflict record ORM model for multi-source fusion.

Phase 2B table: conflict_records.
Stores detected conflicts between property values from different sources,
the applied resolution strategy, and the resolved value.
Per spec §6.1 (conflict_records) and §6.2 (default_conflict_strategy column).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.kg import KGNode
    from nfm_db.models.property import PropertyType
    from nfm_db.models.source import DataSource


class ConflictStrategy(StrEnum):
    """Configurable conflict resolution strategies (ADR-NFM-817-3).

    - newest: Most recent extraction wins
    - confidence: Highest confidence score wins
    - consensus: Statistical aggregation (mean/median with outlier detection)
    - manual: Route to review queue for human decision
    """

    NEWEST = "newest"
    CONFIDENCE = "confidence"
    CONSENSUS = "consensus"
    MANUAL = "manual"


class ConflictStatus(StrEnum):
    """Lifecycle status for a conflict record."""

    PENDING = "pending"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class ConflictRecord(TimestampMixin, Base):
    """A detected conflict between property values from different sources.

    Created when the fusion pipeline detects the same material + property
    from different sources with different values.  Records the resolution
    strategy applied and the resulting value.
    """

    __tablename__ = "conflict_records"
    __table_args__ = (
        Index("ix_conflict_records_material", "material_node_id"),
        Index("ix_conflict_records_property", "property_node_id"),
        Index("ix_conflict_records_status", "status"),
        Index(
            "ix_conflict_records_material_property",
            "material_node_id",
            "property_node_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    material_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="KG node for the material with the conflict",
    )
    property_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="KG node for the property in conflict",
    )
    property_type_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("property_types.id", ondelete="SET NULL"),
        nullable=True,
        comment="PropertyType for strategy lookup",
    )

    # -- conflict data --
    conflicting_values: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="JSON array of {value, source_id, confidence, extracted_at}",
    )
    strategy: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Resolution strategy applied",
    )
    resolved_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="The winning value after resolution",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        comment="pending | resolved | escalated",
    )

    # -- provenance --
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        comment="User who manually resolved (null if auto-resolved)",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolution_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # -- relationships --
    material_node: Mapped["KGNode"] = relationship(
        foreign_keys=[material_node_id],
    )
    property_node: Mapped["KGNode"] = relationship(
        foreign_keys=[property_node_id],
    )
    property_type: Mapped["PropertyType | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<ConflictRecord id={self.id!s} "
            f"material={self.material_node_id!s} "
            f"property={self.property_node_id!s} "
            f"strategy={self.strategy!r} status={self.status!r}>"
        )
