"""Shared schemas for batch import/export operations (NFM-1085).

Reusable request/response models for CSV and JSON batch operations
across materials, properties, and reference_values entities.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BatchRowError(BaseModel):
    """Error detail for a single failed row in a batch import."""

    row: int
    field: str
    message: str


class BatchImportResult(BaseModel):
    """Result of a batch CSV/JSON import operation.

    ``imported`` counts rows successfully upserted.
    ``skipped`` counts rows skipped as duplicates.
    ``failed`` counts rows that had validation errors (details in ``errors``).
    """

    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[BatchRowError] = Field(default_factory=list)


class BatchExportQuery(BaseModel):
    """Query parameters for batch export."""

    format: str = Field(..., pattern="^(csv|json)$", description="Export format")
    page: int = Field(default=1, ge=1, description="Page number")
    per_page: int = Field(default=1000, ge=1, le=10000, description="Rows per page")


class BatchExportResponse(BaseModel):
    """Metadata returned with a batch export file download."""

    entity: str
    format: str
    total_rows: int
    filename: str
    exported_at: datetime


# --- Property measurement import row ---


class PropertyMeasurementRow(BaseModel):
    """Single row for property measurement batch import.

    All fields optional except at least one value field must be present.
    """

    dataset_id: UUID | None = None
    property_type_id: UUID | None = None
    value_scalar: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_expression: str | None = None
    value_list: list[float] | None = None
    value_text: str | None = None
    uncertainty: float | None = None
    unit_id: UUID | None = None
    notes: str | None = None


# --- Reference value import row ---


class ReferenceValueRow(BaseModel):
    """Single row for reference value batch import."""

    element_system: str = Field(min_length=1, max_length=50)
    phase: str | None = Field(default=None, max_length=50)
    property_name: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=50)
    method: str | None = Field(default=None, max_length=100)
    source: str = Field(min_length=1, max_length=200)
    source_doi: str | None = Field(default=None, max_length=200)
    uncertainty: float | None = None
    temperature: float | None = None
