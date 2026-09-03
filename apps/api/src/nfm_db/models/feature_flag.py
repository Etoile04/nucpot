"""Runtime feature-flag model (internal flag service, NFM-4180)."""

from sqlalchemy import Boolean, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class FeatureFlag(TimestampMixin, Base):
    """A runtime-toggleable feature flag.

    Replaces the build-time `NEXT_PUBLIC_DATA_LOSS_NOTICE` env-var gate
    (NFM-4146-FU2 / NFM-4180): values live in the database, are read at
    request time by `/api/v1/feature-flags/{key}/evaluate`, and support
    percentage-rollout cohort targeting without a redeploy.
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    rollout_percentage: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
