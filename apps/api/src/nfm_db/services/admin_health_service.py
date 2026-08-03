"""Service layer for admin health alerts (NFM-2414).

Aggregates the ``health_events`` table populated by
:mod:`nfm_db.services.health_event_emitter` into the structured alert
summary consumed by ``GET /api/v1/admin/health/alerts``.

The previous implementation read from a non-existent
``nfm_db.middleware.error_tracker`` module and always fell through to an
empty :class:`Counter`, so the endpoint was hardwired to ``healthy``. That
defeats the parent story (NFM-2408, "Silent failure detection in CI") —
a monitoring endpoint that reports green by construction is itself a
silent failure. We therefore query the real event store directly.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.health_event import HealthEvent
from nfm_db.schemas.admin_health import AlertItem, HealthAlertsResponse

logger = logging.getLogger(__name__)

#: Look-back window. Matches :data:`nfm_db.services.health_alert_service.DEFAULT_SINCE_HOURS`
#: so the public ``/health/alerts/summary`` endpoint and the admin
#: monitoring endpoint report over the same period.
DEFAULT_LOOKBACK_HOURS = 24


async def get_health_alerts(db: AsyncSession) -> HealthAlertsResponse:
    """Build a structured health alert summary from ``health_events``.

    Aggregates rows in the look-back window by ``event_type`` and emits
    one :class:`AlertItem` per type with the cumulative ``count`` and the
    most recent ``last_seen`` timestamp. When no events fall in the
    window the response is ``status="healthy"`` with an empty alerts
    list; any non-empty aggregation flips it to ``"degraded"``.

    If the underlying aggregation raises, the endpoint flips to
    ``degraded`` with a single synthetic alert so a dashboard sees red
    and an operator investigates — never silently ``healthy``.
    """
    try:
        alerts = await _collect_alerts(db)
    except Exception:
        logger.exception("Failed to aggregate health_events for admin alerts")
        return HealthAlertsResponse(
            status="degraded",
            alerts=[
                AlertItem(
                    type="aggregator_failure",
                    count=1,
                    last_seen=datetime.now(UTC),
                )
            ],
        )

    if not alerts:
        return HealthAlertsResponse(status="healthy", alerts=[])

    return HealthAlertsResponse(status="degraded", alerts=alerts)


async def _collect_alerts(db: AsyncSession) -> list[AlertItem]:
    """Return one :class:`AlertItem` per ``event_type`` within the window."""
    since = datetime.now(UTC) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)

    stmt = (
        select(
            HealthEvent.event_type,
            func.count(HealthEvent.id),
            func.max(HealthEvent.created_at),
        )
        .where(HealthEvent.created_at >= since)
        .group_by(HealthEvent.event_type)
    )

    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return []

    counts_by_type: dict[str, int] = defaultdict(int)
    latest_by_type: dict[str, datetime] = {}
    for event_type, count, last_seen in rows:
        counts_by_type[event_type] = count
        if last_seen is not None:
            latest_by_type[event_type] = last_seen

    alerts: list[AlertItem] = []
    now = datetime.now(UTC)
    for event_type, count in counts_by_type.items():
        alerts.append(
            AlertItem(
                type=event_type,
                count=count,
                last_seen=latest_by_type.get(event_type, now),
            )
        )

    return alerts
