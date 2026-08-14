"""Integration tests — full tiered retention + capacity guardrail pipeline (NFM-3045).

Exercises the entire pipeline end-to-end with a mocked disk:

- :func:`nfm_db.services.backup.retention.apply_tiered_retention` — tier-aware prune
  (NFM-3024 T2). Groups snapshots by tier suffix and prunes oldest beyond the
  per-tier ``count`` cap.
- :class:`nfm_db.services.backup.guardrails.CapacityGuardrails` — pre-write floor
  refusal + post-write cap enforcement + post-pruner floor recheck (NFM-3016).
- :func:`nfm_db.config.backup.check_retention_deprecation` — legacy
  ``retentionDays`` deprecation warning (NFM-3014).

All disk I/O uses pytest's ``tmp_path`` — no real ``statvfs`` reads. Disk state
is fed in as a :class:`DiskUsage` instance so the test runs identically on
macOS, Linux, and CI containers regardless of underlying free space.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from nfm_db.backup.config_loader import check_retention_deprecation
from nfm_db.backup.schema import BackupConfig, RetentionConfig, TierSpec
from nfm_db.backup.tier_engine import Tier
from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
    CapacityGuardrails,
    DiskUsage,
    FloorBreachEvent,
)
from nfm_db.services.backup.metrics import BackupMetrics
from nfm_db.services.backup.retention import (
    RetentionResult,
    apply_tiered_retention,
)

# Type aliases for readability — match the test's domain vocabulary.
BackupTier = Tier
TieredRetention = RetentionConfig
RetentionTier = TierSpec


# ---------------------------------------------------------------------------
# Test harness — mock a 50 GiB volume
# ---------------------------------------------------------------------------

_GIB = 1024**3
_TIER_SUFFIX = {
    ".hourly": BackupTier.HOURLY,
    ".daily": BackupTier.DAILY,
    ".weekly": BackupTier.WEEKLY,
}
_SUFFIX_BY_TIER = {tier: suffix for suffix, tier in _TIER_SUFFIX.items()}


class MockDisk:
    """In-memory disk simulation backed by ``tmp_path``.

    Tracks *free_bytes* (logical) and *total_backup_bytes* (sum of file sizes
    actually on disk). Tests mutate ``free_bytes`` directly to simulate
    changing conditions without invoking real ``statvfs``.
    """

    def __init__(self, *, total_bytes: int, free_bytes: int, root: Path) -> None:
        self.total_bytes = total_bytes
        self.free_bytes = free_bytes
        self._backup_dir = root / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def usage(self) -> DiskUsage:
        total_backup = sum(
            f.stat().st_size for f in self._backup_dir.iterdir() if f.is_file()
        )
        return DiskUsage(
            free_bytes=self.free_bytes, total_backup_bytes=total_backup
        )

    def write_snapshot(
        self,
        *,
        tier: BackupTier,
        size_bytes: int,
        mtime_offset_seconds: float = 0.0,
        counter: int = 0,
        prefix: str = "nucpot",
    ) -> Path:
        name = (
            f"{prefix}-{counter:04d}{_SUFFIX_BY_TIER[tier]}"
            f"-{int(mtime_offset_seconds):08d}.sql.gz"
        )
        path = self._backup_dir / name
        path.write_bytes(b"\x00" * size_bytes)
        if mtime_offset_seconds:
            ts = time.time() - mtime_offset_seconds
            os.utime(path, (ts, ts))
        # Decrement logical free space to reflect the write.
        self.free_bytes -= size_bytes
        return path

    def snapshot_count(self) -> int:
        return sum(1 for f in self._backup_dir.iterdir() if f.is_file())


@pytest.fixture
def mock_disk(tmp_path: Path) -> Iterator[MockDisk]:
    """50 GiB mock disk with 40 GiB initially free.

    The starting free space is calibrated so the test scenarios can write
    24 hourly + 7 daily + 4 weekly + 5 extras snapshots (40 total) of
    300 MiB each (12 GiB total) and still have plenty of room for the
    pipeline to settle without tripping the 20 GiB floor.
    """
    disk = MockDisk(
        total_bytes=50 * _GIB,
        free_bytes=40 * _GIB,
        root=tmp_path,
    )
    yield disk


# ---------------------------------------------------------------------------
# AC1 — Settle test: full pipeline reaches steady state
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlePipeline:
    """AC1: Generate enough backups to fill the disk, run the pipeline,
    verify steady-state invariants."""

    def test_full_pipeline_reaches_steady_state(self, mock_disk: MockDisk) -> None:
        # 1) Generate 40 snapshots (24 hourly + 7 daily + 4 weekly + 5 over).
        # Each snapshot is 300 MiB. Total initially: 40 x 300 MiB ≈ 11.72 GiB.
        snapshot_size = 300 * 1024 * 1024
        tiered = TieredRetention(
            hourly=RetentionTier(interval_minutes=60, count=24),
            daily=RetentionTier(interval_minutes=1440, count=7),
            weekly=RetentionTier(interval_minutes=10080, count=4),
        )

        counter = 0
        for hour in range(24):
            mock_disk.write_snapshot(
                tier=BackupTier.HOURLY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float(hour * 3600),
                counter=counter,
            )
            counter += 1
        for day in range(7):
            mock_disk.write_snapshot(
                tier=BackupTier.DAILY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float((24 + day) * 3600),
                counter=counter,
            )
            counter += 1
        for week in range(4):
            mock_disk.write_snapshot(
                tier=BackupTier.WEEKLY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float((31 + week * 7) * 86400),
                counter=counter,
            )
            counter += 1
        # 5 extras in the hourly tier to be pruned.
        for extra in range(5):
            mock_disk.write_snapshot(
                tier=BackupTier.HOURLY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float((36 + extra) * 86400),
                counter=counter,
            )
            counter += 1

        capacity_cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )
        metrics = BackupMetrics()
        guardrails = CapacityGuardrails(
            config=capacity_cfg, backup_dir=mock_disk.backup_dir, metrics=metrics
        )

        # 2) Pre-write floor check (simulating an incoming new backup of 800 MiB).
        floor_pre = guardrails.check_floor_before_write(
            backup_size=snapshot_size, disk=mock_disk.usage()
        )
        assert floor_pre is None, "Floor should not be breached with 30 GiB free"

        # 3) Apply tiered retention — prunes excess per-tier snapshots.
        result: RetentionResult = apply_tiered_retention(
            backup_dir=mock_disk.backup_dir, retention=tiered
        )

        # 4) Post-write cap enforcement — prunes oldest until total ≤ 12 GiB.
        _pruned_by_cap = guardrails.enforce_cap_after_write(
            disk=mock_disk.usage()
        )

        # 5) Post-pruner floor recheck — free space MUST stay ≥ 20 GiB.
        recheck = guardrails.recheck_floor_after_pruner(disk=mock_disk.usage())
        assert recheck is None, (
            f"Post-pruner floor breach should be impossible; got {recheck}"
        )

        # -- Invariants ------------------------------------------------------
        by_tier = _count_by_tier(mock_disk.backup_dir)
        assert by_tier[BackupTier.HOURLY] == 24
        assert by_tier[BackupTier.DAILY] == 7
        assert by_tier[BackupTier.WEEKLY] == 4

        # Total ≤ 12 GiB (maxTotalBytes cap).
        total_bytes = mock_disk.usage().total_backup_bytes
        assert total_bytes <= 12 * _GIB, (
            f"Total {total_bytes} exceeds cap {12 * _GIB}"
        )

        # No refusals during the healthy settle path.
        assert guardrails.metrics.refusal_count == 0, (
            "No refusals expected during settle, got "
            f"{guardrails.metrics.refusal_count}"
        )

        # The tier-aware pruner pruned at least the 5 hourly extras.
        assert result.pruned_count >= 5


# ---------------------------------------------------------------------------
# AC2 — Floor breach refuses the write with [SRE-WARNING]
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFloorBreachIntegration:
    """AC2: free=19 GiB → backup is refused + SRE-WARNING."""

    def test_floor_breach_refuses_with_sre_warning(
        self, mock_disk: MockDisk, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_disk.free_bytes = 19 * _GIB
        capacity_cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )
        metrics = BackupMetrics()
        guardrails = CapacityGuardrails(
            config=capacity_cfg, backup_dir=mock_disk.backup_dir, metrics=metrics
        )

        backup_size = 1 * _GIB
        with caplog.at_level(
            logging.WARNING, logger="nfm_db.services.backup.guardrails"
        ):
            event = guardrails.check_floor_before_write(
                backup_size=backup_size, disk=mock_disk.usage()
            )

        assert event is not None
        assert isinstance(event, FloorBreachEvent)
        assert event.free_bytes == 19 * _GIB
        assert event.floor == 20 * _GIB

        # The metric was recorded for SRE alerting.
        assert guardrails.metrics.refusal_count == 1
        assert guardrails.metrics.last_refusal_at is not None

        # The SRE-WARNING log line is emitted (matches NFM-2915 alert).
        assert "[SRE-WARNING]" in caplog.text
        assert "floor breach" in caplog.text


# ---------------------------------------------------------------------------
# AC3 — Cap overflow prunes down to ≤ 12 GiB
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCapOverflowIntegration:
    """AC3: inject 15 GiB, run prune, total ≤ 12 GiB."""

    def test_fifteen_gib_injected_then_pruned_to_twelve(
        self, mock_disk: MockDisk
    ) -> None:
        snapshot_size = 1 * _GIB
        for i in range(15):
            mock_disk.write_snapshot(
                tier=BackupTier.DAILY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float((15 - i) * 3600),
                counter=i,
            )
        assert mock_disk.usage().total_backup_bytes == 15 * _GIB

        capacity_cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )
        guardrails = CapacityGuardrails(
            config=capacity_cfg, backup_dir=mock_disk.backup_dir
        )

        pruned = guardrails.enforce_cap_after_write(disk=mock_disk.usage())

        # At least 3 snapshots pruned (15 → 12).
        assert len(pruned) >= 3

        new_total = mock_disk.usage().total_backup_bytes
        assert new_total <= 12 * _GIB, (
            f"Total {new_total} should be ≤ 12 GiB after prune"
        )


# ---------------------------------------------------------------------------
# AC4 — Legacy retentionDays deprecation warning still works
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLegacyRetentionMigration:
    """AC4: Config with only ``retention_days`` produces a [DEPRECATION]
    warning at startup and the resulting config still functions."""

    def test_legacy_retention_days_emits_deprecation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = BackupConfig(
            retention=None,
            retention_days=7,
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )

        with caplog.at_level(
            logging.WARNING, logger="nfm_db.backup.config_loader"
        ):
            check_retention_deprecation(cfg)

        assert "[DEPRECATION]" in caplog.text
        assert "retentionDays" in caplog.text
        assert "hourly" in caplog.text

    def test_legacy_config_still_constructs_capacity_guardrails(
        self, mock_disk: MockDisk
    ) -> None:
        cfg = BackupConfig(
            retention=None,
            retention_days=7,
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )
        capacity_cfg = BackupCapacityConfig(
            max_total_bytes=cfg.max_total_bytes,
            min_free_bytes=cfg.min_free_bytes,
            refuse_on_floor_breach=cfg.refuse_on_floor_breach,
        )
        guardrails = CapacityGuardrails(
            config=capacity_cfg, backup_dir=mock_disk.backup_dir
        )
        assert guardrails.config.max_total_bytes == 12 * _GIB
        assert guardrails.config.min_free_bytes == 20 * _GIB

    def test_no_deprecation_when_tiered_retention_present(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = BackupConfig(
            retention=TieredRetention(),  # explicit tiered config
            retention_days=None,
        )
        with caplog.at_level(
            logging.WARNING, logger="nfm_db.backup.config_loader"
        ):
            check_retention_deprecation(cfg)
        assert "[DEPRECATION]" not in caplog.text


# ---------------------------------------------------------------------------
# AC5 — End-to-end: every guardrail fires in correct order
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEndToEndPipelineOrder:
    """AC5: floor check → tier prune → cap prune → recheck, in order."""

    def test_floor_then_tier_then_cap_then_recheck(
        self,
        mock_disk: MockDisk,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Use a small snapshot size so 30 x 300 MiB = 9 GiB total stays
        # well under the cap and well above the floor.
        snapshot_size = 300 * 1024 * 1024  # 300 MiB
        tiered = TieredRetention()

        # Write 30 hourly snapshots — above the 24-count cap.
        for i in range(30):
            mock_disk.write_snapshot(
                tier=BackupTier.HOURLY,
                size_bytes=snapshot_size,
                mtime_offset_seconds=float((30 - i) * 3600),
                counter=i,
            )

        capacity_cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            refuse_on_floor_breach=True,
        )
        metrics = BackupMetrics()
        guardrails = CapacityGuardrails(
            config=capacity_cfg, backup_dir=mock_disk.backup_dir, metrics=metrics
        )

        # Step 1: floor check (free is still well above 20 GiB).
        floor_pre = guardrails.check_floor_before_write(
            backup_size=snapshot_size, disk=mock_disk.usage()
        )
        assert floor_pre is None

        # Step 2: apply tier retention — drop to 24 hourly.
        result = apply_tiered_retention(
            backup_dir=mock_disk.backup_dir, retention=tiered
        )
        assert result.pruned_count == 6

        # Step 3: cap enforcement — total = 24 * 300 MiB ≈ 7.03 GiB, OK.
        guardrails.enforce_cap_after_write(disk=mock_disk.usage())

        # Step 4: post-pruner recheck.
        with caplog.at_level(
            logging.WARNING, logger="nfm_db.services.backup.guardrails"
        ):
            recheck = guardrails.recheck_floor_after_pruner(disk=mock_disk.usage())

        assert recheck is None
        assert guardrails.metrics.refusal_count == 0
        assert "Post-pruner floor breach" not in caplog.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_by_tier(backup_dir: Path) -> dict[BackupTier, int]:
    counts = {tier: 0 for tier in BackupTier}
    for entry in backup_dir.iterdir():
        if not entry.is_file():
            continue
        tier = _tier_from_filename(entry.name)
        if tier is not None:
            counts[tier] += 1
    return counts


def _tier_from_filename(filename: str) -> BackupTier | None:
    lower = filename.lower()
    for suffix, tier in _TIER_SUFFIX.items():
        if suffix in lower:
            return tier
    return None
