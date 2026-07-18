"""Extraction result model (stub — full implementation pending)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("extraction_jobs.id"))
    property_name: Mapped[str] = mapped_column(default="")
    value: Mapped[Any] = mapped_column(JSON, default=None)
    confidence: Mapped[float] = mapped_column(default=0.0)
    source: Mapped[str | None] = mapped_column(default=None)
    review_status: Mapped[str] = mapped_column(default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    review_notes: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
