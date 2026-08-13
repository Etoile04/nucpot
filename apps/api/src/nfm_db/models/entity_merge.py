"""Entity merge log ORM model (NFM-1391, B3.1.1).

Records the deduplication decisions made by the entity dedup engine
when two material records are judged to refer to the same underlying
entity and the duplicate is merged into a canonical material.

The log preserves an audit trail so reviewers can:
- inspect what the engine decided,
- replay or reverse merges,
- and attribute each merge to its match method (exact, fuzzy, semantic).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB


class MatchMethod(StrEnum):
    """How the dedup engine decided two materials were the same entity."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"


class EntityMergeLog(Base):
    """Audit log entry for a single dedup merge decision.

    The row captures the canonical material (the survivor), the merged
    material (the duplicate that was folded in), the match score and
    method that produced the decision, and an optional JSONB payload
    with rule-specific metadata (matched aliases, edit distance, etc.).
    """

    __tablename__ = "entity_merge_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    canonical_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    merged_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[MatchMethod] = mapped_column(
        Enum(
            MatchMethod,
            name="match_method_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    details: Mapped[dict[str, object] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<EntityMergeLog id={self.id!s} "
            f"canonical={self.canonical_id!s} "
            f"merged={self.merged_id!s} "
            f"method={self.match_method.value} "
            f"score={self.match_score}>"
        )
