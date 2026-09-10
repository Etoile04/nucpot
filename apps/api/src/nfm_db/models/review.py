"""Review model and status enum for Phase 3 human review system.

State machine: pending → approved | rejected | needs_revision → corrected,
plus NFM-4554 spec §3.4 `skipped` state (临时跳过 / 后续仍可恢复).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ReviewStatus(str, enum.Enum):
    """Review status values for the human review state machine.

    Transitions:
        pending       → approved | rejected | needs_revision | skipped
        needs_revision → corrected | approved | rejected
        needs_revision → skipped (a row under revision can still be deferred)
        skipped       → pending (恢复, spec §3.4)
        skipped       → approved | rejected | needs_revision (terminal or
                        continue revision directly without round-tripping
                        through pending)
        approved | rejected | corrected → pending  (reset)
    """

    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    CORRECTED = "corrected"
    # NFM-4554 spec §3.4 — 五动作 "跳过" wire-up. Distinct status (not a
    # no-op self-loop on pending) so the audit trail, stats aggregation,
    # and row re-discovery by the queue stay correct.
    SKIPPED = "skipped"


# Valid transitions enforced at the API/service layer.
VALID_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING: frozenset({
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.NEEDS_REVISION,
        ReviewStatus.SKIPPED,
    }),
    ReviewStatus.NEEDS_REVISION: frozenset({
        ReviewStatus.CORRECTED,
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.SKIPPED,
    }),
    # NFM-4554 spec §3.4 — 后续仍可恢复. Skipped rows can resume via
    # either an explicit reset to pending OR a direct terminal verdict.
    ReviewStatus.SKIPPED: frozenset({
        ReviewStatus.PENDING,
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.NEEDS_REVISION,
    }),
    # Reset: allow returning from terminal states to pending.
    ReviewStatus.APPROVED: frozenset({ReviewStatus.PENDING}),
    ReviewStatus.REJECTED: frozenset({ReviewStatus.PENDING}),
    ReviewStatus.CORRECTED: frozenset({ReviewStatus.PENDING}),
}


class ReviewMixin:
    """Mixin for reviewable entities."""

    status: Mapped[str] = mapped_column(String, default=ReviewStatus.PENDING)
    reviewer_comment: Mapped[str | None] = mapped_column(String, default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )


class Review(Base):
    """Audit trail table for review actions.

    Records every review decision for compliance and adoption rate tracking.
    """

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    result_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    reviewer_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Who performed the review",
    )
    action: Mapped[str] = mapped_column(
        String(50),
        comment="approved | rejected | needs_revision | corrected",
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
