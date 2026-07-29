"""Data DNA ORM model (NFM-2019).

The data DNA record captures the cryptographic identity of a
contributed record (UUIDv4, SHA-256, optional SM3).  It is the
authoritative fingerprint used to deduplicate submissions in the
1+N architecture.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class DataDna(TimestampMixin, Base):
    """A data DNA record — fingerprints a single record by UUID + hash."""

    __tablename__ = "data_dna"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    record_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Type of the record being fingerprinted (e.g. material, property).",
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
        comment="UUID of the source record being fingerprinted.",
    )
    dna_uuid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        unique=True,
        comment="UUIDv4 content fingerprint.",
    )
    sha256_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="SHA-256 hex digest of the record content.",
    )
    sm3_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Optional SM3 hex digest (GB/T 32905).",
    )

    def __repr__(self) -> str:
        return (
            f"<DataDna id={self.id!s} record_type={self.record_type!r} "
            f"dna_uuid={self.dna_uuid!s}>"
        )


__all__ = ["DataDna"]
