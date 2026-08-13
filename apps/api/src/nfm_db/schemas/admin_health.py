"""Admin health alert schemas (NFM-2414).

Structured error summary response for the admin monitoring endpoint.
Separate from the NFM-2222 health event schemas to avoid coupling.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AlertItem(BaseModel):
    """Single aggregated error category in the admin health response."""

    type: str = Field(description="Error category identifier")
    count: int = Field(description="Number of occurrences in the window")
    last_seen: datetime = Field(description="Most recent occurrence timestamp")


class HealthAlertsResponse(BaseModel):
    """Top-level response for GET /api/admin/health/alerts."""

    status: str = Field(description="'healthy' when no alerts, 'degraded' otherwise")
    alerts: list[AlertItem] = Field(
        default_factory=list,
        description="Active alert entries",
    )
