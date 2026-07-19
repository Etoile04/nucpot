"""Review schemas for the human review system (Phase 3).

Provides request/response models for the cross-table review API
covering extraction_results, kg_nodes, kg_edges, and property_measurements.
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
    """Source provenance info attached to a review item."""

    paragraph: str | None = None
    page: int | None = None
    doi: str | None = None


# ---------------------------------------------------------------------------
# Review items
# ---------------------------------------------------------------------------


class ReviewItemResponse(BaseModel):
    """Single review queue item returned by list/source/status endpoints."""

    id: UUID
    item_type: str
    item_data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    review_status: str = "pending"
    source: ReviewSourceInfo | None = None
    created_at: datetime


class ReviewStatusUpdate(BaseModel):
    """Request body for updating a single review item's status."""

    status: str
    note: str | None = None


class ReviewBatchItem(BaseModel):
    """Single item in a batch review request."""

    id: UUID
    status: str
    note: str | None = None


class ReviewBatchRequest(BaseModel):
    """Request body for batch review submission."""

    items: list[ReviewBatchItem] = Field(min_length=1)


class ReviewBatchResponse(BaseModel):
    """Response for batch review operation."""

    succeeded: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Source provenance lookup
# ---------------------------------------------------------------------------


class SourceProvenanceResponse(BaseModel):
    """Response for source provenance lookup."""

    paragraph: str | None = None
    page: int | None = None
    doi: str | None = None
    source_title: str | None = None
    journal: str | None = None
    year: int | None = None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class ReviewStatsResponse(BaseModel):
    """Review statistics summary across all 4 reviewable tables."""

    pending: int = 0
    approved: int = 0
    rejected: int = 0
    needs_revision: int = 0
    corrected: int = 0
