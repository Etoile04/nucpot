"""Pydantic schemas for ExtractionFigure (NFM-852).

Request/response models for the extraction_figures API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ExtractionFigureCreate(BaseModel):
    """Request schema for creating an extraction figure."""

    source_id: UUID | None = Field(
        default=None,
        description="Associated data source ID.",
    )
    page_number: int = Field(
        ge=1,
        description="Page number in the source document.",
    )
    figure_type: str = Field(
        max_length=50,
        description="Type of figure (plot, chart, diagram, table, etc.).",
    )
    bounding_box: dict[str, Any] | None = Field(
        default=None,
        description="Bounding box coordinates {x, y, width, height}.",
    )
    caption: str | None = Field(
        default=None,
        max_length=5000,
        description="Figure caption text.",
    )
    image_path: str | None = Field(
        default=None,
        max_length=500,
        description="File path to stored figure image.",
    )
    extracted_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured data extracted from the figure.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Extraction confidence score 0.0-1.0.",
    )
    extraction_method: str | None = Field(
        default=None,
        max_length=50,
        description="Method used for extraction (ocr, vlm, manual).",
    )


class ExtractionFigureResponse(BaseModel):
    """Response schema for an extraction figure."""

    id: UUID
    source_id: UUID | None = None
    page_number: int
    figure_type: str
    bounding_box: dict[str, Any] | None = None
    caption: str | None = None
    image_path: str | None = None
    extracted_data: dict[str, Any] = Field(
        default_factory=dict,
    )
    confidence: float
    extraction_method: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractionFigureListResponse(BaseModel):
    """Paginated list response for extraction figures."""

    figures: list[ExtractionFigureResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
