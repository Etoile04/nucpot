"""Placeholder review schemas — minimal stubs (NFM-795).

Unblocks conftest.py → main.py import chain. Full implementation pending.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReviewBatchRequest(BaseModel):
    """Request body for batch review submission."""

    item_ids: list[UUID] = Field(default_factory=list)
    decision: str = "approved"
    comment: str | None = None


class ReviewBatchResponse(BaseModel):
    """Response for batch review operation."""

    processed: int = 0
    approved: int = 0
    rejected: int = 0


class ReviewItemResponse(BaseModel):
    """Single review queue item."""

    id: UUID
    item_type: str
    status: str = "pending"
    created_at: datetime


class ReviewSourceInfo(BaseModel):
    """Source provenance info for a review item."""

    source_type: str | None = None
    source_id: UUID | None = None
    source_url: str | None = None


class ReviewStatsResponse(BaseModel):
    """Review statistics summary."""

    total: int = 0
    approved: int = 0
    rejected: int = 0
    pending: int = 0


class ReviewStatusUpdate(BaseModel):
    """Request body for updating a review status."""

    status: str
    comment: str | None = None


class SourceProvenanceResponse(BaseModel):
    """Response for source provenance lookup."""

    source_type: str | None = None
    source_id: UUID | None = None
    source_url: str | None = None
    confidence: float | None = None
