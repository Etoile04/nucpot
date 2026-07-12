"""Pydantic schemas for the seed API endpoints (NFM-702).

Batch import, progress tracking, quality metrics, and measurement review.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# POST /seed/batch
# ---------------------------------------------------------------------------


class BatchRequest(BaseModel):
    """Request body for triggering a batch seed import."""

    dois: list[str] = Field(
        ...,
        min_length=1,
        description="List of DOI strings to seed into the database",
    )


class BatchResponse(BaseModel):
    """Response confirming a batch import has started."""

    batch_id: str
    total: int
    message: str


# ---------------------------------------------------------------------------
# GET /seed/status/{batch_id}
# ---------------------------------------------------------------------------


class BatchProgress(BaseModel):
    """Real-time progress of a batch import job."""

    batch_id: str
    total: int
    completed: int = 0
    failed: int = 0
    in_progress: int = 0
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /seed/quality
# ---------------------------------------------------------------------------


class QualityCategoryCount(BaseModel):
    """Measurement count grouped by property category."""

    category: str
    count: int


class QualityResponse(BaseModel):
    """Aggregate quality metrics across all extracted measurements."""

    total_extracted: int
    total_measurements: int
    by_category: list[QualityCategoryCount] = Field(default_factory=list)
    avg_confidence: float = 0.0


# ---------------------------------------------------------------------------
# PATCH /seed/review/{measurement_id}
# ---------------------------------------------------------------------------


class ReviewRequest(BaseModel):
    """Request body for reviewing a property measurement."""

    review_status: Literal["approved", "rejected"]
    reviewer_note: str | None = Field(None, description="Optional note from reviewer")


class ReviewResponse(BaseModel):
    """Response after updating review status on a measurement."""

    id: UUID
    review_status: str
    reviewer_note: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}
