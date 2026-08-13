"""Schemas for figure detection pipeline (NFM-850).

Provides Pydantic models for layout analysis results:
- ``BoundingBox`` — rectangular region coordinates
- ``FigureType`` — classification enum for detected figures
- ``DetectedFigure`` — single figure with type, location, and confidence
- ``PageDetectionResult`` — per-page detection output
- ``FigureDetectionResult`` — full document result wrapping all pages
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    """Rectangular region in image coordinates.

    All values are in pixels relative to the page image origin
    (top-left corner). Positive x goes right, positive y goes down.
    """

    x: int = Field(ge=0, description="Left edge x-coordinate in pixels.")
    y: int = Field(ge=0, description="Top edge y-coordinate in pixels.")
    width: int = Field(gt=0, description="Region width in pixels.")
    height: int = Field(gt=0, description="Region height in pixels.")


# ---------------------------------------------------------------------------
# Figure type classification
# ---------------------------------------------------------------------------


class FigureType(StrEnum):
    """Classification of a detected figure region."""

    PLOT = "plot"
    TABLE = "table"
    MICROSTRUCTURE = "microstructure"
    DIAGRAM = "diagram"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Detected figure
# ---------------------------------------------------------------------------


class DetectedFigure(BaseModel):
    """A single figure detected within a page image.

    Combines the bounding box location with classification metadata.
    """

    figure_type: FigureType = Field(
        default=FigureType.UNKNOWN,
        description="Classification of the detected figure.",
    )
    bounding_box: BoundingBox = Field(
        description="Location and dimensions of the figure region.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Detection confidence score (0-1).",
    )
    caption: str = Field(
        default="",
        description="Extracted caption or label text near the figure.",
    )
    page_index: int = Field(
        default=0,
        ge=0,
        description="Zero-based page index where the figure was detected.",
    )


# ---------------------------------------------------------------------------
# Per-page result
# ---------------------------------------------------------------------------


class PageDetectionResult(BaseModel):
    """Detection results for a single PDF page."""

    page_index: int = Field(
        ge=0,
        description="Zero-based page index.",
    )
    page_width: int = Field(
        default=0,
        ge=0,
        description="Page image width in pixels.",
    )
    page_height: int = Field(
        default=0,
        ge=0,
        description="Page image height in pixels.",
    )
    figures: list[DetectedFigure] = Field(
        default_factory=list,
        description="Figures detected on this page.",
    )


# ---------------------------------------------------------------------------
# Full document result
# ---------------------------------------------------------------------------


class FigureDetectionResult(BaseModel):
    """Complete figure detection result for a PDF document.

    Aggregates per-page results with summary metadata.
    """

    source_path: str = Field(
        default="",
        description="Path or identifier of the source PDF.",
    )
    total_pages: int = Field(
        default=0,
        ge=0,
        description="Total number of pages processed.",
    )
    total_figures: int = Field(
        default=0,
        ge=0,
        description="Total figures detected across all pages.",
    )
    pages: list[PageDetectionResult] = Field(
        default_factory=list,
        description="Per-page detection results.",
    )
    provider: str = Field(
        default="",
        description="Detection provider used (e.g., 'vlm', 'layoutparser').",
    )
    processing_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Total processing time in milliseconds.",
    )
