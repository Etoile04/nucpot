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
from nfm_db.models.hpc_failover_event import (  # noqa: E402
    HPCFailoverEvent,
)
from nfm_db.models.md_verification import (  # noqa: E402
    DefectAnalysisResult,
    DefectType,
    ExecutionStatus,
    FittingMethod,
    HpcJob,
    HpcJobStatus,
    JobStatus,
    JobType,
    MDSimulationResult,
    MDVerificationJob,
    PotentialFittingResult,
    VerificationResultMD,
)
from nfm_db.models.potential import Potential  # noqa: E402
from nfm_db.models.ref_gap_fill import (  # noqa: E402
    CacheLevel,
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.user import (  # noqa: E402
    BlogRole,
    Permission,
    User,
)
from nfm_db.models.unit import (  # noqa: E402
    Unit,
    UnitConversion,
)
from nfm_db.models.source import (  # noqa: E402
    DataSource,
    Author,
    DataSourceAuthor,
)
from nfm_db.models.material import (  # noqa: E402
    MaterialCategory,
    Material,
    MaterialAlias,
    MaterialComposition,
)
from nfm_db.models.property import (  # noqa: E402
    PropertyCategory,
    PropertyType,
    Dataset,
    PropertyMeasurement,
    MeasurementCondition,
)

__all__ = [
    "Author",
    "Base",
    "BlogPostMetadata",
    "BlogRole",
    "CacheLevel",
    "Confidence",
    "DataSource",
    "DataSourceAuthor",
    "DefectAnalysisResult",
    "DefectType",
    "ExecutionStatus",
    "Feedback",
    "FeedbackStatus",
    "FeedbackType",
    "FittingMethod",
    "HPCFailoverEvent",
    "HpcJob",
    "HpcJobStatus",
    "JobStatus",
    "JobType",
    "MDSimulationResult",
    "Material",
    "MaterialAlias",
    "MaterialCategory",
    "MaterialComposition",
    "MDVerificationJob",
    "MeasurementCondition",
    "Permission",
    "PostStatus",
    "Potential",
    "PotentialFittingResult",
    "Priority",
    "PropertyCategory",
    "PropertyMeasurement",
    "PropertyType",
    "RefGapFillStaging",
    "StagingStatus",
    "TimestampMixin",
    "Unit",
    "UnitConversion",
    "User",
    "VerificationResultMD",
]
