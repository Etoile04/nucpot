"""Pydantic schemas for DataCollectionRequest (NFM-2619).

API response shapes for ``DataCollectionRequest`` records and for
``CoverageMetrics`` summaries (mirrors ``RecallMetrics`` pattern from
the gap-scanner module).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DataCollectionRequestResponse(BaseModel):
    """Public representation of a ``DataCollectionRequest`` row."""

    id: uuid.UUID = Field(
        description="Globally unique request identifier (UUID v4).",
    )
    ontology_version_id: uuid.UUID = Field(
        description="Ontology version that defines the expected schema.",
    )
    entity_type: str = Field(
        description="Entity type, e.g. NuclearMaterial, Isotope.",
        max_length=100,
    )
    property: str = Field(
        description="Property name, e.g. thermal_conductivity, density.",
        max_length=200,
    )
    material_system: str = Field(
        description="Material system, e.g. UO2, Zr, U.",
        max_length=200,
    )
    urgency: int = Field(
        ge=0,
        description="Higher = more urgently needed.",
    )
    source_preference: str = Field(
        description="literature | dft | external_db | any.",
        max_length=30,
    )
    status: str = Field(
        description="open | in_progress | completed | declined.",
        max_length=20,
    )
    requested_at: datetime = Field(
        description="When the request was created (UTC).",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the request reached a terminal status (UTC).",
    )
    metadata_: dict[str, Any] | None = Field(
        default=None,
        description="Flexible metadata bag.",
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CoverageMetricsResponse(BaseModel):
    """Aggregated coverage statistics for an ontology version.

    Mirrors the ``RecallMetrics`` dataclass from gap_scanner but
    scoped to data-collection coverage.
    """

    ontology_version_id: uuid.UUID = Field(
        description="Ontology version these metrics cover.",
    )
    total_requests: int = Field(
        ge=0,
        description="Total data-collection requests for this ontology version.",
    )
    open_requests: int = Field(
        ge=0,
        description="Requests with status ``open``.",
    )
    in_progress_requests: int = Field(
        ge=0,
        description="Requests with status ``in_progress``.",
    )
    completed_requests: int = Field(
        ge=0,
        description="Requests with status ``completed``.",
    )
    declined_requests: int = Field(
        ge=0,
        description="Requests with status ``declined``.",
    )
    coverage_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of requests completed (completed / total).",
    )
    computed_at: datetime = Field(
        description="When these metrics were calculated (UTC).",
    )

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CoverageMetricsResponse",
    "DataCollectionRequestResponse",
]
