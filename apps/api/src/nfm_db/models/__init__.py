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


from nfm_db.models.blog_post import (  # noqa: E402
    BlogPostMetadata,
    PostStatus,
)
from nfm_db.models.feedback import (  # noqa: E402
    Feedback,
    FeedbackStatus,
    FeedbackType,
    Priority,
)
from nfm_db.models.potential import Potential  # noqa: E402
from nfm_db.models.ref_gap_fill import (  # noqa: E402
    CacheLevel,
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.kg_node import (  # noqa: E402
    KGNode,
    KGProvenance,
    KGReviewQueue,
)
from nfm_db.models.user import (  # noqa: E402
    BlogRole,
    Permission,
    User,
)

__all__ = [
    "Base",
    "BlogPostMetadata",
    "BlogRole",
    "CacheLevel",
    "Confidence",
    "Feedback",
    "FeedbackStatus",
    "FeedbackType",
    "KGNode",
    "KGProvenance",
    "KGReviewQueue",
    "Permission",
    "PostStatus",
    "Potential",
    "Priority",
    "RefGapFillStaging",
    "StagingStatus",
    "TimestampMixin",
    "User",
]
