"""Schemas for Domain Expert Service (NFM-87.3).

Request/response schemas for verification API endpoints that connect
to the Nuclear Domain Expert Agent.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Common schemas
# ---------------------------------------------------------------------------


class SourceCredibility(str, Enum):
    """Credibility tier for reference sources."""

    NIST_IPR = "nist_ipr"
    PEER_REVIEWED = "peer_reviewed"
    MATERIALS_PROJECT = "materials_project"
    OPENKIM_VERIFIED = "openkim_verified"
    CONFERENCE = "conference"
    PREPRINT = "preprint"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Reference validation schemas
# ---------------------------------------------------------------------------


class CheckGapRequest(BaseModel):
    """Request body for POST /api/v1/verification/check-gap."""

    element_system: str = Field(
        ...,
        max_length=50,
        description="Chemical system (e.g., 'UO2', 'U-Zr').",
    )
    property_name: str = Field(
        ...,
        max_length=100,
        description="Property name (e.g., 'density', 'thermal_conductivity').",
    )
    value: float = Field(
        ...,
        description="Reference value to validate.",
    )
    unit: str = Field(
        ...,
        max_length=20,
        description="Unit of measurement.",
    )
    source: str = Field(
        ...,
        max_length=200,
        description="Literature or database source.",
    )
    source_type: SourceCredibility = Field(
        default=SourceCredibility.UNKNOWN,
        description="Source credibility tier.",
    )
    source_doi: str | None = Field(
        default=None,
        max_length=200,
        description="DOI of the source (if available).",
    )
    method: str | None = Field(
        default=None,
        max_length=100,
        description="Computational or experimental method.",
    )
    uncertainty: float | None = Field(
        default=None,
        ge=0,
        description="Uncertainty in the measurement.",
    )
    temperature: float | None = Field(
        default=None,
        description="Temperature in Kelvin (if applicable).",
    )
    phase: str | None = Field(
        default=None,
        max_length=50,
        description="Phase or crystal structure.",
    )


class LiteratureMatch(BaseModel):
    """A matching reference found in literature search."""

    source_name: str = Field(..., description="Name of the source.")
    source_type: SourceCredibility = Field(..., description="Source credibility.")
    value: float = Field(..., description="Reference value from source.")
    unit: str = Field(..., description="Unit of measurement.")
    uncertainty: float | None = Field(
        default=None,
        description="Uncertainty from source.",
    )
    source_doi: str | None = Field(
        default=None,
        description="DOI of the source.",
    )
    method: str | None = Field(
        default=None,
        description="Method used.",
    )
    agreement_pct: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="% agreement with candidate value.",
    )


class ValidationResult(BaseModel):
    """Result of reference validation workflow."""

    validation_id: UUID = Field(..., description="Unique validation ID.")
    validated_at: datetime = Field(..., description="Validation timestamp.")
    confidence_score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Confidence score (0-1).",
    )
    is_validated: bool = Field(..., description="Whether reference passed validation.")
    needs_escalation: bool = Field(
        ...,
        description="Whether human review is needed.",
    )
    escalation_reason: str | None = Field(
        default=None,
        description="Reason for escalation (if any).",
    )
    literature_matches: list[LiteratureMatch] = Field(
        default_factory=list,
        description="Matching references from literature.",
    )
    estimated_uncertainty: float | None = Field(
        default=None,
        description="Estimated uncertainty (if not provided).",
    )
    source_credibility_score: float = Field(
        ...,
        ge=0,
        le=1,
        description="Source credibility score.",
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes or warnings.",
    )


# ---------------------------------------------------------------------------
# F-grade adjudication schemas
# ---------------------------------------------------------------------------


class AdjudicationRequest(BaseModel):
    """Request body for POST /api/v1/verification/adjudicate-grade."""

    staging_id: UUID = Field(..., description="Staging record ID with F-grade.")
    lammps_log: str | None = Field(
        default=None,
        description="LAMMPS error log (if available).",
    )


class FixRecommendation(BaseModel):
    """A recommended fix for LAMMPS failure."""

    category: str = Field(..., description="Fix category (e.g., 'timestep', 'potential').")
    description: str = Field(..., description="Description of the fix.")
    priority: str = Field(
        default="medium",
        description="Priority level (high, medium, low).",
    )


class AdjudicationAnalysis(BaseModel):
    """Analysis of F-grade failure."""

    element_system: str = Field(..., description="Chemical system.")
    property_name: str = Field(..., description="Property name.")
    value: float = Field(..., description="Failed reference value.")
    error_type: str = Field(..., description="Classified error type.")
    confidence: float = Field(
        ...,
        ge=0,
        le=1,
        description="Confidence in this analysis.",
    )
    suggested_fixes: list[str] = Field(
        default_factory=list,
        description="Suggested fixes based on error type.",
    )


class AdjudicationResponse(BaseModel):
    """Response to F-grade adjudication request."""

    success: bool = Field(..., description="Whether adjudication succeeded.")
    analysis: AdjudicationAnalysis | None = Field(
        default=None,
        description="Detailed analysis of the failure.",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Recommended actions.",
    )
    error: str | None = Field(
        default=None,
        description="Error message (if adjudication failed).",
    )


# ---------------------------------------------------------------------------
# Conflict resolution schemas
# ---------------------------------------------------------------------------


class ConflictResolutionRequest(BaseModel):
    """Request body for POST /api/v1/verification/resolve-conflict."""

    staging_ids: list[UUID] = Field(
        ...,
        min_length=2,
        max_length=10,
        description="List of conflicting staging record IDs.",
    )


class RankedSource(BaseModel):
    """A ranked source in conflict resolution."""

    id: UUID = Field(..., description="Staging record ID.")
    source: str = Field(..., description="Source name.")
    method: str | None = Field(default=None, description="Method used.")
    uncertainty: float | None = Field(default=None, description="Uncertainty.")
    rank: int = Field(..., ge=1, description="Rank position (1 = best).")


class ConflictResolutionResponse(BaseModel):
    """Response to conflict resolution request."""

    success: bool = Field(..., description="Whether resolution succeeded.")
    primary_source_id: UUID | None = Field(
        default=None,
        description="ID of recommended primary source.",
    )
    primary_source: str | None = Field(
        default=None,
        description="Name of recommended primary source.",
    )
    rationale: str | None = Field(
        default=None,
        description="Explanation for ranking decision.",
    )
    all_ranked: list[RankedSource] = Field(
        default_factory=list,
        description="All sources ranked by quality.",
    )
    error: str | None = Field(
        default=None,
        description="Error message (if resolution failed).",
    )


# ---------------------------------------------------------------------------
# External data source query schemas
# ---------------------------------------------------------------------------


class ExternalDataSource(str, Enum):
    """External nuclear materials data sources."""

    NIST_IPR = "nist_ipr"
    OPENKIM = "openkim"
    MATERIALS_PROJECT = "materials_project"


class ExternalQueryRequest(BaseModel):
    """Request body for POST /api/v1/verification/query-external."""

    source: ExternalDataSource = Field(..., description="Data source to query.")
    formula: str | None = Field(
        default=None,
        max_length=50,
        description="Chemical formula (for NIST IPR, Materials Project).",
    )
    species: str | None = Field(
        default=None,
        max_length=50,
        description="Chemical species (for OpenKIM).",
    )
    property_name: str | None = Field(
        default=None,
        max_length=100,
        description="Optional property filter.",
    )


class ExternalQueryResponse(BaseModel):
    """Response from external data source query."""

    success: bool = Field(..., description="Whether query succeeded.")
    source: str = Field(..., description="Data source name.")
    data: dict | None = Field(
        default=None,
        description="Query results (structure varies by source).",
    )
    cached: bool = Field(
        default=False,
        description="Whether results were from cache.",
    )
    error: str | None = Field(
        default=None,
        description="Error message (if query failed).",
    )
