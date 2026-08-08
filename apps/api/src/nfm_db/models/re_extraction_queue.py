"""ReExtractionQueue ORM model (NFM-2581 / NFM-2573-T4).

Queue entries that track re-extraction jobs triggered when an ontology
version upgrades.  Domain experts select corpora to re-extract against a
newer ontology version; each selection produces a row in this table.

Actual extraction pipeline integration is out of scope — this model and
its API surface only manage the queue lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin

# Allowed statuses for a re-extraction queue entry.
RE_EXTRACTION_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
)


class ReExtractionQueue(TimestampMixin, Base):
    """A queued re-extraction job (NFM-2581).

    Each row represents a request to re-extract a single corpus against a
    specific ontology version.  The lifecycle is:

        pending → running → completed | failed
        pending → cancelled

    Status transitions are enforced at the API layer, not via DB constraints.
    """

    __tablename__ = "re_extraction_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Foreign keys ---
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ontology_versions.id", ondelete="CASCADE"),
        nullable=False,
        comment="The target ontology version to re-extract against.",
    )

    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpus.id", ondelete="CASCADE"),
        nullable=False,
        comment="The corpus to re-extract.",
    )

    triggered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        comment="User who triggered this re-extraction job.",
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        comment=(
            "pending | running | completed | failed | cancelled — see "
            "RE_EXTRACTION_STATUSES."
        ),
    )

    # --- Timing ---
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the extraction worker started processing this entry.",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the entry reached a terminal state.",
    )

    # --- Error ---
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last failure reason when status='failed'.",
    )

    def __repr__(self) -> str:
        return (
            f"<ReExtractionQueue id={self.id!s} "
            f"ontology_version_id={self.ontology_version_id!s} "
            f"corpus_id={self.corpus_id!s} status={self.status!r}>"
        )


__all__ = ["RE_EXTRACTION_STATUSES", "ReExtractionQueue"]
