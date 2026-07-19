"""Extraction result model with source provenance for Phase 3 review.

Stores individual data points extracted from literature sources, with full
traceability back to the source document (paragraph, page, DOI).

Review state machine: pending → approved | rejected | needs_revision → corrected.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ExtractionResult(Base):
    """A single data point extracted from a literature source.

    Each result links to an extraction job and optionally to a data source,
    with provenance fields enabling expert trace-back to the original text.
    """

    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("extraction_jobs.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to the extraction job that produced this result",
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        comment="FK to the literature source this was extracted from",
    )
    property_name: Mapped[str] = mapped_column(String(500), default="")
    item_type: Mapped[str] = mapped_column(
        String(100),
        default="property",
        comment="Type of extracted item (property, entity, relation, etc.)",
    )
    item_data: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        comment="Structured extracted data (varies by item_type)",
    )
    value: Mapped[Any] = mapped_column(JSON, default=None)
    confidence: Mapped[float] = mapped_column(default=0.0)

    # -- Source provenance fields (Phase 3 data traceability) --
    source_paragraph: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Original paragraph text from the source document",
    )
    source_page: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Page number in the source document",
    )
    source_doi: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="DOI of the source publication",
    )

    # -- Review fields --
    review_status: Mapped[str] = mapped_column(
        String(50),
        default="pending",
        comment="pending | approved | rejected | needs_revision | corrected",
    )
    review_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reviewer's notes or reason for decision",
    )
    reviewed_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Identifier of the reviewer who last acted on this item",
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionResult id={self.id!s} "
            f"property={self.property_name!r} status={self.review_status!r}>"
        )
