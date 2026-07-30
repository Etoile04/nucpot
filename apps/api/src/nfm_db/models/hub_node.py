"""Hub node ORM model (NFM-2019).

A *hub node* represents the central national hub in the 1+N
architecture.  Resource nodes register under a hub and
synchronise data via upload/download sessions.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class HubNode(TimestampMixin, Base):
    """A hub node — the central authority in the 1+N data submission architecture."""

    __tablename__ = "hub_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Hub node display name.",
    )
    api_endpoint: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Base URL of the hub node API.",
    )
    public_key: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
        comment="Public key for cryptographic verification.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="active",
        comment="Operational status: active, inactive, suspended.",
    )
    last_heartbeat: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="ISO timestamp of last heartbeat from the hub.",
    )

    def __repr__(self) -> str:
        return f"<HubNode id={self.id!s} name={self.name!r} status={self.status!r}>"


__all__ = ["HubNode"]
