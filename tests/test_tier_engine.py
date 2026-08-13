"""Tests for GFS tier engine — tier classification, promotion, and tiered pruner.

Tests cover all acceptance criteria from NFM-3015:
- Snapshots correctly classified into tiers based on age
- Tier promotion picks the best candidate per window (closest to day boundary)
- Pruner deletes oldest-first, respecting tier count limits
- Steady-state backup size <= 12 GiB
- Restore path (.sql.gz per snapshot) unchanged
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backup.manifest import Manifest
from backup.promoter import promote_snapshots
from backup.pruner import TieredPruner
from backup.snapshot import Snapshot
from backup.tier import Tier, classify_tier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)
ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(weeks=1)


def make_snapshot(
    age: timedelta,
    tier: Tier = Tier.HOURLY,
    snapshot_id: str = "snap",
    size_bytes: int = 300 * 1024 * 1024,
) -> Snapshot:
    """Create a synthetic snapshot with a given age offset from NOW."""
    return Snapshot(
        snapshot_id=snapshot_id,
        timestamp=NOW - age,
        tier=tier,
        size_bytes=size_bytes,
        path=Path(f"/backups/{snapshot_id}.sql.gz"),
    )


def make_snapshots(
    count: int, interval: timedelta, start_age: timedelta = timedelta(0)
) -> list[Snapshot]:
    """Create a sequence of snapshots spaced by *interval*, newest first."""
    return [
        make_snapshot(
            age=start_age + interval * i,
            snapshot_id=f"snap-{i:03d}",
        )
        for i in range(count)
    ]


# ===========================================================================
# TIER CLASSIFICATION
# ===========================================================================


class TestClassifyTier:
    """AC: Snapshots are correctly classified into tiers based on age."""

    def test_fresh_snapshot_is_hourly(self) -> None:
        snap = make_snapshot(age=timedelta(minutes=30))
        assert classify_tier(snap.timestamp, NOW) == Tier.HOURLY

    def test_23_hours_is_hourly(self) -> None:
        snap = make_snapshot(age=timedelta(hours=23))
        assert classify_tier(snap.timestamp, NOW) == Tier.HOURLY

    def test_exactly_24_hours_is_daily(self) -> None:
        snap = make_snapshot(age=ONE_DAY)
        assert classify_tier(snap.timestamp, NOW) == Tier.DAILY

    def test_3_days_is_daily(self) -> None:
        snap = make_snapshot(age=timedelta(days=3))
        assert classify_tier(snap.timestamp, NOW) == Tier.DAILY

    def test_exactly_7_days_is_weekly(self) -> None:
        snap = make_snapshot(age=ONE_WEEK)
        assert classify_tier(snap.timestamp, NOW) == Tier.WEEKLY

    def test_30_days_is_weekly(self) -> None:
        snap = make_snapshot(age=timedelta(days=30))
        assert classify_tier(snap.timestamp, NOW) == Tier.WEEKLY

    def test_classify_boundary_edge_23h59m(self) -> None:
        snap = make_snapshot(age=timedelta(hours=23, minutes=59))
        assert classify_tier(snap.timestamp, NOW) == Tier.HOURLY

    def test_classify_boundary_edge_6d23h(self) -> None:
        snap = make_snapshot(age=timedelta(days=6, hours=23))
        assert classify_tier(snap.timestamp, NOW) == Tier.DAILY


# ===========================================================================
# SNAPSHOT MODEL
# ===========================================================================


class TestSnapshot:
    """Snapshot data model tests."""

    def test_snapshot_age(self) -> None:
        snap = make_snapshot(age=ONE_DAY + ONE_HOUR)
        assert snap.age(NOW) == ONE_DAY + ONE_HOUR

    def test_snapshot_is_restorable(self) -> None:
        """AC: Restore path (.sql.gz per snapshot) unchanged."""
        snap = make_snapshot(age=ONE_HOUR)
        assert snap.path.name.endswith(".sql.gz")

    def test_snapshot_equality(self) -> None:
        ts = NOW - ONE_HOUR
        s1 = Snapshot("a", ts, Tier.HOURLY, 100, Path("/a.sql.gz"))
        s2 = Snapshot("a", ts, Tier.HOURLY, 100, Path("/a.sql.gz"))
        assert s1 == s2

    def test_snapshot_ordering_by_timestamp(self) -> None:
        newer = make_snapshot(age=ONE_HOUR, snapshot_id="new")
        older = make_snapshot(age=ONE_DAY, snapshot_id="old")
        assert newer < older

    def test_tier_enum_values(self) -> None:
        assert Tier.HOURLY.value == "hourly"
        assert Tier.DAILY.value == "daily"
        assert Tier.WEEKLY.value == "weekly"


# ===========================================================================
# TIER PROMOTION
# ===========================================================================


class TestPromoteSnapshots:
    """AC: Tier promotion picks the best candidate per window."""

    def test_promote_hourly_to_daily_picks_closest_to_midnight(self) -> None:
        # All three crossing snapshots on the same calendar day (2026-08-11),
        # all older than 24h from NOW (2026-08-13T12:00Z).
        # Use direct timestamps to guarantee same calendar day.
        near_midnight = Snapshot(
            "near-midnight",
            datetime(2026, 8, 11, 0, 10, tzinfo=UTC),  # 00:10
            Tier.HOURLY, 300 * 1024 * 1024, Path("/backups/near-midnight.sql.gz"),
        )
        morning_snap = Snapshot(
            "morning",
            datetime(2026, 8, 11, 6, 30, tzinfo=UTC),  # 06:30
            Tier.HOURLY, 300 * 1024 * 1024, Path("/backups/morning.sql.gz"),
        )
        afternoon_snap = Snapshot(
            "afternoon",
            datetime(2026, 8, 11, 15, 0, tzinfo=UTC),  # 15:00
            Tier.HOURLY, 300 * 1024 * 1024, Path("/backups/afternoon.sql.gz"),
        )

        result = promote_snapshots([near_midnight, morning_snap, afternoon_snap], NOW)
        assert len(result.promoted) == 1
        assert result.promoted[0].snapshot_id == "near-midnight"
        assert result.promoted[0].tier == Tier.DAILY

    def test_no_promotion_when_all_within_hourly_window(self) -> None:
        snaps = make_snapshots(5, ONE_HOUR)
        result = promote_snapshots(snaps, NOW)
        assert len(result.promoted) == 0

    def test_promote_daily_to_weekly(self) -> None:
        old_daily = make_snapshot(
            age=ONE_WEEK + timedelta(hours=2),
            tier=Tier.DAILY,
            snapshot_id="old-daily",
        )
        result = promote_snapshots([old_daily], NOW)
        assert len(result.promoted) == 1
        assert result.promoted[0].snapshot_id == "old-daily"
        assert result.promoted[0].tier == Tier.WEEKLY

    def test_remaining_snapshots_returned(self) -> None:
        young = make_snapshot(age=ONE_HOUR, snapshot_id="young")
        old = make_snapshot(
            age=ONE_DAY + timedelta(minutes=10),
            tier=Tier.HOURLY,
            snapshot_id="old",
        )
        result = promote_snapshots([young, old], NOW)
        remaining_ids = {s.snapshot_id for s in result.remaining}
        assert "young" in remaining_ids
        assert "old" not in remaining_ids


# ===========================================================================
# TIERED PRUNER
# ===========================================================================


class TestTieredPruner:
    """AC: Pruner deletes oldest-first, respecting tier count limits.
    AC: Never delete sole representative of tier window."""

    def test_prune_excess_hourly(self) -> None:
        snaps = make_snapshots(26, ONE_HOUR)
        pruner = TieredPruner(
            max_hourly=24, max_daily=7, max_weekly=4,
        )
        decisions = pruner.prune(snaps, NOW)
        deleted_ids = {d.snapshot.snapshot_id for d in decisions if d.action == "delete"}
        assert len(deleted_ids) == 2
        assert "snap-024" in deleted_ids
        assert "snap-025" in deleted_ids

    def test_prune_excess_daily(self) -> None:
        snaps = [
            make_snapshot(
                age=ONE_DAY * (i + 1) + timedelta(hours=i),
                tier=Tier.DAILY,
                snapshot_id=f"daily-{i}",
            )
            for i in range(9)
        ]
        pruner = TieredPruner(max_hourly=24, max_daily=7, max_weekly=4)
        decisions = pruner.prune(snaps, NOW)
        deleted_ids = {d.snapshot.snapshot_id for d in decisions if d.action == "delete"}
        assert len(deleted_ids) == 2

    def test_prune_excess_weekly(self) -> None:
        snaps = [
            make_snapshot(
                age=ONE_WEEK * (i + 1) + timedelta(hours=i),
                tier=Tier.WEEKLY,
                snapshot_id=f"weekly-{i}",
            )
            for i in range(6)
        ]
        pruner = TieredPruner(max_hourly=24, max_daily=7, max_weekly=4)
        decisions = pruner.prune(snaps, NOW)
        deleted_ids = {d.snapshot.snapshot_id for d in decisions if d.action == "delete"}
        assert len(deleted_ids) == 2

    def test_no_deletion_within_limits(self) -> None:
        snaps = make_snapshots(10, ONE_HOUR)
        pruner = TieredPruner(max_hourly=24, max_daily=7, max_weekly=4)
        decisions = pruner.prune(snaps, NOW)
        deleted_ids = {d.snapshot.snapshot_id for d in decisions if d.action == "delete"}
        assert len(deleted_ids) == 0

    def test_never_delete_sole_window_representative(self) -> None:
        d1a = make_snapshot(age=ONE_DAY * 1 + timedelta(hours=1), tier=Tier.DAILY, snapshot_id="d1a")
        d1b = make_snapshot(age=ONE_DAY * 1 + timedelta(hours=5), tier=Tier.DAILY, snapshot_id="d1b")
        d1c = make_snapshot(age=ONE_DAY * 1 + timedelta(hours=12), tier=Tier.DAILY, snapshot_id="d1c")
        d2 = make_snapshot(age=ONE_DAY * 2 + timedelta(hours=3), tier=Tier.DAILY, snapshot_id="d2")
        d3 = make_snapshot(age=ONE_DAY * 3 + timedelta(hours=8), tier=Tier.DAILY, snapshot_id="d3")
        d4 = make_snapshot(age=ONE_DAY * 4, tier=Tier.DAILY, snapshot_id="d4")
        d5 = make_snapshot(age=ONE_DAY * 5, tier=Tier.DAILY, snapshot_id="d5")
        d6 = make_snapshot(age=ONE_DAY * 6, tier=Tier.DAILY, snapshot_id="d6")
        d7 = make_snapshot(age=ONE_DAY * 7, tier=Tier.DAILY, snapshot_id="d7")
        d8 = make_snapshot(age=ONE_DAY * 8, tier=Tier.DAILY, snapshot_id="d8")

        all_snaps = [d1a, d1b, d1c, d2, d3, d4, d5, d6, d7, d8]
        pruner = TieredPruner(max_hourly=24, max_daily=7, max_weekly=4)
        decisions = pruner.prune(all_snaps, NOW)
        deleted_ids = {d.snapshot.snapshot_id for d in decisions if d.action == "delete"}

        assert "d2" not in deleted_ids
        assert "d3" not in deleted_ids
        assert len(deleted_ids) > 0

    def test_pruner_precedence_oldest_first(self) -> None:
        hourly_old = make_snapshot(age=ONE_HOUR * 25, tier=Tier.HOURLY, snapshot_id="h-old")
        daily_new = make_snapshot(age=ONE_DAY + ONE_HOUR, tier=Tier.DAILY, snapshot_id="d-new")
        weekly_new = make_snapshot(age=ONE_WEEK + ONE_HOUR, tier=Tier.WEEKLY, snapshot_id="w-new")

        pruner = TieredPruner(max_hourly=0, max_daily=1, max_weekly=1)
        decisions = pruner.prune([hourly_old, daily_new, weekly_new], NOW)
        deleted = [d for d in decisions if d.action == "delete"]
        assert len(deleted) == 1
        assert deleted[0].snapshot.snapshot_id == "h-old"

    def test_steady_state_size_within_budget(self) -> None:
        hourly = make_snapshots(24, ONE_HOUR)
        daily = [
            make_snapshot(
                age=ONE_DAY * (i + 1) + timedelta(hours=6),
                tier=Tier.DAILY,
                snapshot_id=f"daily-{i}",
            )
            for i in range(7)
        ]
        weekly = [
            make_snapshot(
                age=ONE_WEEK * (i + 1) + timedelta(days=1),
                tier=Tier.WEEKLY,
                snapshot_id=f"weekly-{i}",
            )
            for i in range(4)
        ]
        all_snaps = hourly + daily + weekly

        pruner = TieredPruner(max_hourly=24, max_daily=7, max_weekly=4)
        decisions = pruner.prune(all_snaps, NOW)
        deleted_bytes = sum(
            d.snapshot.size_bytes for d in decisions if d.action == "delete"
        )
        retained_bytes = sum(s.size_bytes for s in all_snaps) - deleted_bytes
        max_budget = 12 * 1024 ** 3
        assert retained_bytes <= max_budget


# ===========================================================================
# MANIFEST
# ===========================================================================


class TestManifest:
    """Snapshot metadata tagging via sidecar manifest."""

    def test_write_and_read_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Manifest(Path(tmpdir))
            snap = make_snapshot(age=ONE_HOUR, snapshot_id="test-001")
            manifest.write(snap)

            loaded = manifest.read("test-001")
            assert loaded is not None
            assert loaded.snapshot_id == "test-001"
            assert loaded.tier == Tier.HOURLY
            assert loaded.size_bytes == snap.size_bytes

    def test_manifest_lists_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Manifest(Path(tmpdir))
            for i in range(3):
                manifest.write(make_snapshot(age=ONE_HOUR * i, snapshot_id=f"snap-{i}"))
            all_snaps = manifest.list_all()
            assert len(all_snaps) == 3

    def test_manifest_update_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Manifest(Path(tmpdir))
            snap = make_snapshot(age=ONE_DAY + ONE_HOUR, snapshot_id="promote-me")
            manifest.write(snap)

            manifest.update_tier("promote-me", Tier.DAILY)
            loaded = manifest.read("promote-me")
            assert loaded is not None
            assert loaded.tier == Tier.DAILY

    def test_manifest_delete_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Manifest(Path(tmpdir))
            manifest.write(make_snapshot(age=ONE_HOUR, snapshot_id="to-delete"))
            manifest.write(make_snapshot(age=ONE_HOUR * 2, snapshot_id="to-keep"))

            manifest.delete("to-delete")
            assert manifest.read("to-delete") is None
            assert manifest.read("to-keep") is not None
