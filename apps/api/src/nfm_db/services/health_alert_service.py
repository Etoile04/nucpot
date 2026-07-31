"""Service layer for health alert queries (NFM-2222).

Reads from the ``health_events`` table populated by
:mod:`nfm_db.services.health_event_emitter`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.health_event import HealthEvent
from nfm_db.schemas.health import (
    AlertFilterMeta,
    AlertsMeta,
    AlertsResponse,
    SummaryData,
    SummaryPeriod,
    SummaryResponse,
)

logger = logging.getLogger(__name__)

#: Default look-back window for the ``since`` filter.
DEFAULT_SINCE_HOURS = 24

#: Maximum number of events a single response can carry.
MAX_LIMIT = 500

#: Default page size when no limit is supplied.
DEFAULT_LIMIT = 50


def _default_since() -> datetime:
    """Return the default ``since`` timestamp (24 hours ago, UTC)."""
    return datetime.now(UTC) - timedelta(hours=DEFAULT_SINCE_HOURS)


def _cap_limit(limit: int) -> int:
    """Clamp *limit* to the ``[1, MAX_LIMIT]`` range."""
    return max(1, min(limit, MAX_LIMIT))


def _build_filters(
    *,
    event_type: str | None = None,
    severity: str | None = None,
    source_service: str | None = None,
    since: datetime | None,
) -> list[Any]:
    """Return a list of SQLAlchemy filter expressions."""
    filters: list[Any] = []
    if event_type is not None:
        filters.append(HealthEvent.event_type == event_type)
    if severity is not None:
        filters.append(HealthEvent.severity == severity)
    if source_service is not None:
        filters.append(HealthEvent.source_service == source_service)
    if since is not None:
        filters.append(HealthEvent.created_at >= since)
    return filters


async def get_alerts(
    db: AsyncSession,
    *,
    event_type: str | None = None,
    severity: str | None = None,
    source_service: str | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> AlertsResponse:
    """Query health events with optional filters and a hard cap on rows.

    Args:
        db: Async database session.
        event_type: Filter by ``health_events.event_type``.
        severity: Filter by ``health_events.severity``.
        source_service: Filter by ``health_events.source_service``.
        since: Lower bound for ``created_at``. Defaults to 24 h ago.
        limit: Max rows to return. Clamped to ``[1, 500]``.

    Returns:
        An :class:`AlertsResponse` with ``alerts`` and ``meta``.
    """
    effective_since = since if since is not None else _default_since()
    capped_limit = _cap_limit(limit)
    filters = _build_filters(
        event_type=event_type,
        severity=severity,
        source_service=source_service,
        since=effective_since,
    )

    # Count query (before limit).
    count_stmt = select(func.count()).select_from(HealthEvent)
    for f in filters:
        count_stmt = count_stmt.where(f)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Data query, newest first, limited.
    data_stmt = (
        select(HealthEvent)
        .where(*filters)
        .order_by(HealthEvent.created_at.desc())
        .limit(capped_limit)
    )
    result = await db.execute(data_stmt)
    rows = result.scalars().all()

    alerts = [
        {
            "id": row.id,
            "event_type": row.event_type,
            "severity": row.severity,
            "source_service": row.source_service,
            "context": row.context or {},
            "created_at": row.created_at,
        }
        for row in rows
    ]

    return AlertsResponse(
        alerts=alerts,
        meta=AlertsMeta(
            total=total,
            since=effective_since.isoformat(),
            limit=capped_limit,
            filters=AlertFilterMeta(
                event_type=event_type,
                severity=severity,
                source_service=source_service,
                since=effective_since.isoformat(),
            ),
        ),
    )


async def get_alerts_summary(
    db: AsyncSession,
    *,
    since: datetime | None = None,
) -> SummaryResponse:
    """Return aggregated counts by ``event_type`` and ``severity``.

    Args:
        db: Async database session.
        since: Lower bound for ``created_at``. Defaults to 24 h ago.

    Returns:
        A :class:`SummaryResponse` with ``total_events``, ``by_type``,
        ``by_severity``, and ``period``.
    """
    effective_since = since if since is not None else _default_since()
    filters = _build_filters(since=effective_since)

    stmt = select(HealthEvent).where(*filters)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    by_type: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    for row in rows:
        by_type[row.event_type] += 1
        by_severity[row.severity] += 1

    return SummaryResponse(
        summary=SummaryData(
            total_events=len(rows),
            by_type=dict(by_type),
            by_severity=dict(by_severity),
            period=SummaryPeriod(
                since=effective_since.isoformat(),
                until=datetime.now(UTC).isoformat(),
            ),
        ),
    )
