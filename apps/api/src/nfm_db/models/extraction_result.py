"""Extraction result model with source provenance and review tracking."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ExtractionResult(Base):
    """A single extracted data point with source provenance for review.

    Tracks the original source paragraph, page number, and DOI
    so reviewers can trace any extraction back to the literature.
    """

    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("extraction_jobs.id"),
        nullable=True,
    )
    property_name: Mapped[str] = mapped_column(String(500), default="")
    value: Mapped[Any | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str | None] = mapped_column(String, default=None)

    # Source provenance fields for data traceability
    item_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Type of extracted item (property, entity, etc.)",
    )
    item_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        comment="Extracted item payload (property value, entity fields, etc.)",
    )
    source_paragraph: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original paragraph from the source literature",
    )
    source_page: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Page number in the source document",
    )
    source_doi: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="DOI of the source publication",
    )

    # Review tracking
    review_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
        comment="Review status: pending, approved, rejected, needs_revision, corrected",
    )
    review_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reviewer notes from review process",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="ID of the reviewer who last acted on this item",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last review action",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
