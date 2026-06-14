"""Schemas for verification pipeline (NFM-66).

Export request/response for verify-service integration and
verification callback from the verify-service.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from nfm_db.models.ref_gap_fill import Confidence, StagingStatus

# ---------------------------------------------------------------------------
# Export schemas
# ---------------------------------------------------------------------------


class ExportFilter(BaseModel):
    """Filter criteria for bulk export of reference values for verification."""

    status: StagingStatus | None = Field(
        default=None,
        description="Filter by staging status (default: approved + promoted).",
    )
    element_system: str | None = Field(
        default=None,
        max_length=50,
        description="Filter by element system.",
    )
    phase: str | None = Field(
        default=None,
        max_length=50,
        description="Filter by phase.",
    )
    property_name: str | None = Field(
        default=None,
        max_length=100,
        description="Filter by property name.",
    )
    confidence: Confidence | None = Field(
        default=None,
        description="Filter by confidence level.",
    )
    min_confidence: Confidence | None = Field(
        default=None,
        description="Minimum confidence threshold (inclusive).",
    )
    from_date: datetime | None = Field(
        default=None,
        description="Export records created on or after this date.",
    )
    to_date: datetime | None = Field(
        default=None,
        description="Export records created on or before this date.",
    )


class ExportRequest(BaseModel):
    """Request body for POST /api/v1/reference-values/export."""

    filters: ExportFilter = Field(default_factory=ExportFilter)
    limit: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum number of records to export.",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Pagination offset.",
    )


class ExportedRecord(BaseModel):
    """Single record in the verify-service-compatible export format."""

    id: UUID
    element_system: str
    phase: str | None = None
    property_name: str
    value: float
    unit: str
    method: str | None = None
    source: str
    source_doi: str | None = None
    uncertainty: float | None = None
    temperature: float | None = None
    confidence: Confidence
    status: StagingStatus
    created_at: datetime
    cache_level: str | None = None

    model_config = {"from_attributes": True}


class ExportResponse(BaseModel):
    """Response body for the export endpoint."""

    records: list[ExportedRecord]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------------------
# Verification callback schemas
# ---------------------------------------------------------------------------


class Verdict(str):
    """Verification verdict mapping to A-F grades."""

    A = "A"  # Confirmed — value matches expected range
    B = "B"  # Probable — value within tolerance
    C = "C"  # Uncertain — conflicting sources
    D = "D"  # Suspicious — value outside expected range
    E = "E"  # Likely wrong — contradicts multiple sources
    F = "F"  # Confirmed wrong — known error or retracted


class VerificationResult(BaseModel):
    """Single record verification result from verify-service."""

    staging_id: UUID = Field(
        description="ID of the staging record being verified.",
    )
    verdict: str = Field(
        description="A-F grade verdict from verify-service.",
        pattern=r"^[A-F]$",
    )
    verified_value: float | None = Field(
        default=None,
        description="Corrected value if the verify-service suggests one.",
    )
    verified_uncertainty: float | None = Field(
        default=None,
        description="Updated uncertainty from verification.",
    )
    verified_source: str | None = Field(
        default=None,
        max_length=200,
        description="Source of the verification (e.g., 'Sallee1985', 'MP-DB').",
    )
    verification_note: str | None = Field(
        default=None,
        max_length=2000,
        description="Verification explanation or evidence.",
    )


class VerificationCallbackRequest(BaseModel):
    """Request body for POST /api/v1/reference-values/verify-callback.

    Sent by the verify-service after batch verification completes.
    """

    batch_id: UUID | None = Field(
        default=None,
        description="Client-assigned batch identifier for correlation.",
    )
    results: list[VerificationResult] = Field(
        min_length=1,
        max_length=1000,
        description="Verification results for each staging record.",
    )


class VerificationCallbackItem(BaseModel):
    """Single item result in the callback response."""

    staging_id: UUID
    status: str = Field(description="'updated' or 'not_found'.")


class VerificationCallbackResponse(BaseModel):
    """Response body for the verify-service callback."""

    processed: int
    updated: int
    not_found: int
    results: list[VerificationCallbackItem]
