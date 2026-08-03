"""Health alert response schemas (NFM-2222)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AlertItem(BaseModel):
    """Single health event as returned by the alerts endpoint."""

    id: uuid.UUID
    event_type: str
    severity: str
    source_service: str
    context: dict[str, Any]
    created_at: datetime


class AlertFilterMeta(BaseModel):
    """Metadata about which filters were applied."""

    event_type: str | None = None
    severity: str | None = None
    source_service: str | None = None
    since: str | None = None


class AlertsMeta(BaseModel):
    """Pagination and filter metadata for the alerts response."""

    total: int
    since: str
    limit: int
    filters: AlertFilterMeta


class AlertsResponse(BaseModel):
    """Top-level response body for GET /api/v1/health/alerts."""

    alerts: list[AlertItem]
    meta: AlertsMeta


class SummaryPeriod(BaseModel):
    """Time window covered by the summary."""

    since: str
    until: str


class SummaryData(BaseModel):
    """Aggregated counts for the summary endpoint."""

    total_events: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    period: SummaryPeriod


class SummaryResponse(BaseModel):
    """Top-level response body for GET /api/v1/health/alerts/summary."""

    summary: SummaryData


class ActiveErrorsData(BaseModel):
    """Count of error-severity health events with categorical breakdowns."""

    count: int
    #: axis (``event_type`` / ``severity``) -> value -> error/critical count.
    categories: dict[str, dict[str, int]]
    since: str
    until: str


class AdminAlertsResponse(BaseModel):
    """Top-level response body for GET /api/admin/health/alerts (NFM-2440).

    ``status`` reports whether the *query* succeeded, not whether the system
    is healthy. It is always ``"ok"`` on a 200; a non-zero
    ``active_errors.count`` does not change it. Errors are reported in the
    body so monitoring clients can distinguish "the health API is down"
    (non-200) from "the health API works and there are errors" (200).
    """

    status: str
    active_errors: ActiveErrorsData
