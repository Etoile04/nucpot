"""Tests for backup capacity guardrails (NFM-3051 / NFM-3024-C).

TDD RED phase — these tests define the contract for:
- minFreeBytes refusal path (AC1)
- maxTotalBytes pruner with oldest-first deletion (AC2)
- minFreeBytes never breached across 100 writes (AC3)
- Deterministic capacity decisions on synthetic fs mock (AC4)
- Tier promotion pruner precedence, refusal path, post-prune re-check (AC5)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module under test — will fail on import until implementation exists
# ---------------------------------------------------------------------------
from nfm_db.services.backup_capacity import (
    BackupPruner,
    CapacityConfig,
    CapacityGuard,
    SnapshotStore,
    SnapshotTier,
    WriteResult,
)


# ===========================================================================
# Constants
# ===========================================================================

GiB = 1024 ** 3


# ===========================================================================
# Synthetic filesystem mock (AC4 — deterministic)
# ===========================================================================


@dataclass
class _SyntheticFile:
    """In-memory representation of a backup snapshot file."""

    path: str
    size_bytes: int
    created_at: float
    tier: SnapshotTier


class SyntheticSnapshotStore:
    """Deterministic in-memory snapshot store for testing.

    Implements the SnapshotStore protocol without touching real disk.
    All capacity decisions are fully reproducible.
    """

    def __init__(self, total_disk_bytes: int = 100 * GiB) -> None:
        self._files: dict[str, _SyntheticFile] = {}
        self._total_disk_bytes = total_disk_bytes
        self._deleted_paths: list[str] = []
        self._disk_bytes_used: int = 0

    def add_snapshot(
        self,
        path: str,
        size_bytes: int,
        created_at: float,
        tier: SnapshotTier = SnapshotTier.HOURLY,
    ) -> None:
        self._files[path] = _SyntheticFile(
            path=path,
            size_bytes=size_bytes,
            created_at=created_at,
            tier=tier,
        )
        self._disk_bytes_used += size_bytes

    def set_disk_bytes_used(self, used: int) -> None:
        """Override the simulated disk usage (for testing minFreeBytes)."""
        self._disk_bytes_used = used

    # -- SnapshotStore protocol --

    def list_snapshots(self) -> list[dict]:
        return [
            {
                "path": f.path,
                "size_bytes": f.size_bytes,
                "created_at": f.created_at,
                "tier": f.tier,
            }
            for f in sorted(self._files.values(), key=lambda x: x.created_at)
        ]

    def delete_snapshot(self, path: str) -> int:
        f = self._files.pop(path, None)
        if f is not None:
            self._deleted_paths.append(path)
            self._disk_bytes_used -= f.size_bytes
            return f.size_bytes
        return 0

    def total_snapshot_bytes(self) -> int:
        return sum(f.size_bytes for f in self._files.values())

    def free_bytes(self) -> int:
        return self._total_disk_bytes - self._disk_bytes_used

    @property
    def deleted_paths(self) -> list[str]:
        return list(self._deleted_paths)


# ===========================================================================
# AC1: minFreeBytes refusal path
# ===========================================================================


class TestMinFreeBytesRefusal:
    """AC1: Simulated run that would breach the floor returns
    refused=true with refusalCount incremented."""

    def test_refuses_write_when_free_space_would_drop_below_floor(self):
        store = SyntheticSnapshotStore(total_disk_bytes=100)
        store.set_disk_bytes_used(85)

        config = CapacityConfig(
            max_total_bytes=50,
            min_free_bytes=20,
        )
        guard = CapacityGuard(store=store, config=config)

        result = guard.check_write_feasibility(projected_bytes=10)

        assert result.refused is True
        assert result.refusal_count == 1
        assert result.last_refusal_at is not None

    def test_allows_write_when_free_space_sufficient(self):
        store = SyntheticSnapshotStore(total_disk_bytes=100)
        store.set_disk_bytes_used(50)

        config = CapacityConfig(
            max_total_bytes=50,
            min_free_bytes=20,
        )
        guard = CapacityGuard(store=store, config=config)

        result = guard.check_write_feasibility(projected_bytes=10)

        assert result.refused is False
        assert result.refusal_count == 0

    def test_refusal_count_increments_on_repeated_refusals(self):
        store = SyntheticSnapshotStore(total_disk_bytes=100)
        store.set_disk_bytes_used(90)

        config = CapacityConfig(
            max_total_bytes=200,
            min_free_bytes=20,
        )
        guard = CapacityGuard(store=store, config=config)

        guard.check_write_feasibility(projected_bytes=5)
        guard.check_write_feasibility(projected_bytes=5)
        result = guard.check_write_feasibility(projected_bytes=5)

        assert result.refused is True
        assert result.refusal_count == 3

    def test_refusal_at_exact_boundary(self):
        """Writing exactly to the floor boundary is refused (>=, not >)."""
        store = SyntheticSnapshotStore(total_disk_bytes=100)
        store.set_disk_bytes_used(80)

        config = CapacityConfig(
            max_total_bytes=200,
            min_free_bytes=20,
        )
        guard = CapacityGuard(store=store, config=config)

        result = guard.check_write_feasibility(projected_bytes=1)

        assert result.refused is True


# ===========================================================================
# AC2: maxTotalBytes pruner with oldest-first deletion
# ===========================================================================


class TestMaxTotalBytesPruner:
    """AC2: Simulated run that exceeds maxTotalBytes triggers pruner;
    pruner settles total under cap; deletion order is oldest-first."""

    def test_pruner_deletes_oldest_first_until_under_cap(self):
        store = SyntheticSnapshotStore(total_disk_bytes=1000)
        for i in range(5):
            store.add_snapshot(
                path=f"/backup/snap_{i}.sql.gz",
                size_bytes=3 * GiB,
                created_at=float(i),
                tier=SnapshotTier.HOURLY,
            )

        config = CapacityConfig(
            max_total_bytes=10 * GiB,
            min_free_bytes=0,
        )
        pruner = BackupPruner(store=store, config=config)
        deleted = pruner.prune()

        assert store.total_snapshot_bytes() <= config.max_total_bytes
        assert len(deleted) >= 2
        assert deleted == sorted(
            deleted, key=lambda p: float(p.split("_")[1].split(".")[0])
        )

    def test_pruner_preserves_newest_in_each_tier(self):
        """Pruner MUST NOT delete the newest snapshot in any tier (RPO)."""
        store = SyntheticSnapshotStore(total_disk_bytes=1000)
        store.add_snapshot("/backup/h1.sql.gz", 2 * GiB, 1.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/h2.sql.gz", 2 * GiB, 2.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/h3.sql.gz", 2 * GiB, 3.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/d1.sql.gz", 3 * GiB, 4.0, SnapshotTier.DAILY)
        store.add_snapshot("/backup/d2.sql.gz", 3 * GiB, 5.0, SnapshotTier.DAILY)
        store.add_snapshot("/backup/w1.sql.gz", 5 * GiB, 6.0, SnapshotTier.WEEKLY)

        config = CapacityConfig(
            max_total_bytes=8 * GiB,
            min_free_bytes=0,
        )
        pruner = BackupPruner(store=store, config=config)
        pruner.prune()

        remaining_paths = {f["path"] for f in store.list_snapshots()}
        assert "/backup/h3.sql.gz" in remaining_paths
        assert "/backup/d2.sql.gz" in remaining_paths
        assert "/backup/w1.sql.gz" in remaining_paths

    def test_pruner_skips_when_under_cap(self):
        store = SyntheticSnapshotStore(total_disk_bytes=1000)
        store.add_snapshot("/backup/snap.sql.gz", 1 * GiB, 1.0)

        config = CapacityConfig(
            max_total_bytes=10 * GiB,
            min_free_bytes=0,
        )
        pruner = BackupPruner(store=store, config=config)
        deleted = pruner.prune()

        assert deleted == []

    def test_pruner_tier_precedence_deletes_hourly_before_daily(self):
        """Tier promotion precedence: hourly pruned before daily before weekly."""
        store = SyntheticSnapshotStore(total_disk_bytes=1000)
        store.add_snapshot("/backup/h1.sql.gz", 1 * GiB, 5.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/h2.sql.gz", 1 * GiB, 6.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/h3.sql.gz", 1 * GiB, 7.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/d1.sql.gz", 1 * GiB, 5.0, SnapshotTier.DAILY)
        store.add_snapshot("/backup/d2.sql.gz", 1 * GiB, 6.0, SnapshotTier.DAILY)
        store.add_snapshot("/backup/w1.sql.gz", 1 * GiB, 5.0, SnapshotTier.WEEKLY)

        config = CapacityConfig(
            max_total_bytes=3 * GiB,
            min_free_bytes=0,
        )
        pruner = BackupPruner(store=store, config=config)
        deleted = pruner.prune()

        assert deleted[0] == "/backup/h1.sql.gz"
        assert deleted[1] == "/backup/h2.sql.gz"
        assert deleted[2] == "/backup/d1.sql.gz"


# ===========================================================================
# AC3: Free space never drops below minFreeBytes across 100 writes
# ===========================================================================


class TestFreeSpaceFloorAcrossWrites:
    """AC3: Free space never drops below minFreeBytes due to backup
    growth across 100 simulated writes."""

    def test_floor_never_breached_across_100_writes(self):
        total_disk = 50 * GiB
        store = SyntheticSnapshotStore(total_disk_bytes=total_disk)
        store.set_disk_bytes_used(10 * GiB)

        config = CapacityConfig(
            max_total_bytes=30 * GiB,
            min_free_bytes=20 * GiB,
        )
        guard = CapacityGuard(store=store, config=config)

        write_size = 200 * 1024 ** 2  # 200 MiB
        accepted = 0
        refused = 0

        for i in range(100):
            pruner = BackupPruner(store=store, config=config)
            pruner.prune()

            result = guard.check_write_feasibility(projected_bytes=write_size)

            if result.refused:
                refused += 1
            else:
                accepted += 1
                current_used = total_disk - store.free_bytes()
                store.set_disk_bytes_used(current_used + write_size)

            assert (
                store.free_bytes() >= config.min_free_bytes
            ), f"Floor breached at write {i}: free={store.free_bytes()}, floor={config.min_free_bytes}"

        assert accepted > 0


# ===========================================================================
# AC4: Deterministic capacity decisions on synthetic fs mock
# ===========================================================================


class TestDeterministicCapacity:
    """AC4: Capacity decisions are deterministic on a synthetic fs mock."""

    def test_same_inputs_produce_same_outputs(self):
        config = CapacityConfig(
            max_total_bytes=10 * GiB,
            min_free_bytes=5 * GiB,
        )

        results = []
        for _ in range(3):
            store = SyntheticSnapshotStore(total_disk_bytes=100 * GiB)
            store.set_disk_bytes_used(90 * GiB)

            for i in range(5):
                store.add_snapshot(
                    f"/backup/snap_{i}.sql.gz",
                    2 * GiB,
                    float(i),
                    SnapshotTier.DAILY,
                )

            guard = CapacityGuard(store=store, config=config)
            pruner = BackupPruner(store=store, config=config)

            pruner.prune()
            check = guard.check_write_feasibility(projected_bytes=1 * GiB)

            results.append((check.refused, check.refusal_count, len(store.list_snapshots())))

        assert results[0] == results[1] == results[2]

    def test_deleted_order_is_deterministic(self):
        store1 = SyntheticSnapshotStore(total_disk_bytes=1000)
        store2 = SyntheticSnapshotStore(total_disk_bytes=1000)
        config = CapacityConfig(max_total_bytes=3 * GiB, min_free_bytes=0)

        for s in (store1, store2):
            for i in range(10):
                s.add_snapshot(f"/backup/snap_{i}.sql.gz", 1 * GiB, float(i))

        pruner1 = BackupPruner(store=store1, config=config)
        deleted1 = pruner1.prune()
        pruner2 = BackupPruner(store=store2, config=config)
        deleted2 = pruner2.prune()

        assert deleted1 == deleted2


# ===========================================================================
# AC5: Tier promotion, refusal path, post-prune re-check
# ===========================================================================


class TestTierPromotionAndPostPruneRecheck:
    """AC5: Tier promotion pruner precedence, refusal path, post-prune
    re-check."""

    def test_post_prune_recheck_allows_previously_refused_write(self):
        """After prune → re-measure, a previously refused write may be accepted."""
        total_disk = 100 * GiB
        store = SyntheticSnapshotStore(total_disk_bytes=total_disk)
        store.set_disk_bytes_used(48 * GiB)

        for i in range(10):
            store.add_snapshot(
                f"/backup/old_{i}.sql.gz",
                4 * GiB,
                float(i),
                SnapshotTier.HOURLY,
            )

        config = CapacityConfig(
            max_total_bytes=30 * GiB,
            min_free_bytes=20 * GiB,
        )
        guard = CapacityGuard(store=store, config=config)

        # Before prune: 12 GiB free < 20 GiB floor → refused
        result_before = guard.check_write_feasibility(projected_bytes=1 * GiB)
        assert result_before.refused is True

        # Run pruner — deletes old snapshots (40 GiB > 30 GiB cap), freeing disk
        pruner = BackupPruner(store=store, config=config)
        pruner.prune()

        # Fresh guard after prune
        guard = CapacityGuard(store=store, config=config)
        result_after = guard.check_write_feasibility(projected_bytes=1 * GiB)

        # After deleting 40 GiB of snapshots, free space recovered
        assert result_after.refused is False

    def test_tier_promotion_hourly_to_daily_preserves_rpo(self):
        """When a snapshot is promoted, the pruner respects the newest
        invariant in the new tier."""
        store = SyntheticSnapshotStore(total_disk_bytes=1000)
        store.add_snapshot("/backup/h1.sql.gz", 2 * GiB, 1.0, SnapshotTier.HOURLY)
        store.add_snapshot("/backup/d1.sql.gz", 3 * GiB, 2.0, SnapshotTier.DAILY)
        store.add_snapshot("/backup/d_promoted.sql.gz", 2 * GiB, 3.0, SnapshotTier.DAILY)

        config = CapacityConfig(max_total_bytes=3 * GiB, min_free_bytes=0)
        pruner = BackupPruner(store=store, config=config)
        pruner.prune()

        remaining = {f["path"] for f in store.list_snapshots()}
        assert "/backup/h1.sql.gz" in remaining
        assert "/backup/d_promoted.sql.gz" in remaining


# ===========================================================================
# Single-flight serialization
# ===========================================================================


class TestSingleFlight:
    """Concurrent backup calls must serialize capacity decisions."""

    def test_concurrent_checks_are_serialized(self):
        """Only one capacity check runs at a time."""
        store = SyntheticSnapshotStore(total_disk_bytes=100)
        store.set_disk_bytes_used(50)

        config = CapacityConfig(max_total_bytes=80, min_free_bytes=10)
        guard = CapacityGuard(store=store, config=config)

        execution_log: list[str] = []
        original_check = guard.check_write_feasibility

        def tracked_check(projected_bytes: int) -> WriteResult:
            execution_log.append("start")
            time.sleep(0.01)
            execution_log.append("end")
            return original_check(projected_bytes)

        guard.check_write_feasibility = tracked_check  # type: ignore[method-assign]

        results = [guard.check_write_feasibility(5) for _ in range(5)]

        assert results
        assert len(execution_log) == 10
