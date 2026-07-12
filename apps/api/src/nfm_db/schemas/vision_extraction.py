"""Schemas for VLM-based figure content extraction (NFM-851).

Provides Pydantic models for structured extraction results from plot/chart
images (B1.2) and table images (B1.3) via Vision Language Models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Plot / Chart extraction schemas (B1.2)
# ---------------------------------------------------------------------------


class AxisInfo(BaseModel):
    """Describes a single axis of a plot or chart."""

    label: str = Field(default="", description="Axis label text (e.g. 'Temperature').")
    unit: str = Field(default="", description="Axis unit (e.g. 'K', 'MPa', 'at.%').")
    values: list[float] = Field(
        default_factory=list,
        description="Extracted tick/axis values in display order.",
    )
    scale: str = Field(
        default="linear",
        description="Scale type: 'linear', 'log', 'log10', 'ln'.",
    )


class SeriesData(BaseModel):
    """A single data series in a plot or chart."""

    name: str = Field(default="", description="Series label / legend entry.")
    values: list[float] = Field(
        default_factory=list,
        description="Y-values corresponding to axis tick positions.",
    )
    color: str = Field(
        default="",
        description="Approximate color or marker description from legend.",
    )
    marker_style: str = Field(
        default="",
        description="Marker style if discernible: 'circle', 'square', 'triangle', etc.",
    )


class PlotData(BaseModel):
    """Structured extraction result for a single plot or chart image."""

    title: str = Field(default="", description="Figure title or caption text.")
    plot_type: str = Field(
        default="unknown",
        description="Plot type: 'line', 'scatter', 'bar', 'heatmap', 'contour', 'unknown'.",
    )
    x_axis: AxisInfo = Field(default_factory=AxisInfo)
    y_axis: AxisInfo = Field(default_factory=AxisInfo)
    y2_axis: AxisInfo | None = Field(
        default=None,
        description="Secondary y-axis, if present (twin axes plots).",
    )
    series: list[SeriesData] = Field(
        default_factory=list,
        description="Data series extracted from the plot.",
    )
    legend_entries: list[str] = Field(
        default_factory=list,
        description="Raw legend entry text for verification.",
    )
    annotations: list[str] = Field(
        default_factory=list,
        description="Text annotations within the plot area.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall extraction confidence (0-1).",
    )
    raw_response: dict[str, Any] | None = Field(
        default=None,
        description="Unmodified VLM response for debugging.",
    )


# ---------------------------------------------------------------------------
# Table extraction schemas (B1.3)
# ---------------------------------------------------------------------------


class TableCell(BaseModel):
    """A single cell in an extracted table."""

    value: str = Field(default="", description="Cell text content.")
    row_span: int = Field(default=1, ge=1, description="Number of rows this cell spans.")
    col_span: int = Field(default=1, ge=1, description="Number of columns this cell spans.")
    is_header: bool = Field(default=False, description="Whether this cell is a header cell.")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Per-cell extraction confidence.",
    )


class TableHeader(BaseModel):
    """Column header information for an extracted table."""

    columns: list[str] = Field(
        default_factory=list,
        description="Column header text in left-to-right order.",
    )
    sub_headers: list[str] | None = Field(
        default=None,
        description="Sub-header row text if the table uses multi-row headers.",
    )


class TableData(BaseModel):
    """Structured extraction result for a single table image."""

    title: str = Field(default="", description="Table title or caption text.")
    headers: TableHeader = Field(default_factory=TableHeader)
    rows: list[list[TableCell]] = Field(
        default_factory=list,
        description="Table rows (each a list of cells in column order).",
    )
    num_columns: int = Field(default=0, ge=0, description="Detected column count.")
    num_rows: int = Field(default=0, ge=0, description="Detected data row count (excl. header).")
    has_merged_cells: bool = Field(
        default=False,
        description="Whether merged cells were detected.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Footnotes or notes below the table.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall extraction confidence (0-1).",
    )
    raw_response: dict[str, Any] | None = Field(
        default=None,
        description="Unmodified VLM response for debugging.",
    )


# ---------------------------------------------------------------------------
# Combined extraction result
# ---------------------------------------------------------------------------


class VisionExtractionResult(BaseModel):
    """Unified result wrapping plot or table extraction output."""

    figure_type: str = Field(
        description="Type of figure extracted: 'plot' or 'table'.",
    )
    plot_data: PlotData | None = Field(default=None)
    table_data: TableData | None = Field(default=None)
    source_image_path: str | None = Field(
        default=None,
        description="Path or identifier of the source image.",
    )
    provider: str = Field(
        default="openai",
        description="VLM provider used for extraction.",
    )
    model: str = Field(
        default="",
        description="Model identifier used for extraction.",
    )
    extraction_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Time taken for extraction in milliseconds.",
    )
    fallback_used: bool = Field(
        default=False,
        description="Whether OCR fallback was used instead of VLM.",
    )
