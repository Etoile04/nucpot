"""ExtractionJob ORM model (stub).

Represents a single extraction pipeline run. The full model
will be implemented in a future Phase 2 issue. This stub exists
solely to satisfy the FK on ExtractionFigure.job_id so that
``Base.metadata.create_all`` succeeds in the SQLite test environment.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, JSONArray, TimestampMixin


class ExtractionJob(TimestampMixin, Base):
    """A single extraction pipeline run (stub)."""

    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    # Multimodal extraction fields (NFM-979 cherry-pick)
    extract_figures: Mapped[bool] = mapped_column(default=False)
    extract_tables: Mapped[bool] = mapped_column(default=False)
    confidence_threshold: Mapped[float] = mapped_column(default=0.5)
    figure_types: Mapped[list[str] | None] = mapped_column(
        JSONArray, default=None, nullable=True,
    )
