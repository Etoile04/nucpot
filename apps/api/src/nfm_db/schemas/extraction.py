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
    """A single property extracted by the OntoFuel module (v4-aligned).

    Field order (NFM-526):
    source_file → material_name → composition → phase → element →
    property_category → property → value → unit → conditions →
    context → confidence → reference
    """

    # --- v4 core fields (NFM-526) ---
    source_file: str | None = Field(
        default=None, description="Source Markdown file path relative to project root"
    )
    material_name: str | None = Field(
        default=None, description="Material name, alloy grade, or sample material"
    )
    composition: str | None = Field(
        default=None,
        description="Composition from source text or material name itself",
    )
    phase: str | None = Field(default=None, description="Material phase (alpha, beta, gamma, etc.)")
    element: str | None = Field(
        default=None, description="Element if property is element-specific"
    )
    property_category: str | None = Field(
        default=None, description="Property category from fixed catalog"
    )
    property: str = Field(..., description="Property name")
    value: str = Field(..., description="Numeric value as string (preserves precision)")
    unit: str = Field(..., description="Unit of measurement")
    conditions: dict[str, Any] | None = Field(
        default=None, description="Measurement conditions (temp, pressure, etc.)"
    )
    context: str | None = Field(
        default=None, description="Additional context for understanding the value"
    )
    confidence: str = Field(default="medium", description="Confidence level: high/medium/low")
    reference: str | None = Field(default=None, description="Reference: Author, Title")

    # --- Legacy v3 fields (for backward compatibility) ---
    element_system: str | None = Field(default=None, deprecated=True)
    property_name: str | None = Field(default=None, deprecated=True)
    method: str | None = Field(default=None, description="Extraction method")
    source: str | None = Field(default=None, deprecated=True)
    source_doi: str | None = Field(default=None, description="DOI of source")
    uncertainty: float | None = Field(default=None, description="Measurement uncertainty")
    temperature: float | None = Field(default=None, deprecated=True)
    cache_level: str | None = Field(default=None, description="Cache level (L1, L2, L3A, L3B)")

    # --- Optional v4 field ---
    property_note: str | None = Field(
        default=None, description="Note for catalog-excluded properties"
    )


class ExtractionResult(BaseModel):
    """Summary of an extraction run's output."""

    source_reference: str
    total_extracted: int
    properties: list[ExtractedProperty]
