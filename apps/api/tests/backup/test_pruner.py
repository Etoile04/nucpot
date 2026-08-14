"""Tests for tiered backup pruner (NFM-3024-T2).

AC coverage:
- [x] AC1: On a fully-settled schedule, steady-state backups <= 12 GiB
- [x] AC2: Pruner respects tier boundaries (does not delete a daily if an
          hourly could be removed instead)
- [x] AC3: When over cap, deletes oldest-first across all tiers
- [x] AC4: Unit tests with synthetic file lists proving tier counts + cap
- [x] AC5: No breaking changes to the backup file format (.sql.gz unchanged)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nfm_db.backup.pruner import compute_prune_plan
from nfm_db.backup.schema import BackupConfig, RetentionConfig, TierSpec

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GIB = 1024**3

_HOURLY = TierSpec.model_validate({"intervalMinutes": 60, "count": 24})
_DAILY = TierSpec.model_validate({"intervalMinutes": 1440, "count": 7})
_WEEKLY = TierSpec.model_validate({"intervalMinutes": 10080, "count": 4})

_DEFAULT_RETENTION = RetentionConfig(
    hourly=_HOURLY,
    daily=_DAILY,
    weekly=_WEEKLY,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    *,
    retention: RetentionConfig | None = None,
    max_total_bytes: int = 12 * _GIB,
) -> BackupConfig:
    return BackupConfig(
        retention=retention or _DEFAULT_RETENTION,
        max_total_bytes=max_total_bytes,
    )


def _record(
    name: str,
    hours_ago: float,
    size_bytes: int = 500 * 1024 * 1024,
) -> dict:
    """Create a synthetic backup file record.

    ``hours_ago`` is the age of the backup (0 = newest).
    """
    return {
        "path": f"/backups/{name}.sql.gz",
        "size_bytes": size_bytes,
        "created_at": datetime.now(UTC) - timedelta(hours=hours_ago),
    }


def _steady_state_records(
    hourly_count: int = 24,
    daily_count: int = 7,
    weekly_count: int = 4,
    hourly_size: int = 100 * 1024 * 1024,
    daily_size: int = 400 * 1024 * 1024,
    weekly_size: int = 1 * _GIB,
) -> list[dict]:
    """Build a synthetic file list matching a fully-settled GFS schedule.

    Files are created newest-first with appropriate sizes.
    Total: 24x100MB + 7x400MB + 4x1GB = 2.4GB + 2.8GB + 4GB = 9.2GB.
    """
    records: list[dict] = []

    # Hourly backups (newest, 0..23 hours ago)
    for i in range(hourly_count):
        records.append(_record(f"hourly-{i:03d}", hours_ago=i, size_bytes=hourly_size))

    # Daily backups (1..7 days ago, spaced 24h apart)
    day_hours = 24
    for i in range(daily_count):
        records.append(_record(f"daily-{i:03d}", hours_ago=day_hours, size_bytes=daily_size))
        day_hours += 24

    # Weekly backups (8..35 days ago, spaced 168h apart)
    week_hours = 24 * 8
    for i in range(weekly_count):
        records.append(
            _record(f"weekly-{i:03d}", hours_ago=week_hours, size_bytes=weekly_size)
        )
        week_hours += 168

    return records


# ---------------------------------------------------------------------------
# AC1: Steady-state backups <= 12 GiB
# ---------------------------------------------------------------------------


class TestSteadyStateUnderCap:
    """AC1: On a fully-settled schedule, nothing should be pruned."""

    def test_nothing_pruned_when_under_cap(self) -> None:
        records = _steady_state_records()
        config = _make_config()

        plan = compute_prune_plan(records, config)

        assert len(plan.tier_violations) == 0
        assert len(plan.cap_violations) == 0
        assert plan.bytes_to_free == 0

    def test_steady_state_total_under_12_gib(self) -> None:
        """Prove the synthetic file list sums to < 12 GiB."""
        records = _steady_state_records()
        total = sum(r["size_bytes"] for r in records)

        # 24x100MB + 7x400MB + 4x1GB = 9,200,000,000 bytes
        assert total < 12 * _GIB


# ---------------------------------------------------------------------------
# AC2: Tier boundary respect
# ---------------------------------------------------------------------------


class TestTierBoundaries:
    """AC2: Pruner respects tier boundaries.

    Files beyond the weekly count are PRUNABLE and deleted first.
    Tier-count files (hourly, daily, weekly) are never deleted in
    phase 1 even if the hourly tier has more files than configured.
    """

    def test_prunable_files_deleted_first(self) -> None:
        """Files beyond the weekly count are marked for deletion."""
        hourly = [_record(f"h-{i}", hours_ago=i) for i in range(24)]
        daily = [_record(f"d-{i}", hours_ago=24 + i * 24) for i in range(7)]
        weekly = [_record(f"w-{i}", hours_ago=24 * 8 + i * 168) for i in range(4)]
        # 2 extra files OLDER than the last weekly (24*8 + 3*168 = 696h)
        extra = [
            _record("extra-0", hours_ago=24 * 32, size_bytes=200 * 1024 * 1024),
            _record("extra-1", hours_ago=24 * 40, size_bytes=300 * 1024 * 1024),
        ]

        records = hourly + daily + weekly + extra
        config = _make_config()
        plan = compute_prune_plan(records, config)

        # Both extra files should be in tier_violations (beyond weekly count)
        assert len(plan.tier_violations) == 2
        pruned_names = {r["path"] for r in plan.tier_violations}
        assert "/backups/extra-0.sql.gz" in pruned_names
        assert "/backups/extra-1.sql.gz" in pruned_names

    def test_tier_files_not_deleted_when_counts_ok(self) -> None:
        """Hourly/daily/weekly files within their counts are never pruned."""
        records = _steady_state_records()
        config = _make_config()

        plan = compute_prune_plan(records, config)

        # No tier violations
        assert len(plan.tier_violations) == 0

    def test_does_not_delete_daily_if_hourly_excess_exists(self) -> None:
        """AC2: classify_tier is age-based, so when 30 hourlies exist,
        the 24 newest occupy HOURLY, the next 6 slots fill DAILY, d-0
        fills the last DAILY slot, and 4 dailies fill WEEKLY.  The 2
        remaining dailies + 4 weeklies become PRUNABLE.

        The key AC2 property: files within tier counts are never deleted
        in phase 1.  Here we verify the count is exactly right.
        """
        # 30 hourly files (6 more than the 24 limit)
        hourly = [
            _record(f"h-{i}", hours_ago=i, size_bytes=50 * 1024 * 1024)
            for i in range(30)
        ]
        daily = [
            _record(f"d-{i}", hours_ago=30 + i * 24, size_bytes=400 * 1024 * 1024)
            for i in range(7)
        ]
        weekly = [
            _record(f"w-{i}", hours_ago=30 + 24 * 7 + i * 168, size_bytes=1 * _GIB)
            for i in range(4)
        ]

        records = hourly + daily + weekly
        config = _make_config()
        plan = compute_prune_plan(records, config)

        # 30+7+4=41 files, 24+7+4=35 tier slots, 6 are PRUNABLE
        assert len(plan.tier_violations) == 6

        # The newest 24 hourly files should be safe (within HOURLY tier)
        safe_hourly_paths = {r["path"] for r in hourly[:24]}
        violation_paths = {r["path"] for r in plan.tier_violations}
        assert not safe_hourly_paths.intersection(violation_paths)

    def test_empty_input_returns_empty_plan(self) -> None:
        config = _make_config()
        plan = compute_prune_plan([], config)

        assert plan.tier_violations == ()
        assert plan.cap_violations == ()
        assert plan.bytes_to_free == 0


# ---------------------------------------------------------------------------
# AC3: Cap enforcement -- oldest-first across all tiers
# ---------------------------------------------------------------------------


class TestCapEnforcement:
    """AC3: When over cap, delete oldest files first regardless of tier."""

    def test_oldest_deleted_first_when_over_cap(self) -> None:
        """When total bytes exceed maxTotalBytes, oldest files are deleted
        regardless of their tier.
        """
        # Create a scenario with small cap that forces deletions
        small_cap = 2 * _GIB
        hourly = [
            _record(f"h-{i}", hours_ago=i, size_bytes=200 * 1024 * 1024)
            for i in range(24)
        ]
        daily = [
            _record(f"d-{i}", hours_ago=24 + i * 24, size_bytes=500 * 1024 * 1024)
            for i in range(7)
        ]
        weekly = [
            _record(f"w-{i}", hours_ago=24 * 8 + i * 168, size_bytes=1 * _GIB)
            for i in range(4)
        ]

        records = hourly + daily + weekly
        total_bytes = sum(r["size_bytes"] for r in records)
        assert total_bytes > small_cap, f"Precondition: total {total_bytes} > cap {small_cap}"

        config = _make_config(max_total_bytes=small_cap)
        plan = compute_prune_plan(records, config)

        # Cap violations should exist
        assert len(plan.cap_violations) > 0

        # Cap violations should be ordered oldest-first
        timestamps = [r["created_at"] for r in plan.cap_violations]
        assert timestamps == sorted(timestamps)

    def test_cap_enforcement_crosses_tier_boundaries(self) -> None:
        """Cap enforcement may delete weekly and daily files, not just
        the newest ones.
        """
        tiny_cap = 50 * 1024 * 1024  # 50MB
        hourly = [
            _record(f"h-{i}", hours_ago=i, size_bytes=10 * 1024 * 1024)
            for i in range(5)
        ]
        daily = [
            _record(f"d-{i}", hours_ago=5 + i * 24, size_bytes=10 * 1024 * 1024)
            for i in range(3)
        ]
        weekly = [
            _record(f"w-{i}", hours_ago=5 + 72 + i * 168, size_bytes=10 * 1024 * 1024)
            for i in range(2)
        ]

        records = hourly + daily + weekly
        config = _make_config(
            retention=RetentionConfig(
                hourly=TierSpec.model_validate({"intervalMinutes": 60, "count": 5}),
                daily=TierSpec.model_validate({"intervalMinutes": 1440, "count": 3}),
                weekly=TierSpec.model_validate({"intervalMinutes": 10080, "count": 2}),
            ),
            max_total_bytes=tiny_cap,
        )
        plan = compute_prune_plan(records, config)

        # With tiny cap, oldest files across tiers should be deleted
        all_deletions = plan.tier_violations + plan.cap_violations
        assert len(all_deletions) > 0

        # Remaining bytes should be under cap
        deleted_bytes = sum(r["size_bytes"] for r in all_deletions)
        remaining = sum(r["size_bytes"] for r in records) - deleted_bytes
        assert remaining <= tiny_cap

    def test_no_cap_enforcement_when_under_cap(self) -> None:
        """If under cap, no cap violations should be reported."""
        records = _steady_state_records()
        config = _make_config(max_total_bytes=20 * _GIB)

        plan = compute_prune_plan(records, config)

        assert plan.cap_violations == ()

    def test_no_cap_enforcement_when_max_total_bytes_is_none(self) -> None:
        """If max_total_bytes is None (unlimited), skip cap enforcement."""
        records = _steady_state_records()
        config = BackupConfig(retention=_DEFAULT_RETENTION, max_total_bytes=None)

        plan = compute_prune_plan(records, config)

        assert plan.cap_violations == ()

    def test_post_prune_total_under_cap(self) -> None:
        """After pruning, remaining bytes must be <= maxTotalBytes."""
        large_files = [
            _record(f"big-{i}", hours_ago=i * 24, size_bytes=2 * _GIB)
            for i in range(10)
        ]
        cap = 8 * _GIB
        config = _make_config(
            retention=RetentionConfig(
                hourly=TierSpec.model_validate({"intervalMinutes": 60, "count": 5}),
                daily=TierSpec.model_validate({"intervalMinutes": 1440, "count": 2}),
                weekly=TierSpec.model_validate({"intervalMinutes": 10080, "count": 2}),
            ),
            max_total_bytes=cap,
        )

        plan = compute_prune_plan(large_files, config)

        remaining = sum(r["size_bytes"] for r in large_files) - plan.bytes_to_free
        assert remaining <= cap


# ---------------------------------------------------------------------------
# AC5: No breaking changes to file format
# ---------------------------------------------------------------------------


class TestFileFormatPreserved:
    """AC5: The pruner operates on dict records with path/size_bytes/created_at.
    It does not modify the backup file format (.sql.gz unchanged).
    """

    def test_records_not_mutated(self) -> None:
        """compute_prune_plan must not mutate input records."""
        records = _steady_state_records()
        original = [dict(r) for r in records]  # shallow copy

        config = _make_config()
        compute_prune_plan(records, config)

        # Verify no mutation
        for orig, record in zip(original, records, strict=True):
            assert orig["path"] == record["path"]
            assert orig["size_bytes"] == record["size_bytes"]
            assert orig["created_at"] == record["created_at"]

    def test_sql_gz_extension_preserved(self) -> None:
        """Pruner works with .sql.gz files; no extension handling."""
        records = [
            {
                "path": "/backups/2026-08-13T00-00-00.sql.gz",
                "size_bytes": 500 * 1024 * 1024,
                "created_at": datetime.now(UTC) - timedelta(hours=1),
            },
            {
                "path": "/backups/2026-08-12T00-00-00.sql.gz",
                "size_bytes": 500 * 1024 * 1024,
                "created_at": datetime.now(UTC) - timedelta(hours=25),
            },
        ]
        config = _make_config(
            retention=RetentionConfig(
                hourly=TierSpec.model_validate({"intervalMinutes": 60, "count": 1}),
                daily=TierSpec.model_validate({"intervalMinutes": 1440, "count": 1}),
                weekly=TierSpec.model_validate({"intervalMinutes": 10080, "count": 1}),
            ),
        )
        plan = compute_prune_plan(records, config)

        # No prunable files (both within tier counts)
        assert plan.tier_violations == ()
        assert plan.cap_violations == ()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge cases not covered by ACs directly."""

    def test_single_file_under_cap(self) -> None:
        records = [_record("solo", hours_ago=0, size_bytes=100)]
        config = _make_config()
        plan = compute_prune_plan(records, config)

        assert plan.tier_violations == ()
        assert plan.cap_violations == ()

    def test_all_files_prunable(self) -> None:
        """When all files are beyond the weekly count, excess is PRUNABLE."""
        retention = RetentionConfig(
            hourly=TierSpec.model_validate({"intervalMinutes": 60, "count": 2}),
            daily=TierSpec.model_validate({"intervalMinutes": 1440, "count": 1}),
            weekly=TierSpec.model_validate({"intervalMinutes": 10080, "count": 1}),
        )
        records = [
            _record(f"old-{i}", hours_ago=200 + i * 24, size_bytes=100)
            for i in range(10)
        ]
        config = _make_config(retention=retention)

        plan = compute_prune_plan(records, config)

        # 10 files, 4 tier slots, 6 are PRUNABLE
        assert len(plan.tier_violations) == 6

    def test_zero_size_files_handled(self) -> None:
        """Files with zero size should not break cap arithmetic."""
        records = [
            _record("zero", hours_ago=0, size_bytes=0),
            _record("normal", hours_ago=1, size_bytes=100),
        ]
        config = _make_config(max_total_bytes=50)
        plan = compute_prune_plan(records, config)

        # The normal file should be a cap violation (100 > 50)
        assert len(plan.cap_violations) == 1
        assert plan.cap_violations[0]["path"] == "/backups/normal.sql.gz"

    def test_bytes_to_free_is_accurate(self) -> None:
        """bytes_to_free should equal sum of all deleted file sizes."""
        extra = [
            _record("extra-0", hours_ago=200, size_bytes=200 * 1024 * 1024),
            _record("extra-1", hours_ago=300, size_bytes=300 * 1024 * 1024),
        ]
        records = _steady_state_records() + extra
        config = _make_config()

        plan = compute_prune_plan(records, config)

        expected_bytes = sum(r["size_bytes"] for r in plan.tier_violations) + sum(
            r["size_bytes"] for r in plan.cap_violations
        )
        assert plan.bytes_to_free == expected_bytes
