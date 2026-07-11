"""Schemas for OntoFuel extraction pipeline (NFM-66).

Trigger, status, and response models for the literature extraction
pipeline: PDF → extraction → property mapping → quality gate → staging.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from nfm_db.schemas.vision_extraction import (
    TableData,
    VisionExtractionResult,
)

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


# ---------------------------------------------------------------------------
# V4 extraction API schemas (NFM-558)
# ---------------------------------------------------------------------------


class V4ExtractionSubmitRequest(BaseModel):
    """Request to submit a v4 extraction job.

    Extends the v1 trigger with priority and stricter source_type validation.
    """

    source_reference: str = Field(
        min_length=1,
        max_length=500,
        description="Literature source: DOI, URL, file path, or internal document ID.",
    )
    source_type: str = Field(
        description="Source reference type: doi, url, file, or internal_id.",
    )
    element_systems: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Element filters (e.g. ['U', 'Zr']). Max 20 items.",
    )
    cache_level: str | None = Field(
        default=None,
        max_length=10,
        description="Cache level: L1, L2, L3A, L3B.",
    )
    max_confidence: str | None = Field(
        default=None,
        max_length=10,
        description="Confidence ceiling: high, medium, low.",
    )
    priority: str = Field(
        default="normal",
        description="Job queue priority: normal or high.",
    )

    # --- Multimodal extraction options (NFM-922) ---
    extract_figures: bool = Field(
        default=False,
        description="Enable figure/plot extraction via VLM.",
    )
    extract_tables: bool = Field(
        default=False,
        description="Enable table extraction via VLM.",
    )
    figure_types: list[str] | None = Field(
        default=None,
        description=(
            "Filter figure types to extract: 'line', 'scatter', 'bar', "
            "'heatmap', 'contour'. None means all types."
        ),
    )
    confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum VLM confidence to accept an extraction.",
    )
    conflict_strategy: Literal["prefer_vlm", "prefer_text", "merge", "keep_both"] = Field(
        default="prefer_vlm",
        description=(
            "How to resolve VLM vs text extraction conflicts: "
            "prefer_vlm, prefer_text, merge, keep_both."
        ),
    )


class V4SubmitResponse(BaseModel):
    """Response after submitting a v4 extraction job."""

    job_id: str
    source_reference: str
    source_type: str
    status: str = "queued"
    message: str = "Extraction job queued successfully."
    error_message: str | None = None
    created_at: datetime | None = None


class V4JobProgress(BaseModel):
    """Progress sub-object for job status polling."""

    current_step: str = "queued"
    steps_completed: list[str] = Field(default_factory=list)
    steps_remaining: list[str] = Field(default_factory=list)


class V4StatusResponse(BaseModel):
    """V4 extraction job status with progress tracking."""

    job_id: str
    source_reference: str
    source_type: str
    status: str
    progress: V4JobProgress = Field(default_factory=V4JobProgress)
    extracted_count: int = 0
    staged_count: int = 0
    rejected_count: int = 0
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class V4PropertyResponse(BaseModel):
    """Extended property response with staging and metadata."""

    id: int | None = None
    material_name: str | None = None
    composition: str | None = None
    phase: str | None = None
    element: str | None = None
    property_category: str | None = None
    property: str
    value: str
    unit: str
    conditions: dict[str, Any] | None = None
    context: str | None = None
    confidence: str = "medium"
    reference: str | None = None
    source_file: str | None = None
    job_id: str | None = None
    staging_status: str | None = None
    cache_level: str | None = None
    created_at: datetime | None = None


class V4ResultResponse(BaseModel):
    """Extraction result with pagination metadata."""

    source_reference: str
    job_status: str
    total_extracted: int
    properties: list[V4PropertyResponse] = Field(default_factory=list)
    figures: list[V4FigureResult] = Field(
        default_factory=list,
        description="Extracted figure/plot data from multimodal extraction.",
    )
    tables: list[V4TableResult] = Field(
        default_factory=list,
        description="Extracted table data from multimodal extraction.",
    )


class V4BrowseResponse(BaseModel):
    """Material-system scoped property browsing response."""

    material_system: str
    total_count: int
    properties: list[V4PropertyResponse] = Field(default_factory=list)


class V4ValidateRequest(BaseModel):
    """Validation workflow trigger options."""

    auto_approve: bool = True
    scope: str = Field(
        default="pending_only",
        description="Which properties to validate: 'all' or 'pending_only'.",
    )


class V4ValidateResponse(BaseModel):
    """Validation workflow result summary."""

    job_id: str
    validation_id: str
    total_properties: int
    auto_approved: int = 0
    sent_to_review: int = 0
    flagged: int = 0
    review_url: str | None = None


class V4ConfidenceSummary(BaseModel):
    """Confidence breakdown for a material system."""

    high: int = 0
    medium: int = 0
    low: int = 0


class V4MaterialSystemSummary(BaseModel):
    """Overview of a material system's extracted data."""

    name: str
    display_name: str = ""
    total_properties: int = 0
    categories: list[str] = Field(default_factory=list)
    confidence_summary: V4ConfidenceSummary = Field(
        default_factory=V4ConfidenceSummary,
    )
    pending_review_count: int = 0
    last_extraction_at: datetime | None = None


# ---------------------------------------------------------------------------
# V4 multimodal extraction result schemas (NFM-922)
# ---------------------------------------------------------------------------


class V4FigureResult(BaseModel):
    """Wraps a VisionExtractionResult with page/source context for V4 API."""

    page_number: int = Field(
        ge=0,
        description="Page number in the source document (0-indexed).",
    )
    source_file: str = Field(
        description="Source file path or identifier.",
    )
    extraction: VisionExtractionResult = Field(
        description="Complete VLM extraction result for this figure.",
    )


class V4TableResult(BaseModel):
    """Wraps a TableData with page/source context for V4 API."""

    page_number: int = Field(
        ge=0,
        description="Page number in the source document (0-indexed).",
    )
    source_file: str = Field(
        description="Source file path or identifier.",
    )
    table_data: TableData = Field(
        description="Structured table extraction result.",
    )


class V4MultimodalSummary(BaseModel):
    """Aggregate statistics for multimodal extraction results."""

    total_figures: int = Field(
        default=0,
        ge=0,
        description="Total figures extracted.",
    )
    total_tables: int = Field(
        default=0,
        ge=0,
        description="Total tables extracted.",
    )
    fallback_count: int = Field(
        default=0,
        ge=0,
        description="Number of extractions that used OCR fallback.",
    )
    avg_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Average VLM confidence across all multimodal extractions.",
    )
