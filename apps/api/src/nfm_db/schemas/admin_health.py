"""Admin health alerts response schemas (NFM-2416).

Structured error summary for admin monitoring with healthy/degraded status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AdminAlertItem(BaseModel):
    """Aggregated alert for a single event type."""

    type: str
    count: int
    last_seen: datetime


class AdminHealthAlertsResponse(BaseModel):
    """Response body for GET /api/admin/health/alerts."""

    status: Literal["healthy", "degraded"]
    alerts: list[AdminAlertItem]
