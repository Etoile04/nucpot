"""Extraction figure model (stub — full implementation pending)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ExtractionFigure(Base):
    __tablename__ = "extraction_figures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("extraction_jobs.id"))
    file_path: Mapped[str] = mapped_column(String, default="")
