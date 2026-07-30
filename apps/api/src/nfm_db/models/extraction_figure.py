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
    """A figure extracted from a document during extraction pipeline.

    Model fields mirror the production `extraction_figures` table schema
    (see alembic migration that created it). Columns in the DB but not in
    the model (bounding_box, caption, image_path, confidence,
    extraction_method) are not selected by SQLAlchemy core unless a column
    is added here — for SELECT on delete we only need the fields the model
    declares so we never SELECT a missing column.
    """

    __tablename__ = "extraction_figures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("data_sources.id"), nullable=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    figure_type: Mapped[str | None] = mapped_column(String, nullable=True)
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    # Columns from the production DB schema that were previously missing
    # from the model. Added so SELECT queries for the literature detail
    # panel can access figure image paths, captions, and confidence scores.
    image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    caption: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(
        nullable=False, default=0.0
    )
