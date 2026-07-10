"""SQLAlchemy ORM base and common mixins."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Text, TypeDecorator, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

if TYPE_CHECKING:
    pass


class JSONArray(TypeDecorator[list[str] | None]):
    """PostgreSQL ARRAY ↔ JSON text for cross-database compatibility.

    On PostgreSQL, uses native ARRAY(Text).
    On SQLite and other databases, serializes lists as JSON text.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value


class CompatJSONB(TypeDecorator[dict[str, Any] | None]):
    """PostgreSQL JSONB ↔ JSON text for cross-database compatibility.

    On PostgreSQL, uses native JSONB.
    On SQLite and other databases, serializes dicts as JSON text.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, str):
            return json.loads(value)
        return value


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
from nfm_db.models.conflict import (  # noqa: E402
    ConflictRecord,
    ConflictStatus,
    ResolutionStrategy,
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
from nfm_db.models.kg import (  # noqa: E402
    VALID_NODE_TYPES,
    VALID_RELATION_TYPES,
    KGEdge,
    KGNode,
    KGReviewQueue,
    OntologyIdMap,
)
from nfm_db.models.material import (  # noqa: E402
    Material,
    MaterialAlias,
    MaterialCategory,
    MaterialComposition,
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
from nfm_db.models.ontology import (  # noqa: E402
    KEntityType,
    KRelationType,
)
from nfm_db.models.potential import Potential  # noqa: E402
from nfm_db.models.property import (  # noqa: E402
    Dataset,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.models.ref_gap_fill import (  # noqa: E402
    CacheLevel,
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.models.source import (  # noqa: E402
    Author,
    DataSource,
    DataSourceAuthor,
)
from nfm_db.models.unit import (  # noqa: E402
    Unit,
    UnitConversion,
)
from nfm_db.models.user import (  # noqa: E402
    BlogRole,
    Permission,
    User,
)

__all__ = [
    "VALID_NODE_TYPES",
    "VALID_RELATION_TYPES",
    "Author",
    "Base",
    "BlogPostMetadata",
    "BlogRole",
    "CacheLevel",
    "CompatJSONB",
    "Confidence",
    "ConflictRecord",
    "ConflictStatus",
    "DataSource",
    "DataSourceAuthor",
    "Dataset",
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
    "JSONArray",
    "JobStatus",
    "JobType",
    "KEntityType",
    "KGEdge",
    "KGNode",
    "KGReviewQueue",
    "KRelationType",
    "MDSimulationResult",
    "MDVerificationJob",
    "Material",
    "MaterialAlias",
    "MaterialCategory",
    "MaterialComposition",
    "MeasurementCondition",
    "OntologyIdMap",
    "Permission",
    "PostStatus",
    "Potential",
    "PotentialFittingResult",
    "Priority",
    "PropertyCategory",
    "PropertyMeasurement",
    "PropertyType",
    "RefGapFillStaging",
    "ResolutionStrategy",
    "StagingStatus",
    "TimestampMixin",
    "Unit",
    "UnitConversion",
    "User",
    "VerificationResultMD",
]
