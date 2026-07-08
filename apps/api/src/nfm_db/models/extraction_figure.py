"""ExtractionFigure ORM model (NFM-852).

Stores extracted figures (plots, charts, diagrams) from literature
sources with their bounding boxes, captions, and extracted data.

Spec reference: section 6.1 (New tables: extraction_figures).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.source import DataSource


class ExtractionFigure(TimestampMixin, Base):
    """An extracted figure from a literature source.

    Stores plots, charts, diagrams, and other visual content
    extracted during the multimodal extraction pipeline along with
    their bounding boxes, captions, and structured extracted data.
    """

    __tablename__ = "extraction_figures"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_type: Mapped[str] = mapped_column(String(50), nullable=False)
    bounding_box: Mapped[dict | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    extracted_data: Mapped[dict] = mapped_column(
        CompatJSONB,
        nullable=False,
        default=dict,
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )
    extraction_method: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # -- relationships --
    source: Mapped["DataSource | None"] = relationship(
        back_populates="extraction_figures",
    )

    def __repr__(self) -> str:
        return (
            f"<ExtractionFigure id={self.id!s} "
            f"type={self.figure_type!r} page={self.page_number}>"
        )
