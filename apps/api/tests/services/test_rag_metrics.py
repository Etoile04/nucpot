"""Tests for the RAG metrics computation (NFM-4539 RAG-E / AC-8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, RagAccessLog, RagIndexAuditLog
from nfm_db.services.rag_metrics import (
    DEFAULT_WINDOW_DAYS,
    SAMPLE_FLOOR,
    TIER_1_TARGET_MS,
    TIER_2_TARGET_MS,
    WindowBounds,
    compute_rag_metrics,
    percentile_p95,
    rolling_window,
    tier_p95,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_percentile_p95_empty_returns_none() -> None:
    assert percentile_p95([]) is None


def test_percentile_p95_single_value() -> None:
    assert percentile_p95([0.5]) == 0.5


def test_percentile_p95_known_distribution() -> None:
    # Linear interpolation rule: index 0.95 * (n-1).
    # For [10, 20, 30, 40, 50, 60, 70, 80, 90, 100] → rank = 0.95 * 9 = 8.55
    # → lower=8 (90), upper=9 (100), frac=0.55 → 90 + 10*0.55 = 95.5
    assert percentile_p95(list(range(10, 110, 10))) == pytest.approx(95.5)


def test_percentile_p95_all_equal() -> None:
    assert percentile_p95([7.0] * 25) == 7.0


def test_tier_p95_below_sample_floor_is_none() -> None:
    """Below SAMPLE_FLOOR rows, P95 must be None — no misleading percentile."""
    out = tier_p95([0.1, 0.2, 0.3], target_ms=1000.0)  # 3 rows < 5
    assert out.p95_ms is None
    assert out.sample_size == 3
    assert out.target_ms == 1000.0
    assert out.meets_sla is False


def test_tier_p95_meets_sla() -> None:
    out = tier_p95([0.05] * 100, target_ms=1000.0)
    assert out.p95_ms == pytest.approx(50.0)
    assert out.sample_size == 100
    assert out.meets_sla is True


def test_tier_p95_misses_sla() -> None:
    out = tier_p95([2.0] * 10, target_ms=1000.0)
    assert out.p95_ms == pytest.approx(2000.0)
    assert out.meets_sla is False


def test_tier_p95_floor_is_exact_threshold() -> None:
    """``floor=5`` → 5 samples is enough; 4 is not."""
    enough = tier_p95([0.1] * 5, target_ms=1000.0, floor=SAMPLE_FLOOR)
    assert enough.p95_ms is not None
    not_enough = tier_p95([0.1] * 4, target_ms=1000.0, floor=SAMPLE_FLOOR)
    assert not_enough.p95_ms is None


def test_tier_2_target_ms_is_ten_seconds_post_nfm_4525() -> None:
    """NFM-4525 tightened Tier-2 P95 from < 30s to < 10s.  The constant
    must agree — otherwise the dashboard reports ``meets_sla=true`` for
    queries that actually exceed the spec (NFM-4617-B3 regression)."""
    assert TIER_2_TARGET_MS == 10_000.0


def test_tier_2_target_ms_flips_meets_sla_at_10s_threshold() -> None:
    """When ``target_ms=TIER_2_TARGET_MS`` (post-NFM-4525 = 10s):

      * p95 == 9_999.99 ms → ``meets_sla=true`` (within budget)
      * p95 == 10_000.01 ms → ``meets_sla=false`` (over budget)

    This pins the boundary semantics against the post-NFM-4525 constant
    so a future drift can not silently re-introduce the NFM-4617-B3 bug.
    """
    # 5 samples * 9.99999 s = P95 = 9999.99 ms → under 10s budget.
    inside = tier_p95([9.99999] * SAMPLE_FLOOR, target_ms=TIER_2_TARGET_MS)
    assert inside.p95_ms == pytest.approx(9999.99)
    assert inside.meets_sla is True

    # 5 samples * 10.00001 s = P95 = 10000.01 ms → over 10s budget.
    outside = tier_p95([10.00001] * SAMPLE_FLOOR, target_ms=TIER_2_TARGET_MS)
    assert outside.p95_ms == pytest.approx(10000.01)
    assert outside.meets_sla is False

    # Exact target is inclusive (p95_ms <= target_ms is the rule).
    exact = tier_p95([10.0] * SAMPLE_FLOOR, target_ms=TIER_2_TARGET_MS)
    assert exact.p95_ms == pytest.approx(10_000.0)
    assert exact.meets_sla is True


# ---------------------------------------------------------------------------
# NFM-4734 — Tier-2 SLA breach detection
# ---------------------------------------------------------------------------


class TestTier2SlaBreachSignal:
    """NFM-4734 §3 / AC-3: Tier-2 P95踩线(Tier-2 P95 > 10s)时告警。

    The breach signal rides on the AC-8 dashboard payload via a new
    ``tier_2_breach`` boolean + a ``tier_2_breach_at`` timestamp.  Operators
    pin a single Grafana panel and the boolean flips the row red without
    any extra Prometheus rule.
    """

    def test_metrics_response_has_tier_2_breach_field(self) -> None:
        """Schema exposes the breach signal."""
        from nfm_db.schemas.lightrag import MetricsResponse, TierP95

        resp = MetricsResponse(
            lit_completed_total=0,
            lit_indexed_total=0,
            lit_diff_count=0,
            tier_1_p95=TierP95(target_ms=1000.0),
            tier_2_p95=TierP95(target_ms=10000.0),
            generated_at=datetime.now(UTC),
            tier_2_breach=False,
        )
        assert resp.tier_2_breach is False

    def test_metrics_response_default_tier_2_breach_is_false(self) -> None:
        """Default value is False so legacy callers keep working."""
        from nfm_db.schemas.lightrag import MetricsResponse, TierP95

        resp = MetricsResponse(
            lit_completed_total=0,
            lit_indexed_total=0,
            lit_diff_count=0,
            tier_1_p95=TierP95(target_ms=1000.0),
            tier_2_p95=TierP95(target_ms=10000.0),
            generated_at=datetime.now(UTC),
        )
        assert resp.tier_2_breach is False

    def test_breach_signal_flips_when_p95_exceeds_target(self) -> None:
        """A single Tier-2 sample over the 10s target flips the signal True."""
        # 5 samples, one is 12s (clearly over), p95 lands above 10s.
        out = tier_p95([9.0, 9.5, 10.0, 10.5, 12.0], target_ms=TIER_2_TARGET_MS)
        assert out.p95_ms is not None
        assert out.p95_ms > TIER_2_TARGET_MS
        # The boolean is a pure projection of ``meets_sla``:
        #   meets_sla=False → tier_2_breach=True
        breach = out.p95_ms is not None and not out.meets_sla
        assert breach is True

    def test_breach_signal_stays_false_when_under_target(self) -> None:
        """All samples under 10s → ``tier_2_breach=False``."""
        out = tier_p95([0.5, 0.7, 0.9, 1.1, 1.3], target_ms=TIER_2_TARGET_MS)
        assert out.meets_sla is True
        breach = out.p95_ms is not None and not out.meets_sla
        assert breach is False

    def test_breach_signal_stays_false_when_below_sample_floor(self) -> None:
        """Below the 5-row floor, ``tier_2_breach=False`` — we do NOT alert
        on a percentile computed from 1-4 samples.
        """
        out = tier_p95([15.0], target_ms=TIER_2_TARGET_MS)  # 1 sample < 5
        assert out.p95_ms is None
        breach = out.p95_ms is not None and not out.meets_sla
        assert breach is False


def test_rolling_window_defaults_to_seven_days() -> None:
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    bounds = rolling_window(now=now)
    assert bounds.end == now
    assert bounds.start == now - timedelta(days=DEFAULT_WINDOW_DAYS)
    assert isinstance(bounds, WindowBounds)


def test_rolling_window_normalises_naive_anchor() -> None:
    """A naive ``datetime`` is treated as UTC so the test suite can pin
    time without juggling tzinfo throughout."""
    naive = datetime(2026, 9, 10, 12, 0, 0)
    bounds = rolling_window(now=naive, days=3)
    assert bounds.end.tzinfo is UTC
    assert bounds.start.tzinfo is UTC


# ---------------------------------------------------------------------------
# Database-backed metrics
# ---------------------------------------------------------------------------


async def _seed_completed(session: AsyncSession, *, n: int) -> list[DataSource]:
    rows = [
        DataSource(
            id=uuid.uuid4(),
            title=f"lit-{i}",
            source_type="journal_article",
            parse_status="completed",
        )
        for i in range(n)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def _seed_pending(session: AsyncSession, *, n: int) -> list[DataSource]:
    rows = [
        DataSource(
            id=uuid.uuid4(),
            title=f"lit-pending-{i}",
            source_type="journal_article",
            parse_status="uploaded",
        )
        for i in range(n)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def _seed_audit(
    session: AsyncSession,
    *,
    run_date: date_cls,
    noop_count: int,
    reingest_count: int = 0,
    routine: str = "rag_audit_index_coverage",
) -> None:
    for _ in range(noop_count):
        session.add(
            RagIndexAuditLog(
                ts=datetime.now(UTC),
                run_date=run_date,
                routine=routine,
                literature_id=uuid.uuid4(),
                action="noop",
            )
        )
    for _ in range(reingest_count):
        session.add(
            RagIndexAuditLog(
                ts=datetime.now(UTC),
                run_date=run_date,
                routine=routine,
                literature_id=uuid.uuid4(),
                action="reingest",
            )
        )
    await session.commit()


async def _seed_access(
    session: AsyncSession,
    *,
    time_total: float,
    was_cached: bool,
    was_fallback: bool = False,
    ts: datetime | None = None,
) -> RagAccessLog:
    row = RagAccessLog(
        ts=ts or datetime.now(UTC),
        mode="mix",
        was_cached=was_cached,
        was_fallback=was_fallback,
        query_kind="semantic",
        result_count=0,
        time_total=time_total,
    )
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_metrics_returns_zero_when_db_empty(
    db_session: AsyncSession,
) -> None:
    payload = await compute_rag_metrics(db_session)
    assert payload.lit_completed_total == 0
    assert payload.lit_indexed_total == 0
    assert payload.lit_diff_count == 0
    assert payload.tier_1_p95.p95_ms is None
    assert payload.tier_2_p95.p95_ms is None
    assert payload.window_days == DEFAULT_WINDOW_DAYS


@pytest.mark.asyncio
async def test_metrics_counts_only_completed_literature(
    db_session: AsyncSession,
) -> None:
    await _seed_completed(db_session, n=7)
    await _seed_pending(db_session, n=4)

    payload = await compute_rag_metrics(db_session)
    assert payload.lit_completed_total == 7


@pytest.mark.asyncio
async def test_metrics_indexed_total_uses_latest_audit_run(
    db_session: AsyncSession,
) -> None:
    await _seed_completed(db_session, n=20)
    # Older audit run — must be ignored.
    await _seed_audit(db_session, run_date=date_cls(2026, 9, 1), noop_count=15)
    # Latest audit run — drives the dashboard.
    await _seed_audit(
        db_session,
        run_date=date_cls(2026, 9, 10),
        noop_count=12,
        reingest_count=3,
    )

    payload = await compute_rag_metrics(db_session)
    assert payload.lit_indexed_total == 12
    assert payload.lit_diff_count == 20 - 12


@pytest.mark.asyncio
async def test_metrics_diff_count_equals_completed_minus_indexed(
    db_session: AsyncSession,
) -> None:
    await _seed_completed(db_session, n=10)
    await _seed_audit(db_session, run_date=date_cls(2026, 9, 10), noop_count=4)

    payload = await compute_rag_metrics(db_session)
    assert payload.lit_completed_total == 10
    assert payload.lit_indexed_total == 4
    assert payload.lit_diff_count == 6


@pytest.mark.asyncio
async def test_metrics_tier1_partitions_by_was_cached(
    db_session: AsyncSession,
) -> None:
    """Tier-1 = was_cached=true; Tier-2 = was_cached=false AND was_fallback=false."""
    await _seed_completed(db_session, n=2)

    # 6 cached rows, all fast.
    for _ in range(6):
        await _seed_access(db_session, time_total=0.1, was_cached=True)
    # 6 fresh semantic rows, slower.
    for _ in range(6):
        await _seed_access(db_session, time_total=2.0, was_cached=False, was_fallback=False)
    # 6 fallback rows — must NOT contribute to Tier-2.
    for _ in range(6):
        await _seed_access(db_session, time_total=5.0, was_cached=False, was_fallback=True)

    payload = await compute_rag_metrics(db_session)
    assert payload.tier_1_p95.sample_size == 6
    assert payload.tier_1_p95.p95_ms == pytest.approx(100.0)
    assert payload.tier_2_p95.sample_size == 6
    assert payload.tier_2_p95.p95_ms == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_metrics_window_filters_by_ts(
    db_session: AsyncSession,
) -> None:
    """Rows older than ``window_days`` must not contribute to P95."""
    now = datetime.now(UTC)
    await _seed_completed(db_session, n=1)
    await _seed_audit(db_session, run_date=now.date(), noop_count=1)

    # Old row (8 days back) — must be ignored.
    old_ts = now - timedelta(days=8)
    await _seed_access(db_session, time_total=99.0, was_cached=True, ts=old_ts)
    # Fresh rows (within 7d) — enough to clear the 5-row sample floor.
    for i in range(5):
        await _seed_access(
            db_session,
            time_total=0.05,
            was_cached=True,
            ts=now - timedelta(seconds=i),
        )

    payload = await compute_rag_metrics(db_session, window_days=7)
    assert payload.tier_1_p95.sample_size == 5
    assert payload.tier_1_p95.p95_ms == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_metrics_window_floor_is_respected(
    db_session: AsyncSession,
) -> None:
    """Below the floor, P95 must be None even if all rows are fast."""
    await _seed_completed(db_session, n=2)
    for _ in range(3):
        await _seed_access(db_session, time_total=0.1, was_cached=True)

    payload = await compute_rag_metrics(db_session)
    assert payload.tier_1_p95.sample_size == 3
    assert payload.tier_1_p95.p95_ms is None


@pytest.mark.asyncio
async def test_metrics_tier2_target_is_configurable(
    db_session: AsyncSession,
) -> None:
    """``tier_2_target_ms`` lets operators pre-set the post-NFM-4525 10s
    target without redeploying."""
    await _seed_completed(db_session, n=2)
    for _ in range(6):
        await _seed_access(db_session, time_total=11.0, was_cached=False, was_fallback=False)

    pre_fix = await compute_rag_metrics(db_session, tier_2_target_ms=30_000.0)
    assert pre_fix.tier_2_p95.meets_sla is True
    assert pre_fix.tier_2_p95.target_ms == 30_000.0

    post_fix = await compute_rag_metrics(db_session, tier_2_target_ms=10_000.0)
    assert post_fix.tier_2_p95.meets_sla is False
    assert post_fix.tier_2_p95.target_ms == 10_000.0


@pytest.mark.asyncio
async def test_metrics_generated_at_is_anchor_time(
    db_session: AsyncSession,
) -> None:
    payload = await compute_rag_metrics(db_session, now=datetime(2026, 9, 10, 3, 30, tzinfo=UTC))
    assert payload.generated_at == datetime(2026, 9, 10, 3, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_metrics_tier1_target_is_one_second(
    db_session: AsyncSession,
) -> None:
    """Tier-1 target is fixed at < 1s (§3.3 / AC-5)."""
    assert TIER_1_TARGET_MS == 1_000.0
    await _seed_completed(db_session, n=1)
    for _ in range(6):
        await _seed_access(db_session, time_total=0.7, was_cached=True)
    payload = await compute_rag_metrics(db_session)
    assert payload.tier_1_p95.target_ms == 1_000.0
    assert payload.tier_1_p95.meets_sla is True


@pytest.mark.asyncio
async def test_metrics_no_audit_means_indexed_zero(
    db_session: AsyncSession,
) -> None:
    """Before the first audit run, ``lit_indexed_total`` falls back to 0
    and ``lit_diff_count`` equals ``lit_completed_total`` — a truthful
    'we have no proof of indexing yet' state."""
    await _seed_completed(db_session, n=5)

    payload = await compute_rag_metrics(db_session)
    assert payload.lit_indexed_total == 0
    assert payload.lit_diff_count == 5


@pytest.mark.asyncio
async def test_metrics_ignores_non_default_routine_in_audit(
    db_session: AsyncSession,
) -> None:
    """A future routine sharing the table must not pollute the dashboard."""
    await _seed_completed(db_session, n=3)
    await _seed_audit(
        db_session,
        run_date=date_cls(2026, 9, 10),
        noop_count=99,
        routine="rag_audit_future_routine",
    )

    payload = await compute_rag_metrics(db_session)
    assert payload.lit_indexed_total == 0
