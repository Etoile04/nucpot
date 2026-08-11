"""ExtractionStep ORM model.

Represents a single step within an extraction pipeline run (NFM-2567).
Each job comprises an ordered sequence of steps: chunk, extract, map,
quality_gate, and gap_scan.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin

# Allowed step types in the extraction pipeline.
EXTRACTION_STEP_TYPES: tuple[str, ...] = (
    "chunk",
    "extract",
    "map",
    "quality_gate",
    "gap_scan",
)

# Allowed statuses for a pipeline step.
EXTRACTION_STEP_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
)


class ExtractionStep(TimestampMixin, Base):
    """A single step within an extraction pipeline run (NFM-2567).

    Tracks per-step status, timing, and error state so operators can
    pinpoint failures and re-run individual steps.
    """

    __tablename__ = "extraction_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Parent job ---
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        comment="Parent extraction job this step belongs to.",
    )

    # --- Step identity ---
    step_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "chunk | extract | map | quality_gate | gap_scan — see "
            "EXTRACTION_STEP_TYPES."
        ),
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        comment=(
            "pending | running | completed | failed | skipped — see "
            "EXTRACTION_STEP_STATUSES."
        ),
    )

    # --- Skip detection ---
    input_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Input fingerprint for skip detection on repeated runs.",
    )

    # --- Output reference ---
    output_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        comment="Reference to the product artifact this step produced.",
    )

    # --- Timestamps ---
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --- Error tracking ---
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Last failure reason when status='failed'.",
    )

    # --- Flexible metadata ---
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment=(
            "Arbitrary step metadata. Trailing underscore avoids "
            "SQLAlchemy MetaData name collision."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionStep id={self.id!s} job_id={self.job_id!s} "
            f"type={self.step_type!r} status={self.status!r}>"
        )
