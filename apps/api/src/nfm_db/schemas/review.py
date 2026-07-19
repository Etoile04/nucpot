"""Review & Provenance API schemas.

Pydantic models for the 5 review endpoints defined in ADR-NFM-796 §4:
- GET  /pending        → PaginatedResponse[ReviewItemResponse]
- GET  /{id}/source   → SourceProvenanceResponse
- PATCH /{id}          → ReviewItemResponse
- POST /batch          → ReviewBatchResponse
- GET  /stats          → ReviewStatsResponse
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Source provenance
# ---------------------------------------------------------------------------


class ReviewSourceInfo(BaseModel):
    """Source paragraph, page, and DOI for a review item."""

    paragraph: str | None = None
    page: int | None = None
    doi: str | None = None
    title: str | None = None


class SourceProvenanceResponse(BaseModel):
    """Full provenance response including source metadata."""

    paragraph: str | None = None
    page: int | None = None
    doi: str | None = None
    source_title: str | None = None
    journal: str | None = None
    year: int | None = None


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------


class ReviewItemResponse(BaseModel):
    """A single review queue item from any of the 4 reviewable tables."""

    id: UUID
    item_type: str
    item_data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    review_status: str = "pending"
    source: ReviewSourceInfo | None = None
    created_at: datetime


class ReviewStatusUpdate(BaseModel):
    """Request body for updating a single item's review status."""

    status: str
    note: str | None = None


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


class ReviewBatchItem(BaseModel):
    """Single item within a batch review request."""

    id: UUID
    status: str
    note: str | None = None


class ReviewBatchRequest(BaseModel):
    """Request body for batch review submission."""

    items: list[ReviewBatchItem] = Field(min_length=1)


class ReviewBatchResponse(BaseModel):
    """Response for batch review operations."""

    succeeded: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class ReviewStatsResponse(BaseModel):
    """Review statistics: per-status counts aggregated across all 4 tables."""

    pending: int = 0
    approved: int = 0
    rejected: int = 0
    needs_revision: int = 0
    corrected: int = 0
