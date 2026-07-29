"""Upload session ORM model (NFM-2019).

A upload session coordinates a chunked file submission from a
resource node.  It tracks progress, exposes a resume token, and
records the final SHA-256 once the upload completes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class UploadSession(TimestampMixin, Base):
    """A chunked file upload session owned by a single resource node."""

    __tablename__ = "upload_sessions"

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
        comment="Resource node performing the upload; CASCADE on delete.",
    )
    file_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Original file name as supplied by the resource node.",
    )
    total_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Total file size in bytes.",
    )
    chunk_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Size of each chunk in bytes.",
    )
    total_chunks: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Number of chunks the file was split into.",
    )
    uploaded_chunks: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="0",
        comment="Number of chunks successfully uploaded so far.",
    )
    resume_token: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,
        comment="Opaque token used to resume a paused upload.",
    )
    sha256_full: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 of the fully reassembled file (set on completion).",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="Lifecycle status: pending, in_progress, completed, failed.",
    )

    def __repr__(self) -> str:
        return (
            f"<UploadSession id={self.id!s} file_name={self.file_name!r} "
            f"status={self.status!r}>"
        )


__all__ = ["UploadSession"]
