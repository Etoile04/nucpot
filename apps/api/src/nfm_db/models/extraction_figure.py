"""Extraction figure model — full implementation matching tests.

Stores figure metadata extracted from documents.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ExtractionFigure(Base):
    """A figure extracted from a document during extraction pipeline."""

    __tablename__ = "extraction_figures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("extraction_jobs.id"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    figure_type: Mapped[str | None] = mapped_column(String, nullable=True)
    file_path: Mapped[str] = mapped_column(String, default="")
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
