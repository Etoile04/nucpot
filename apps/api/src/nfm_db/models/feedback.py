"""Feedback model and enumerations."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class FeedbackType(str, enum.Enum):
    """Type of user feedback."""

    BUG_REPORT = "bug_report"
    FEATURE_REQUEST = "feature_request"
    DATA_CORRECTION = "data_correction"
    USAGE_INQUIRY = "usage_inquiry"


class Priority(str, enum.Enum):
    """Feedback priority level."""

    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FeedbackStatus(str, enum.Enum):
    """Feedback processing status."""

    OPEN = "open"
    CLASSIFIED = "classified"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Feedback(TimestampMixin, Base):
    """User feedback submitted through the website."""

    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    feedback_type: Mapped[FeedbackType] = mapped_column(
        Enum(FeedbackType, name="feedback_type_enum"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="priority_enum"),
        nullable=False,
        default=Priority.MEDIUM,
    )
    status: Mapped[FeedbackStatus] = mapped_column(
        Enum(FeedbackStatus, name="feedback_status_enum"),
        nullable=False,
        default=FeedbackStatus.OPEN,
    )
    assignee: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Feedback id={self.id!s} "
            f"type={self.feedback_type.value} "
            f"priority={self.priority.value} "
            f"status={self.status.value}>"
        )
