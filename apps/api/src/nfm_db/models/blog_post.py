"""Blog post metadata model for review workflow."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, CheckConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from nfm_db.models.user import User


class PostStatus(str, enum.Enum):
    """Blog post status for review workflow."""

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class BlogPostMetadata(TimestampMixin, Base):
    """Blog post metadata model for review workflow tracking.
    
    This table stores workflow metadata separately from the markdown content,
    enabling efficient querying while keeping content in files.
    """

    __tablename__ = "blog_posts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'under_review', 'approved', 'published', 'rejected')",
            name="check_post_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PostStatus.DRAFT.value,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def post_status(self) -> PostStatus:
        """Get the post status as an enum."""
        return PostStatus(self.status)

    @post_status.setter
    def post_status(self, value: PostStatus) -> None:
        """Set the post status from an enum."""
        self.status = value.value

    def can_transition_to(self, new_status: PostStatus) -> bool:
        """Check if status transition is valid."""
        valid_transitions = {
            PostStatus.DRAFT: [PostStatus.UNDER_REVIEW],
            PostStatus.UNDER_REVIEW: [PostStatus.APPROVED, PostStatus.REJECTED],
            PostStatus.APPROVED: [PostStatus.PUBLISHED, PostStatus.DRAFT],
            PostStatus.PUBLISHED: [],  # Terminal state
            PostStatus.REJECTED: [PostStatus.DRAFT],
        }
        return new_status in valid_transitions.get(self.post_status, [])

    def __repr__(self) -> str:
        return (
            f"<BlogPostMetadata id={self.id!s} "
            f"slug={self.slug!r} "
            f"status={self.status}>"
        )
