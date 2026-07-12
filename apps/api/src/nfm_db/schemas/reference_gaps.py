"""Pydantic schemas for reference gap API endpoints.

Contracts per NFM-54 design Sections 2.1-2.3.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from nfm_db.models.ref_gap_fill import CacheLevel, Confidence
from nfm_db.schemas.common import ApiResponse

# ---------------------------------------------------------------------------
# GET /api/reference-gaps — List gaps
# ---------------------------------------------------------------------------


class ReferenceGapItem(BaseModel):
    """A single gap tuple in the gap list response."""

    element_system: str
    phase: str | None = None
    property_name: str
    priority: int = Field(description="Gap priority ranking (lower = higher priority).")


class ReferenceGapsListQuery(BaseModel):
    """Query parameters for listing reference gaps."""

    element_system: str | None = None
    phase: str | None = None
    property_name: str | None = None
    sort_by: str = Field(default="priority", pattern=r"^(priority|element_system)$")
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)


class ReferenceGapsListResponse(BaseModel):
    """Response body for GET /api/reference-gaps."""

    gaps: list[ReferenceGapItem]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# GET /api/reference-gaps/summary — Coverage statistics
# ---------------------------------------------------------------------------


class SystemCoverageBreakdown(BaseModel):
    """Coverage breakdown per element_system/phase group."""

    element_system: str
    phase: str | None = None
    total: int
    covered: int
    gaps: int


class ReferenceGapsSummaryResponse(BaseModel):
    """Response body for GET /api/reference-gaps/summary."""

    total_target_tuples: int
    covered: int
    gaps: int
    coverage_percent: float
    by_system: list[SystemCoverageBreakdown]
    staging_pending: int
    staging_approved: int


# ---------------------------------------------------------------------------
# POST /api/reference-gaps/fill — Trigger fill operation
# ---------------------------------------------------------------------------


class FillRequest(BaseModel):
    """Request body for POST /api/reference-gaps/fill."""

    element_system: str = Field(min_length=1, max_length=50)
    phase: str | None = Field(default=None, max_length=50)
    property_name: str = Field(min_length=1, max_length=100)
    cache_levels: list[CacheLevel] = Field(
        default_factory=lambda: [CacheLevel.L1, CacheLevel.L2],
        description="Cache levels to search for reference values.",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, discover values but do not stage them.",
    )


class FillResultItem(BaseModel):
    """Result for a single gap targeted by the fill operation."""

    element_system: str
    phase: str | None = None
    property_name: str
    status: str = Field(description="Result status: staged, duplicate, not_found.")
    confidence: Confidence | None = None
    source: str | None = None


class FillResponse(BaseModel):
    """Response body for POST /api/reference-gaps/fill."""

    batch_id: UUID | None = Field(
        default=None,
        description="Batch ID for tracking. None when dry_run=True.",
    )
    gaps_targeted: int
    values_found: int
    staged: int
    duplicates: int
    results: list[FillResultItem]


# ---------------------------------------------------------------------------
# POST /api/reference-gaps/scan — Manual gap scan
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Request body for POST /api/reference-gaps/scan."""

    element_systems: list[str] | None = Field(
        default=None,
        description="Filter scan to specific element systems. None = scan all.",
    )


class ScanResultItem(BaseModel):
    """Result for a single element_system in the scan."""

    element_system: str
    phase: str | None = None
    gaps_found: int
    properties_scanned: int


class ScanResponse(BaseModel):
    """Response body for POST /api/reference-gaps/scan."""

    total_gaps_found: int
    systems_scanned: int
    results: list[ScanResultItem]


# ---------------------------------------------------------------------------
# Shared API envelope (generic)
# ---------------------------------------------------------------------------

ReferenceGapsApiResponse = ApiResponse[
    ReferenceGapsListResponse | ReferenceGapsSummaryResponse | FillResponse | ScanResponse | None
]
