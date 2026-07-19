"""Review model and status enum for Phase 3 human review system.

State machine: pending → approved | rejected | needs_revision → corrected.
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
        pending       → approved | rejected | needs_revision
        needs_revision → corrected
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"
    CORRECTED = "corrected"


# Valid transitions enforced at the API/service layer.
VALID_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING: {
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.NEEDS_REVISION,
    },
    ReviewStatus.NEEDS_REVISION: {
        ReviewStatus.CORRECTED,
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
    },
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
