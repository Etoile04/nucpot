"""SQLAlchemy ORM base and common mixins."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class TimestampMixin:
    """Provide created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


from nfm_db.models.feedback import Feedback, FeedbackStatus, FeedbackType, Priority  # noqa: E402, F401
from nfm_db.models.ref_gap_fill import (  # noqa: E402, F401
    CacheLevel,
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.user import (  # noqa: E402, F401
    BlogRole,
    Permission,
    User,
)
from nfm_db.models.blog_post import (  # noqa: E402, F401
    BlogPostMetadata,
    PostStatus,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "Feedback",
    "FeedbackType",
    "Priority",
    "FeedbackStatus",
    "RefGapFillStaging",
    "Confidence",
    "StagingStatus",
    "CacheLevel",
    "User",
    "BlogRole",
    "Permission",
    "BlogPostMetadata",
    "PostStatus",
]
