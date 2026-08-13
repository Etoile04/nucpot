"""Tests for the GFS (Grandfather-Father-Son) tier classification engine.

NFM-3050 / NFM-3024-B — 3-tier backup scheduler with tier tagging.

All tests use synthetic snapshots with deterministic mtimes (no filesystem
access). The core invariant: given (now, mtimes, tier_config), the output
tiers are deterministic and idempotent across scheduler restarts.

TDD RED phase — these tests define the acceptance criteria before any
implementation exists.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from nfm_db.services.backup.gfs_tier import (
    GFSTierConfig,
    classify_tiers,
    compute_retention_plan,
    default_gfs_config,
)
from nfm_db.services.backup.models import BackupSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap(mtime_offset_seconds: float, size_bytes: int = 300_000_000) -> BackupSnapshot:
    """Create a snapshot with mtime relative to *now*.

    Positive offset = older (in the past), negative = future (shouldn't
    happen in practice but the engine must handle gracefully).
    """
    now = time.time()
    return BackupSnapshot(
        filename=f"nucpot-{now - mtime_offset_seconds:.0f}.sql.gz",
        mtime=now - mtime_offset_seconds,
        size_bytes=size_bytes,
        tier=None,
    )


def _snaps_from_offsets(*offsets_seconds: float) -> list[BackupSnapshot]:
    """Convenience: build a list of snapshots from relative offsets."""
    return [_snap(off) for off in offsets_seconds]


# ---------------------------------------------------------------------------
# GFSTierConfig
# ---------------------------------------------------------------------------

class TestGFSTierConfig:
    """Tests for the tier configuration dataclass."""

    def test_default_config_matches_spec(self) -> None:
        """Default config must match the NFM-3024 spec table."""
        cfg = default_gfs_config()
        assert cfg.hourly_interval_minutes == 60
        assert cfg.hourly_count == 24
        assert cfg.daily_interval_minutes == 1440  # 24h
        assert cfg.daily_count == 7
        assert cfg.weekly_interval_minutes == 10080  # 168h / 7d
        assert cfg.weekly_count == 4

    def test_max_age_hours(self) -> None:
        """max_age for each tier equals interval_minutes * count."""
        cfg = default_gfs_config()
        assert cfg.hourly_max_age_hours == pytest.approx(24.0)
        assert cfg.daily_max_age_hours == pytest.approx(168.0)  # 7d
        assert cfg.weekly_max_age_hours == pytest.approx(672.0)  # 28d = 4w

    def test_custom_config_overrides(self) -> None:
        """Custom config values are respected."""
        cfg = GFSTierConfig(
            hourly_interval_minutes=30,
            hourly_count=48,
            daily_interval_minutes=1440,
            daily_count=14,
            weekly_interval_minutes=10080,
            weekly_count=8,
        )
        assert cfg.hourly_max_age_hours == pytest.approx(24.0)
        assert cfg.daily_max_age_hours == pytest.approx(336.0)
        assert cfg.weekly_max_age_hours == pytest.approx(1344.0)


# ---------------------------------------------------------------------------
# classify_tiers — AC4: synthetic file list, all tiers correctly classified
# ---------------------------------------------------------------------------

class TestClassifyTiers:
    """Core tier classification algorithm tests (AC4)."""

    def test_single_fresh_snapshot_is_hourly(self) -> None:
        """A brand-new snapshot (age 0) should be classified as hourly."""
        now = time.time()
        snap = BackupSnapshot(
            filename="fresh.sql.gz",
            mtime=now,
            size_bytes=300_000_000,
            tier=None,
        )
        result = classify_tiers([snap], now=now)
        assert len(result) == 1
        assert result[0].tier == "hourly"

    def test_empty_list_returns_empty(self) -> None:
        """No snapshots -> no output."""
        assert classify_tiers([], now=time.time()) == []

    def test_hourly_tier_fills_to_cap(self) -> None:
        """24 snapshots within 24h fill all 24 hourly slots."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"hourly-{i}.sql.gz",
                mtime=now - (i * 3600),  # every hour
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(24)
        ]
        result = classify_tiers(snaps, now=now)
        hourly = [s for s in result if s.tier == "hourly"]
        assert len(hourly) == 24

    def test_hourly_overflow_promotes_to_daily(self) -> None:
        """25 snapshots within 24h: 24 hourly + 1 daily."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"overflow-{i}.sql.gz",
                mtime=now - (i * 3500),  # slightly less than 1h apart
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(25)
        ]
        result = classify_tiers(snaps, now=now)
        hourly = [s for s in result if s.tier == "hourly"]
        daily = [s for s in result if s.tier == "daily"]
        assert len(hourly) == 24
        assert len(daily) == 1

    def test_daily_tier_fills_to_cap(self) -> None:
        """7 snapshots within the daily window (but outside hourly) fill
        all 7 daily slots."""
        now = time.time()
        # 24h to 162h — all >= 24h (outside hourly) and < 168h (daily max).
        # 23h spacing keeps the 7th snapshot at 162h < 168h.
        snaps = [
            BackupSnapshot(
                filename=f"daily-{i}.sql.gz",
                mtime=now - (24 * 3600 + i * 23 * 3600),
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(7)  # 24h, 47h, 70h, 93h, 116h, 139h, 162h
        ]
        result = classify_tiers(snaps, now=now)
        daily = [s for s in result if s.tier == "daily"]
        assert len(daily) == 7

    def test_weekly_tier_fills_to_cap(self) -> None:
        """After daily slots are full, 4 snapshots in the weekly window
        fill all 4 weekly slots."""
        now = time.time()
        snaps = []
        # Fill all 7 daily slots first (outside hourly window).
        for i in range(7):
            snaps.append(BackupSnapshot(
                filename=f"daily-{i}.sql.gz",
                mtime=now - (25 * 3600 + i * 24 * 3600),
                size_bytes=300_000_000,
                tier=None,
            ))
        # 4 snapshots in the weekly-only window (beyond daily max 168h,
        # within weekly max 672h).  Spaced widely to stay < 672h.
        weekly_offsets = [169, 337, 505, 671]  # hours
        for i, offset_h in enumerate(weekly_offsets):
            snaps.append(BackupSnapshot(
                filename=f"weekly-{i}.sql.gz",
                mtime=now - (offset_h * 3600),
                size_bytes=300_000_000,
                tier=None,
            ))
        result = classify_tiers(snaps, now=now)
        weekly = [s for s in result if s.tier == "weekly"]
        assert len(weekly) == 4

    def test_beyond_weekly_cap_is_prune(self) -> None:
        """A 5th weekly snapshot (still within 4w) gets prune."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"weekly-{i}.sql.gz",
                mtime=now - (i * 7 * 86400),
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(5)
        ]
        result = classify_tiers(snaps, now=now)
        prune = [s for s in result if s.tier == "prune"]
        assert len(prune) == 1

    def test_beyond_max_age_is_prune(self) -> None:
        """Snapshot older than 4 weeks (28 days) is always prune."""
        now = time.time()
        snap = BackupSnapshot(
            filename="ancient.sql.gz",
            mtime=now - (30 * 86400),  # 30 days old
            size_bytes=300_000_000,
            tier=None,
        )
        result = classify_tiers([snap], now=now)
        assert result[0].tier == "prune"

    def test_fully_settled_schedule_totals(self) -> None:
        """AC1: 24 hourly + 7 daily + 4 weekly = 35 snapshots kept.

        Generate snapshots from 0 to 35 hours apart. With default
        config the first 24 are hourly, the next 7 are daily, the
        last 4 are weekly.
        """
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"settled-{i}.sql.gz",
                mtime=now - (i * 3600),  # every hour for 35 hours
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(35)
        ]
        result = classify_tiers(snaps, now=now)
        hourly = [s for s in result if s.tier == "hourly"]
        daily = [s for s in result if s.tier == "daily"]
        weekly = [s for s in result if s.tier == "weekly"]
        assert len(hourly) == 24
        assert len(daily) == 7
        assert len(weekly) == 4

    def test_fully_settled_schedule_size_under_12gib(self) -> None:
        """AC1: With all tiers filled, total on-disk size <= 12 GiB.

        Each snapshot ~ 300 MB. 24+7+4 = 35 x 300 MB = 10.5 GiB < 12 GiB.
        """
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"cap-{i}.sql.gz",
                mtime=now - (i * 3600),
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(35)
        ]
        result = classify_tiers(snaps, now=now)
        total_bytes = sum(s.size_bytes for s in result if s.tier != "prune")
        gib = total_bytes / (1024 ** 3)
        assert gib <= 12.0

    def test_newest_snapshots_get_hourly_priority(self) -> None:
        """Newer snapshots should be hourly before older ones."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename="newer.sql.gz",
                mtime=now - 60,
                size_bytes=300_000_000,
                tier=None,
            ),
            BackupSnapshot(
                filename="older.sql.gz",
                mtime=now - 7200,
                size_bytes=300_000_000,
                tier=None,
            ),
        ]
        result = classify_tiers(snaps, now=now)
        tiers = {s.filename: s.tier for s in result}
        assert tiers["newer.sql.gz"] == "hourly"
        assert tiers["older.sql.gz"] == "hourly"  # both fit in hourly

    def test_newer_daily_replaces_older(self) -> None:
        """When daily slots are full, older snapshots beyond the daily
        age window spill into the next available tier (weekly)."""
        now = time.time()
        snaps = []
        # 8 snapshots, 23h apart, starting at 24h (outside hourly).
        # First 7 (24h–162h) fill daily; 8th (185h) exceeds daily max
        # and falls into weekly.
        for i in range(8):
            snaps.append(BackupSnapshot(
                filename=f"daily-{i}.sql.gz",
                mtime=now - (24 * 3600 + i * 23 * 3600),
                size_bytes=300_000_000,
                tier=None,
            ))
        result = classify_tiers(snaps, now=now)
        daily = [s for s in result if s.tier == "daily"]
        weekly = [s for s in result if s.tier == "weekly"]
        assert len(daily) == 7
        assert len(weekly) == 1


# ---------------------------------------------------------------------------
# Idempotency - AC3: restart mid-week does not duplicate or lose tiers
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Tier classification must produce the same result when re-run."""

    def test_idempotent_single_call(self) -> None:
        """Calling classify_tiers twice with same inputs yields same output."""
        now = time.time()
        snaps = _snaps_from_offsets(0, 3600, 7200, 86400, 172800)
        result1 = classify_tiers(snaps, now=now)
        result2 = classify_tiers(snaps, now=now)
        tiers1 = [(s.filename, s.tier) for s in result1]
        tiers2 = [(s.filename, s.tier) for s in result2]
        assert tiers1 == tiers2

    def test_idempotent_after_time_advance(self) -> None:
        """Reclassifying 1 hour later with updated `now` shifts tiers
        correctly but remains deterministic."""
        now1 = time.time()
        snaps = _snaps_from_offsets(0, 3600, 86400, 172800)
        result1 = classify_tiers(snaps, now=now1)

        now2 = now1 + 3600  # 1 hour later
        result2 = classify_tiers(snaps, now=now2)

        # Running again with same now2 must reproduce
        result3 = classify_tiers(snaps, now=now2)
        tiers2 = {s.filename: s.tier for s in result2}
        tiers3 = {s.filename: s.tier for s in result3}
        assert tiers2 == tiers3

    def test_restart_mid_week_no_loss(self) -> None:
        """AC3: Restarting mid-week preserves existing tier assignments.

        Simulate: scheduler runs, then "restarts" (re-runs classify_tiers
        with the same snapshots). No snapshot should flip from kept->prune
        that wasn't already prune.
        """
        now = time.time()
        snaps = []
        for i in range(20):
            snaps.append(_snap(i * 3600))       # 0-20h: hourly window
        for i in range(5):
            snaps.append(_snap((25 + i) * 3600))  # 25-29h: daily window
        for i in range(2):
            snaps.append(_snap((8 * 24 + i * 7 * 24) * 3600))  # weekly window

        result1 = classify_tiers(snaps, now=now)
        result2 = classify_tiers(snaps, now=now)

        for s1, s2 in zip(result1, result2):
            assert s1.tier == s2.tier, (
                f"Snapshot {s1.filename} changed tier on restart: "
                f"{s1.tier} -> {s2.tier}"
            )

    def test_no_duplicate_tiers_on_restart(self) -> None:
        """Re-running classification should not cause a snapshot to occupy
        two different tier slots simultaneously."""
        now = time.time()
        # Use offsets spanning all three tiers to ensure each key exists.
        snaps = _snaps_from_offsets(
            0, 3600, 7200,          # hourly window
            48 * 3600, 96 * 3600,   # daily window (outside hourly)
            200 * 3600,             # weekly window (outside daily)
        )
        result1 = classify_tiers(snaps, now=now)
        result2 = classify_tiers(snaps, now=now)

        for result in [result1, result2]:
            counts: dict[str | None, int] = {}
            for s in result:
                t = s.tier
                counts[t] = counts.get(t, 0) + 1
            assert counts.get("hourly", 0) <= 24
            assert counts.get("daily", 0) <= 7
            assert counts.get("weekly", 0) <= 4


