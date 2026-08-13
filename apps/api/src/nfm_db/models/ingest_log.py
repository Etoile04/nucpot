"""Ingest log ORM model (NFM-2019).

An ingest log entry records a single data flow (upload or
download) between a resource node and a hub.  It is the
audit-trail backbone of the 1+N architecture.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class IngestLog(Base):
    """A single data-flow event between a resource node and a hub."""

    __tablename__ = "ingest_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    resource_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resource_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Originating resource node.",
    )
    hub_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hub_nodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Counter-party hub node; NULL when only the resource is known.",
    )
    direction: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Flow direction: upload or download.",
    )
    record_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="0",
        comment="Number of records transferred.",
    )
    data_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="0",
        comment="Aggregate data size in bytes.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="Lifecycle status: pending, in_progress, completed, failed.",
    )
    error_detail: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-form error payload when status=failed.",
    )
    started_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Wall-clock time the operation started.",
    )
    completed_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Wall-clock time the operation completed (NULL while in flight).",
    )

    def __repr__(self) -> str:
        return (
            f"<IngestLog id={self.id!s} direction={self.direction!r} "
            f"status={self.status!r}>"
        )


__all__ = ["IngestLog"]
