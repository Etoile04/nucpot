"""Classification level ORM model and enum (NFM-2019, NFM-2026).

NFM-2026 adds:
  - ClassificationLevelEnum with Chinese labels
  - classification_check_constraint() for DB post-write safety

The ClassificationLevel table already exists from migration 032.
This module adds the enum and CHECK constraint for enforcement.
"""
from __future__ import annotations

import enum
import uuid
from typing import ClassVar

from sqlalchemy import CheckConstraint, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class ClassificationLevelEnum(str, enum.Enum):
    """Contract security labels (§3.1.2, §5.7)."""

    UNCLASSIFIED: ClassVar[str] = "非密"
    INTERNAL: ClassVar[str] = "内部"
    SECRET: ClassVar[str] = "秘密"

    @classmethod
    def labels(cls) -> set[str]:
        """Return the set of valid label strings."""
        return {m.value for m in cls}


def classification_check_constraint(column_name: str = "classification_level") -> CheckConstraint:
    """Return a reusable CHECK constraint for classification_level columns.

    Post-write safety net: even if Pydantic validation is bypassed,
    the database rejects invalid values.
    """
    valid = ClassificationLevelEnum.labels()
    values_sql = ", ".join(repr(v) for v in sorted(valid))
    return CheckConstraint(
        f"{column_name} IN ({values_sql})",
        name=f"ck_{column_name}_valid_label",
    )


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


__all__ = ["ClassificationLevel", "ClassificationLevelEnum", "classification_check_constraint"]
