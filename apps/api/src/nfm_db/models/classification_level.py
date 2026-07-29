"""Classification level ORM model (NFM-2019).

A classification level represents one of the contract's security
labels: 非密 (unclassified), 内部 (internal), or 秘密 (secret).
Rows are referenced by upload sessions and data DNA records so the
system can enforce the contract's classification rules.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class ClassificationLevel(TimestampMixin, Base):
    """A classification level (非密/内部/秘密) usable by submissions."""

    __tablename__ = "classification_levels"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    label: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        comment="Contract label (非密, 内部, 秘密).",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-form description of the level.",
    )

    def __repr__(self) -> str:
        return f"<ClassificationLevel id={self.id!s} label={self.label!r}>"


__all__ = ["ClassificationLevel"]
