"""Pydantic schemas for reference value staging API.

Input/output schemas for staging records, matching NFM-54 design
Sections 2.2 (endpoint contracts) and 3.1 (quality gate pipeline).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from nfm_db.models.ref_gap_fill import CacheLevel, Confidence, StagingStatus

# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------


class ReferenceValueInput(BaseModel):
    """Single reference value for bulk staging ingestion.

    Matches the canonical reference_value dict produced by nfm-ref-gapfill.
    """

    element_system: str = Field(
        min_length=1,
        max_length=50,
        description="Element system identifier (e.g. 'U', 'UO2').",
    )
    phase: str | None = Field(
        default=None,
        max_length=50,
        description="Crystal phase or thermodynamic phase (e.g. 'BCC', 'FCC').",
    )
    property: str = Field(
        min_length=1,
        max_length=100,
        description="Property name (e.g. 'lattice_constant', 'bulk_modulus').",
        alias="property_name",
    )
    value: float = Field(description="Numeric property value.")
    unit: str = Field(
        min_length=1,
        max_length=50,
        description="Measurement unit (e.g. 'angstrom', 'GPa').",
    )
    method: str | None = Field(
        default=None,
        max_length=100,
        description="Measurement or calculation method (e.g. 'DFT', 'EXP').",
    )
    source: str = Field(
        min_length=1,
        max_length=200,
        description="Data source identifier (e.g. 'Smirnov2014', 'MP-DFT').",
    )
    source_doi: str | None = Field(
        default=None,
        max_length=200,
        description="DOI of the source publication.",
    )
    confidence: Confidence = Field(
        default=Confidence.MEDIUM,
        description="Confidence level assigned by the extraction pipeline.",
    )
    uncertainty: float | None = Field(
        default=None,
        description="Measurement uncertainty.",
    )
    temperature: float | None = Field(
        default=None,
        description="Measurement temperature in Kelvin.",
    )
    cache_level: CacheLevel | None = Field(
        default=None,
        description="NFM reference cache level (L1, L2, L3A, L3B).",
    )

    model_config = {"populate_by_name": True}


class BulkStagingRequest(BaseModel):
    """Request body for POST /api/reference-values/bulk."""

    values: list[ReferenceValueInput] = Field(
        min_length=1,
        max_length=1000,
        description="Reference values to stage (max 1000 per request).",
    )


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


class StagingRecordResponse(BaseModel):
    """Single staging record in API responses."""

    id: UUID
    element_system: str
    phase: str | None
    property_name: str
    value: float
    unit: str
    method: str | None
    source: str
    source_doi: str | None
    uncertainty: float | None
    temperature: float | None
    confidence: Confidence
    dedup_hash: str
    range_validated: bool
    status: StagingStatus
    review_note: str | None
    reviewer_id: UUID | None
    reviewed_at: datetime | None
    promoted_to_pm_id: UUID | None
    promoted_at: datetime | None
    cache_level: CacheLevel | None
    fill_batch_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BulkStagingItemResult(BaseModel):
    """Result for a single item in bulk staging response."""

    staging_id: UUID | None = None
    status: str = Field(
        description="Result status: auto_staged, pending_review, duplicate, rejected."
    )
    confidence: Confidence


class BulkStagingResponse(BaseModel):
    """Response body for POST /api/reference-values/bulk."""

    accepted: int
    rejected: int
    results: list[BulkStagingItemResult]


class ReviewRequest(BaseModel):
    """Request body for approve/reject endpoints."""

    review_note: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional review note explaining the decision.",
    )


class ReviewResponse(BaseModel):
    """Response body for approve/reject endpoints."""

    staging_id: UUID
    status: StagingStatus
    review_note: str | None = None
    property_measurement_id: UUID | None = Field(
        default=None,
        description="ID of the promoted property_measurement (only for approve).",
    )
    material_id: UUID | None = Field(
        default=None,
        description="ID of the resolved material (only for approve).",
    )


# ---------------------------------------------------------------------------
# Query schemas
# ---------------------------------------------------------------------------


class PendingReviewQuery(BaseModel):
    """Query parameters for GET /api/reference-values/pending-review."""

    element_system: str | None = None
    phase: str | None = None
    property_name: str | None = None
    confidence: Confidence | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class PendingReviewResponse(BaseModel):
    """Response body for GET /api/reference-values/pending-review."""

    records: list[StagingRecordResponse]
    total: int
    page: int
    per_page: int
