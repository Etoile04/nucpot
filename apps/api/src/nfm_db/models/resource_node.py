"""Resource node ORM model (NFM-2019).

A *resource node* is a downstream site that synchronises data
with a hub node in the 1+N architecture.  Resource nodes are
owned by a hub and may be of type ``computing``, ``storage`` or
``observatory``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class ResourceNode(TimestampMixin, Base):
    """A resource node — a downstream site in the 1+N data submission architecture."""

    __tablename__ = "resource_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    hub_node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hub_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owning hub node; CASCADE on hub delete.",
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Resource node display name.",
    )
    node_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Type: computing, storage, observatory.",
    )
    api_endpoint: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Base URL of the resource node API.",
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
        comment="ISO timestamp of last heartbeat from the resource node.",
    )
    offline_since: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="ISO timestamp when the node transitioned to offline.",
    )
    sync_watermark: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="ISO timestamp of the last successfully synced batch.",
    )

    def __repr__(self) -> str:
        return (
            f"<ResourceNode id={self.id!s} hub={self.hub_node_id!s} "
            f"name={self.name!r} status={self.status!r}>"
        )


__all__ = ["ResourceNode"]
