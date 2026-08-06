"""Durable Hub-side operation log for resource-node synchronization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class SyncOperation(Base):
    """An idempotently applied operation received from a resource node."""

    __tablename__ = "sync_operations"
    __table_args__ = (
        UniqueConstraint("resource_node_id", "operation_id", name="uq_sync_operations_node_operation"),
    )

    sequence_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    resource_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resource_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    op_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    vector_clock: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_record(self) -> dict[str, Any]:
        """Return the wire representation consumed by resource nodes."""
        return {
            "sync_id": self.sequence_no,
            "operation_id": str(self.operation_id),
            "resource_node_id": str(self.resource_node_id),
            "op_type": self.op_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": self.payload,
            "vector_clock": self.vector_clock or {},
            "updated_at": self.created_at.timestamp() if self.created_at else 0.0,
        }


__all__ = ["SyncOperation"]