# ---------------------------------------------------------------------------
# compute_retention_plan - which snapshots to keep vs delete
# ---------------------------------------------------------------------------

class TestComputeRetentionPlan:
    """Integration test: classify tiers then determine keep/prune lists."""

    def test_prune_list_excludes_kept_snapshots(self) -> None:
        """Snapshots classified as hourly/daily/weekly are in keep list."""
        now = time.time()
        snaps = _snaps_from_offsets(0, 3600, 86400, 604800)
        result = compute_retention_plan(snaps, now=now)
        assert all(s.tier != "prune" for s in result.keep)
        assert all(s.tier == "prune" for s in result.prune)

    def test_overfull_schedule_prunes_oldest(self) -> None:
        """When all tiers are full, excess goes to prune list."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"over-{i}.sql.gz",
                mtime=now - (i * 3600),
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(40)  # more than 24+7+4=35
        ]
        result = compute_retention_plan(snaps, now=now)
        kept_count = len(result.keep)
        pruned_count = len(result.prune)
        assert kept_count <= 35  # 24 hourly + 7 daily + 4 weekly
        assert pruned_count == 40 - kept_count

    def test_prune_size_savings(self) -> None:
        """Pruned bytes should represent the overflow beyond tier caps."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"sz-{i}.sql.gz",
                mtime=now - (i * 3600),
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(40)
        ]
        result = compute_retention_plan(snaps, now=now)
        pruned_bytes = sum(s.size_bytes for s in result.prune)
        kept_bytes = sum(s.size_bytes for s in result.keep)
        assert pruned_bytes > 0
        assert kept_bytes + pruned_bytes == sum(s.size_bytes for s in snaps)

    def test_single_snapshot_kept(self) -> None:
        """One snapshot is always kept (hourly)."""
        now = time.time()
        snap = _snap(0)
        result = compute_retention_plan([snap], now=now)
        assert len(result.keep) == 1
        assert len(result.prune) == 0

    def test_ancient_snapshot_pruned(self) -> None:
        """Snapshot older than 4 weeks is pruned."""
        now = time.time()
        snap = _snap(35 * 86400)  # 35 days
        result = compute_retention_plan([snap], now=now)
        assert len(result.prune) == 1
        assert result.prune[0].tier == "prune"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary and edge-case tests."""

    def test_all_snapshots_same_mtime(self) -> None:
        """Multiple snapshots with identical mtime: all compete for hourly,
        oldest-mtime-tie-breaker should handle deterministically."""
        now = time.time()
        snaps = [
            BackupSnapshot(
                filename=f"tie-{i}.sql.gz",
                mtime=now - 100,
                size_bytes=300_000_000,
                tier=None,
            )
            for i in range(25)
        ]
        result = classify_tiers(snaps, now=now)
        hourly = [s for s in result if s.tier == "hourly"]
        daily = [s for s in result if s.tier == "daily"]
        assert len(hourly) == 24
        assert len(daily) == 1

    def test_future_mtime_handled(self) -> None:
        """Snapshot with mtime in the future should still be hourly."""
        now = time.time()
        snap = BackupSnapshot(
            filename="future.sql.gz",
            mtime=now + 60,
            size_bytes=300_000_000,
            tier=None,
        )
        result = classify_tiers([snap], now=now)
        assert result[0].tier == "hourly"

    def test_zero_size_snapshot(self) -> None:
        """Zero-size snapshot (failed backup?) still gets classified."""
        now = time.time()
        snap = BackupSnapshot(
            filename="empty.sql.gz",
            mtime=now - 60,
            size_bytes=0,
            tier=None,
        )
        result = classify_tiers([snap], now=now)
        assert result[0].tier == "hourly"

    def test_classify_does_not_mutate_input(self) -> None:
        """classify_tiers must return new objects, not mutate input."""
        now = time.time()
        snap = _snap(60)
        original_mtime = snap.mtime
        original_tier = snap.tier
        _ = classify_tiers([snap], now=now)
        assert snap.mtime == original_mtime
        assert snap.tier == original_tier  # should still be None

    def test_config_validation_negative_count(self) -> None:
        """Negative count should raise ValueError."""
        with pytest.raises(ValueError):
            GFSTierConfig(
                hourly_interval_minutes=60,
                hourly_count=-1,
                daily_interval_minutes=1440,
                daily_count=7,
                weekly_interval_minutes=10080,
                weekly_count=4,
            )

    def test_config_validation_zero_interval(self) -> None:
        """Zero interval should raise ValueError."""
        with pytest.raises(ValueError):
            GFSTierConfig(
                hourly_interval_minutes=0,
                hourly_count=24,
                daily_interval_minutes=1440,
                daily_count=7,
                weekly_interval_minutes=10080,
                weekly_count=4,
            )
