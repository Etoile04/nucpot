"""Service layer for admin health alerts (NFM-2414).

Aggregates application error state into structured alert summaries.
Currently monitors in-process error counters; will be connected
to the health_events table once NFM-2222 merges.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from nfm_db.schemas.admin_health import AlertItem, HealthAlertsResponse


def get_health_alerts() -> HealthAlertsResponse:
    """Build a structured health alert summary from application state.

    Returns a response with status 'healthy' or 'degraded' and a list
    of alert entries with type, count, and last_seen timestamps.
    """
    alerts = _collect_alerts()

    if not alerts:
        return HealthAlertsResponse(status="healthy", alerts=[])

    return HealthAlertsResponse(
        status="degraded",
        alerts=alerts,
    )


def _collect_alerts() -> list[AlertItem]:
    """Collect current alert data from application error counters.

    Placeholder implementation: aggregates error counts from the
    in-process error tracker. Will be extended to query the
    health_events table (NFM-2222) once available on main.
    """
    counters = _read_error_counters()
    if not counters:
        return []

    now = datetime.now(timezone.utc)
    return [
        AlertItem(type=error_type, count=count, last_seen=now)
        for error_type, count in counters.items()
    ]


def _read_error_counters() -> Counter[str]:
    """Read error counts from the application error tracking state.

    In production, this would aggregate from:
    - Application error logs (last N hours)
    - CI failure counters
    - Health event persistence (NFM-2222 health_events table)

    For now returns a counter from the in-process error tracker
    if available.
    """
    try:
        from nfm_db.middleware.error_tracker import get_error_counts

        raw = get_error_counts()
        return Counter(raw) if raw else Counter()
    except ImportError:
        return Counter()
