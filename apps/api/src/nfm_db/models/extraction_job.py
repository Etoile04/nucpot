"""ExtractionJob ORM model.

Represents a single extraction pipeline run.  NFM-2013 extended the
stub into a real persistence target so operators can audit what landed
in the database and poll the new
``GET /api/v1/extraction/ingest/{job_id}/status`` endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin


# Status values mirror the contract documented for /ingest/{job_id}/status.
# Mapping is synchronous in the current handler, so the persisted rows
# always land at 'completed' (or 'failed' if map_and_persist raised).
EXTRACTION_JOB_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "completed",
    "failed",
)


class ExtractionJob(TimestampMixin, Base):
    """A single extraction pipeline run (NFM-2013 AC-2 + AC-5).

    The original stub carried only multimodal-extraction flags.  NFM-2013
    added provenance + status fields so the ingest handler can persist
    a row on every POST and the new status endpoint can serve the
    real state instead of the Celery/in-memory fallback facade.
    """

    __tablename__ = "extraction_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Provenance (NFM-2013 AC-2) ---
    source_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="DOI / URL / file path the batch was extracted from.",
    )
    source_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="doi | url | file | internal_id | datasource.",
    )
    corpus_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="External corpus slug the batch was tagged with.",
    )

    # --- Status (NFM-2013 AC-5) ---
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        comment=(
            "pending | processing | completed | failed — see "
            "EXTRACTION_JOB_STATUSES."
        ),
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last failure reason when status='failed'.",
    )

    # --- Counts (NFM-2013 AC-5 / OntoFuel handoff contract) ---
    total_received: Mapped[int] = mapped_column(default=0, nullable=False)
    created_measurements: Mapped[int] = mapped_column(default=0, nullable=False)
    reused_entities: Mapped[int] = mapped_column(default=0, nullable=False)
    skipped_duplicate_measurements: Mapped[int] = mapped_column(
        default=0, nullable=False
    )
    skipped_unknown_properties: Mapped[int] = mapped_column(
        default=0, nullable=False
    )
    skipped_duplicates: Mapped[int] = mapped_column(
        default=0, nullable=False,
        comment="Backward-compat alias: reused + skipped_dup + skipped_unknown.",
    )
    validation_errors: Mapped[int] = mapped_column(default=0, nullable=False)

    # --- Timestamps ---
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- Multimodal extraction flags (preserved from the stub) ---
    extract_figures: Mapped[bool] = mapped_column(default=False)
    extract_tables: Mapped[bool] = mapped_column(default=False)
    confidence_threshold: Mapped[float] = mapped_column(default=0.5)
    figure_types: Mapped[list[str] | None] = mapped_column(
        CompatJSONB, default=None, nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionJob id={self.id!s} status={self.status!r} "
            f"source={self.source_reference!r}>"
        )