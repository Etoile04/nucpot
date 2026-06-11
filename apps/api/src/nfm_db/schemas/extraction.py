"""Schemas for OntoFuel extraction pipeline (NFM-66).

Trigger, status, and response models for the literature extraction
pipeline: PDF → extraction → property mapping → quality gate → staging.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Trigger schemas
# ---------------------------------------------------------------------------


class ExtractionTriggerRequest(BaseModel):
    """Request to trigger extraction for a literature source.

    The source_reference can be a file path, URL, DOI, or internal
    reference ID that the OntoFuel module knows how to resolve.
    """

    source_reference: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Literature source reference: file path (PDF), DOI, URL, "
            "or internal document ID."
        ),
    )
    source_type: str = Field(
        default="doi",
        max_length=20,
        description="Type of source reference: 'doi', 'url', 'file', 'internal_id'.",
    )
    element_systems: list[str] | None = Field(
        default=None,
        max_length=20,
        description=(
            "Optional list of element systems to extract (e.g., ['U', 'Pu']). "
            "If None, extract all found elements."
        ),
    )
    cache_level: str | None = Field(
        default=None,
        max_length=10,
        description="Cache level to tag extracted values with (L1, L2, L3A, L3B).",
    )
    max_confidence: str | None = Field(
        default=None,
        max_length=10,
        description="Maximum confidence level to auto-stage (high = all, medium = review, low = skip).",
    )


class ExtractionTriggerResponse(BaseModel):
    """Response after triggering an extraction job."""

    job_id: UUID = Field(description="Unique job identifier for status tracking.")
    source_reference: str
    source_type: str
    status: str = Field(
        default="queued",
        description="Initial job status: 'queued'.",
    )
    message: str = Field(
        default="Extraction job queued successfully.",
    )


# ---------------------------------------------------------------------------
# Status schemas
# ---------------------------------------------------------------------------


class ExtractionStatusResponse(BaseModel):
    """Status of an extraction job."""

    job_id: UUID
    source_reference: str
    source_type: str
    status: str = Field(
        description=(
            "Job status: 'queued', 'running', 'extracting', 'mapping', "
            "'quality_gate', 'completed', 'failed', 'partial'."
        ),
    )
    extracted_count: int = Field(
        default=0,
        description="Number of properties extracted from the source.",
    )
    staged_count: int = Field(
        default=0,
        description="Number of values that passed quality gate and were staged.",
    )
    rejected_count: int = Field(
        default=0,
        description="Number of values rejected by quality gate.",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if status is 'failed'.",
    )
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Pipeline result schemas
# ---------------------------------------------------------------------------


class ExtractedProperty(BaseModel):
    """A single property extracted by the OntoFuel module."""

    element_system: str
    phase: str | None = None
    property_name: str
    value: float
    unit: str
    method: str | None = None
    source: str
    source_doi: str | None = None
    confidence: str = "medium"
    uncertainty: float | None = None
    temperature: float | None = None
    cache_level: str | None = None


class ExtractionResult(BaseModel):
    """Summary of an extraction run's output."""

    source_reference: str
    total_extracted: int
    properties: list[ExtractedProperty]
