"""RAG metrics computation (NFM-4539 RAG-E / AC-8).

Pure async helpers that compute the AC-8 dashboard payload directly from
the database.  Keeps the FastAPI handler thin and makes the percentile
math testable in isolation.

The percentile is computed in-Python rather than via SQL because:

  * The SQL ``percentile_cont`` aggregate exists in Postgres but not in
    SQLite, and we want the same code path to drive both environments.
  * The sample size is bounded (RAG-B rows in a 7-day window — at most
    ~100k) so the in-process sort + linear-interpolation pass is cheap.

The Tier-1 / Tier-2 partition is defined by the §5 / §3.3 spec:

  Tier-1 (cached / indexed + simple)        → ``was_cached=true``
  Tier-2 (fresh semantic-search answers)    → ``was_cached=false`` AND
                                               ``was_fallback=false``

Below a 5-row sample floor the P95 is reported as ``None`` so the
dashboard renders an honest "insufficient data" badge instead of a
misleading percentile from a tiny sample.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, RagAccessLog, RagIndexAuditLog
from nfm_db.schemas.lightrag import MetricsResponse, TierP95

# ---------------------------------------------------------------------------
# Constants (NFM-4539 §3.3 / AC-5)
# ---------------------------------------------------------------------------

DEFAULT_WINDOW_DAYS = 7
TIER_1_TARGET_MS = 1_000.0  # < 1s
TIER_2_TARGET_MS = 10_000.0  # < 10s (NFM-4525 spec; pre-NFM-4525 was < 30s)
SAMPLE_FLOOR = 5  # below this, P95 is reported as None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def percentile_p95(values: Sequence[float]) -> float | None:
    """Return the 95th percentile using linear interpolation.

    Returns ``None`` for empty input so callers can render an "insufficient
    data" badge instead of a meaningless zero.  The interpolation rule
    matches NumPy's default ``linear`` method, which is the de-facto
    standard for percentile reporting.
    """
    if not values:
        return None
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = 0.95 * (len(sorted_vals) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_vals[int(rank)])
    frac = rank - lower
    return float(sorted_vals[lower] + (sorted_vals[upper] - sorted_vals[lower]) * frac)


def tier_p95(
    sample: Sequence[float],
    *,
    target_ms: float,
    floor: int = SAMPLE_FLOOR,
) -> TierP95:
    """Project a sample + target into the dashboard's :class:`TierP95` shape."""
    sample_size = len(sample)
    if sample_size < floor:
        return TierP95(
            p95_ms=None,
            sample_size=sample_size,
            target_ms=target_ms,
            meets_sla=False,
        )
    p95_s = percentile_p95(sample)
    p95_ms = (p95_s or 0.0) * 1000.0
    return TierP95(
        p95_ms=round(p95_ms, 2),
        sample_size=sample_size,
        target_ms=target_ms,
        meets_sla=p95_ms <= target_ms,
    )


@dataclass(frozen=True)
class WindowBounds:
    """Inclusive lower / exclusive upper bound for the rolling window."""

    start: datetime
    end: datetime


def rolling_window(
    *,
    now: datetime | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
) -> WindowBounds:
    """Return ``[now - days, now)`` in UTC.  Anchored at ``now`` so tests can pin time."""
    anchor = now or datetime.now(UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    start = anchor - timedelta(days=days)
    return WindowBounds(start=start, end=anchor)


# ---------------------------------------------------------------------------
# Database-backed helpers
# ---------------------------------------------------------------------------


async def _count_completed_literature(session: AsyncSession) -> int:
    """``lit_completed_total`` — completed DataSource rows."""
    stmt = select(func.count(DataSource.id)).where(DataSource.parse_status == "completed")
    return int((await session.execute(stmt)).scalar_one())


async def _latest_audit_indexed_count(session: AsyncSession) -> int | None:
    """``lit_indexed_total`` from the most recent ``rag_audit_index_coverage`` run.

    We sum the ``action='noop'`` rows for the latest ``run_date`` — that
    count is the audit's ground truth for "how many of the completed
    literature set were already indexed when the audit last ran".  Live
    LightRAG state may have drifted since, but the daily audit is the
    contract surface the dashboard subscribes to.

    Returns ``None`` when the audit has never run.
    """
    latest_stmt = select(func.max(RagIndexAuditLog.run_date)).where(
        RagIndexAuditLog.routine == "rag_audit_index_coverage"
    )
    latest_date = (await session.execute(latest_stmt)).scalar_one_or_none()
    if latest_date is None:
        return None
    noop_stmt = select(func.count(RagIndexAuditLog.id)).where(
        and_(
            RagIndexAuditLog.routine == "rag_audit_index_coverage",
            RagIndexAuditLog.run_date == latest_date,
            RagIndexAuditLog.action == "noop",
        )
    )
    return int((await session.execute(noop_stmt)).scalar_one())


async def _tier_samples(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> tuple[Sequence[float], Sequence[float]]:
    """Pull the Tier-1 and Tier-2 ``time_total`` samples for the window."""
    tier_1_stmt = select(RagAccessLog.time_total).where(
        and_(
            RagAccessLog.ts >= start,
            RagAccessLog.ts < end,
            RagAccessLog.was_cached.is_(True),
        )
    )
    tier_2_stmt = select(RagAccessLog.time_total).where(
        and_(
            RagAccessLog.ts >= start,
            RagAccessLog.ts < end,
            RagAccessLog.was_cached.is_(False),
            RagAccessLog.was_fallback.is_(False),
        )
    )
    tier_1 = [float(v) for v in (await session.execute(tier_1_stmt)).scalars().all()]
    tier_2 = [float(v) for v in (await session.execute(tier_2_stmt)).scalars().all()]
    return tier_1, tier_2


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def compute_rag_metrics(
    session: AsyncSession,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    tier_2_target_ms: float = TIER_2_TARGET_MS,
    now: datetime | None = None,
) -> MetricsResponse:
    """Build the AC-8 dashboard payload from the live database state."""
    bounds = rolling_window(now=now, days=window_days)
    completed_total = await _count_completed_literature(session)
    indexed_total = await _latest_audit_indexed_count(session)
    tier_1, tier_2 = await _tier_samples(session, start=bounds.start, end=bounds.end)

    diff_count = (
        (completed_total - (indexed_total or 0)) if indexed_total is not None else completed_total
    )

    tier_2_p95_obj = tier_p95(tier_2, target_ms=tier_2_target_ms)
    # NFM-4734 §3 / AC-3: Tier-2 P95 踩线告警。tier_p95 returns
    # ``meets_sla=False`` when ``p95_ms`` is None (under the sample
    # floor), so the breach signal only flips True when we have a
    # statistically meaningful sample that is over budget.
    tier_2_breach = tier_2_p95_obj.p95_ms is not None and not tier_2_p95_obj.meets_sla

    return MetricsResponse(
        lit_completed_total=completed_total,
        lit_indexed_total=indexed_total if indexed_total is not None else 0,
        lit_indexed_source="rag_index_audit_log",
        lit_diff_count=diff_count,
        tier_1_p95=tier_p95(tier_1, target_ms=TIER_1_TARGET_MS),
        tier_2_p95=tier_2_p95_obj,
        window_days=window_days,
        generated_at=bounds.end,
        tier_2_breach=tier_2_breach,
    )


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "SAMPLE_FLOOR",
    "TIER_1_TARGET_MS",
    "TIER_2_TARGET_MS",
    "WindowBounds",
    "compute_rag_metrics",
    "percentile_p95",
    "rolling_window",
    "tier_p95",
]
