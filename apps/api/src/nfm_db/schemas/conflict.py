"""Pydantic schemas for conflict resolution API.

Provides request/response models for:
  - Conflict listing (GET /kg/conflicts)
  - Conflict resolution (POST /kg/conflicts/{id}/resolve)
  - Multi-source fusion pipeline results
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Conflict Value Entry (an individual value in a conflict)
# ---------------------------------------------------------------------------


class ConflictingValueEntry(BaseModel):
    """One value in a set of conflicting property values."""

    value: dict[str, Any] = Field(
        description="The property value (e.g. {'scalar': 10.5, 'unit': 'W/mK'})",
    )
    source_id: uuid.UUID | None = Field(
        default=None,
        description="Data source this value came from",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score of this extraction",
    )
    extracted_at: datetime | None = Field(
        default=None,
        description="When this value was extracted",
    )


# ---------------------------------------------------------------------------
# Conflict Response
# ---------------------------------------------------------------------------


class ConflictResponse(BaseModel):
    """A single conflict record returned by the API."""

    id: uuid.UUID
    material_node_id: uuid.UUID
    property_node_id: uuid.UUID
    property_type_id: uuid.UUID | None = None
    conflicting_values: list[dict[str, Any]]
    strategy: str
    resolved_value: dict[str, Any] | None = None
    status: str
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    resolution_notes: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Conflict List Response
# ---------------------------------------------------------------------------


class ConflictListResponse(BaseModel):
    """Paginated list of conflicts."""

    conflicts: list[ConflictResponse]
    total: int


# ---------------------------------------------------------------------------
# Resolution Request
# ---------------------------------------------------------------------------


class ResolveConflictRequest(BaseModel):
    """Request body for manually resolving a conflict."""

    strategy_override: str | None = Field(
        default=None,
        description="Override strategy for this resolution",
    )
    resolved_value: dict[str, Any] | None = Field(
        default=None,
        description="Manually chosen resolved value",
    )
    notes: str | None = Field(
        default=None,
        description="Human reviewer notes",
    )


# ---------------------------------------------------------------------------
# Resolution Response
# ---------------------------------------------------------------------------


class ResolveConflictResponse(BaseModel):
    """Response after resolving a conflict."""

    id: uuid.UUID
    strategy: str
    resolved_value: dict[str, Any] | None
    status: str
    resolved_at: datetime | None = None


# ---------------------------------------------------------------------------
# Fusion Result
# ---------------------------------------------------------------------------


class FusionResult(BaseModel):
    """Result of a multi-source fusion run."""

    conflicts_detected: int
    conflicts_resolved: int
    conflicts_escalated: int
    errors: list[str] = Field(default_factory=list)
