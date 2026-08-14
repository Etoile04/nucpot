"""Tests for CapacityGuardrails — the core NFM-3016 acceptance criteria.

AC coverage:
- [x] Writing a backup that would breach minFreeBytes is refused with [SRE-WARNING]
- [x] maxTotalBytes cap triggers pruner until total <= cap
- [x] Cap and floor are checked AFTER pruner run, not just before write
- [x] refusalCount and lastRefusalAt are tracked
- [x] Unit test: simulated run that would breach floor produces [SRE-WARNING]
- [x] refuseOnFloorBreach=false disables the floor check
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
    BackupEntry,
    CapacityGuardrails,
    DiskUsage,
    FloorBreachEvent,
)
from nfm_db.services.backup.metrics import BackupMetrics

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIB = 1024**3


def _make_config(
    *,
    max_total: int = 12 * _GIB,
    min_free: int = 20 * _GIB,
    refuse_on_floor: bool = True,
) -> BackupCapacityConfig:
    return BackupCapacityConfig(
        max_total_bytes=max_total,
        min_free_bytes=min_free,
        refuse_on_floor_breach=refuse_on_floor,
    )


def _make_disk(*, free: int, total_backup: int) -> DiskUsage:
    return DiskUsage(free_bytes=free, total_backup_bytes=total_backup)


# ---------------------------------------------------------------------------
# AC: Floor breach refusal with SRE-WARNING
# ---------------------------------------------------------------------------


class TestFloorBreachRefusal:
    """AC: Writing a backup that would breach minFreeBytes is refused."""

    def test_write_permitted_when_enough_free(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=10 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=30 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert result is None
        assert metrics.refusal_count == 0

    def test_write_refused_when_floor_breached(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert result is not None
        assert isinstance(result, FloorBreachEvent)
        assert result.free_bytes == 22 * _GIB
        assert result.backup_size == 5 * _GIB
        assert result.floor == 20 * _GIB
        assert metrics.refusal_count == 1

    def test_sre_warning_logged_on_refusal(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert "[SRE-WARNING]" in caplog.text
        assert "floor breach" in caplog.text

    def test_exact_boundary_is_permitted(self, tmp_path: Path) -> None:
        """free - backup_size == floor is NOT a breach."""
        cfg = _make_config(min_free=20 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=25 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert result is None

    def test_one_byte_over_is_refused(self, tmp_path: Path) -> None:
        """free - backup_size == floor - 1 IS a breach."""
        cfg = _make_config(min_free=20 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=25 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB + 1, disk=disk)
        assert result is not None


# ---------------------------------------------------------------------------
# AC: refuseOnFloorBreach=false disables the floor check
# ---------------------------------------------------------------------------


class TestRefuseOnFloorDisabled:
    def test_floor_disabled_allows_write(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB, refuse_on_floor=False)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=1, total_backup=0)

        result = gr.check_floor_before_write(backup_size=1, disk=disk)
        assert result is None
        assert metrics.refusal_count == 0

    def test_post_pruner_floor_disabled(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB, refuse_on_floor=False)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=1, total_backup=0)

        result = gr.recheck_floor_after_pruner(disk=disk)
        assert result is None


# ---------------------------------------------------------------------------
# AC: maxTotalBytes cap triggers pruner
# ---------------------------------------------------------------------------


class TestCapEnforcement:
    def test_no_pruning_when_under_cap(self, tmp_path: Path) -> None:
        cfg = _make_config(max_total=10 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=100 * _GIB, total_backup=8 * _GIB)

        pruned = gr.enforce_cap_after_write(disk=disk)
        assert pruned == []

    def test_pruning_when_over_cap(self, tmp_path: Path) -> None:
        """Oldest backups are pruned first until total <= cap."""
        cfg = _make_config(max_total=10 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)

        # Create 3 backup files: oldest 5 GiB, middle 4 GiB, newest 3 GiB = 12 GiB total
        oldest = tmp_path / "backup_oldest.sql.gz"
        middle = tmp_path / "backup_middle.sql.gz"
        newest = tmp_path / "backup_newest.sql.gz"

        oldest.write_bytes(b"x" * (5 * _GIB))
        time.sleep(0.01)
        middle.write_bytes(b"x" * (4 * _GIB))
        time.sleep(0.01)
        newest.write_bytes(b"x" * (3 * _GIB))

        disk = _make_disk(free=100 * _GIB, total_backup=12 * _GIB)
        pruned = gr.enforce_cap_after_write(disk=disk)

        assert len(pruned) >= 1
        assert oldest in pruned
        assert not oldest.exists()
        # After pruning oldest (5 GiB), total is 7 GiB <= 10 GiB cap
        assert sum(f.stat().st_size for f in tmp_path.iterdir() if f.is_file()) <= 10 * _GIB

    def test_empty_backup_dir_no_error(self, tmp_path: Path) -> None:
        cfg = _make_config(max_total=10 * _GIB)
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path / "nonexistent")
        disk = _make_disk(free=100 * _GIB, total_backup=15 * _GIB)

        pruned = gr.enforce_cap_after_write(disk=disk)
        assert pruned == []


# ---------------------------------------------------------------------------
# AC: Cap and floor checked AFTER pruner run
# ---------------------------------------------------------------------------


class TestPostPrunerFloorRecheck:
    def test_floor_ok_after_pruner(self, tmp_path: Path) -> None:
        """After pruning, if free space is sufficient, no refusal."""
        cfg = _make_config(max_total=10 * _GIB, min_free=15 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=25 * _GIB, total_backup=8 * _GIB)

        gr.enforce_cap_after_write(disk=disk)
        result = gr.recheck_floor_after_pruner(disk=disk)
        assert result is None

    def test_floor_breach_after_pruner_emits_sre_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC: stale snapshot that pushed free to 0 must not be tolerated."""
        cfg = _make_config(max_total=10 * _GIB, min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)

        # Simulate: pruner ran, but free space is still below floor
        disk = _make_disk(free=5 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            result = gr.recheck_floor_after_pruner(disk=disk)

        assert result is not None
        assert isinstance(result, FloorBreachEvent)
        assert result.backup_size == 0
        assert "[SRE-WARNING]" in caplog.text
        assert "Post-pruner floor breach" in caplog.text
        assert metrics.refusal_count == 1

    def test_full_sequence_write_prune_recheck(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """End-to-end: write permitted -> cap triggers prune -> floor recheck passes."""
        cfg = _make_config(max_total=10 * _GIB, min_free=15 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)

        # 1. Pre-write floor check: 30 GiB free, writing 5 GiB -> 25 GiB free >= 15 GiB floor
        disk_before = _make_disk(free=30 * _GIB, total_backup=5 * _GIB)
        floor_result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk_before)
        assert floor_result is None

        # 2. Post-write cap check: total now 10 GiB = cap, no pruning needed
        disk_after_write = _make_disk(free=25 * _GIB, total_backup=10 * _GIB)
        pruned = gr.enforce_cap_after_write(disk=disk_after_write)
        assert pruned == []

        # 3. Post-pruner floor recheck: 25 GiB free >= 15 GiB floor
        recheck = gr.recheck_floor_after_pruner(disk=disk_after_write)
        assert recheck is None
        assert metrics.refusal_count == 0


# ---------------------------------------------------------------------------
# AC: refusalCount and lastRefusalAt are tracked
# ---------------------------------------------------------------------------


class TestRefusalMetricsTracking:
    def test_refusal_count_increments_on_breach(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert metrics.refusal_count == 1

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert metrics.refusal_count == 2

    def test_last_refusal_at_updated(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        assert metrics.last_refusal_at is None
        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        assert metrics.last_refusal_at is not None

    def test_snapshot_accessible_via_metrics(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        snap = gr.metrics.snapshot()
        assert snap.refusal_count == 1
        assert snap.last_refusal_at is not None


# ---------------------------------------------------------------------------
# Data class immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_disk_usage_frozen(self) -> None:
        d = DiskUsage(free_bytes=100, total_backup_bytes=200)
        with pytest.raises(AttributeError):
            d.free_bytes = 0  # type: ignore[misc]

    def test_floor_breach_event_frozen(self) -> None:
        e = FloorBreachEvent(
            free_bytes=1,
            backup_size=2,
            floor=3,
            refused_at=datetime.now(UTC),
            capacity_total_bytes=4,
        )
        with pytest.raises(AttributeError):
            e.free_bytes = 0  # type: ignore[misc]

    def test_backup_entry_frozen(self) -> None:
        e = BackupEntry(path=Path("/x"), size_bytes=1, modified_at=0.0)
        with pytest.raises(AttributeError):
            e.size_bytes = 0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Guardrails → SRE alert emitter integration (NFM-3053)
# ---------------------------------------------------------------------------


class TestGuardrailsAlertEmitterIntegration:
    """Exercises the alert_emitter property and _make_alert_emitter factory
    that live on CapacityGuardrails (NFM-3053 CR suggestion).
    """

    def test_floor_breach_calls_alert_emitter(self, tmp_path: Path) -> None:
        """check_floor_before_write drives alert_emitter.on_refusal on breach."""
        from unittest.mock import MagicMock

        mock_emitter = MagicMock()
        mock_emitter.on_refusal.return_value = None

        cfg = _make_config(
            min_free=20 * _GIB,
        )
        gr = CapacityGuardrails(
            config=cfg,
            backup_dir=tmp_path,
            alert_emitter=mock_emitter,
        )
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        mock_emitter.on_refusal.assert_called_once()
        call_kwargs = mock_emitter.on_refusal.call_args[1]
        assert call_kwargs["free_bytes"] == 22 * _GIB
        assert call_kwargs["total_bytes"] == 0
        assert call_kwargs["min_free_bytes"] == 20 * _GIB
        assert call_kwargs["max_total_bytes"] == 12 * _GIB
        assert call_kwargs["refusal_count"] == 1
        assert call_kwargs["last_refusal_at"] is not None

    def test_make_alert_emitter_uses_config_debounce(self, tmp_path: Path) -> None:
        """_make_alert_emitter reads push_debounce_seconds from config."""
        cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            push_on_refusal=True,
            push_debounce_seconds=1800,
        )
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)

        assert gr.alert_emitter.push_on_refusal is True
        assert gr.alert_emitter.debounce_seconds == 1800

    def test_make_alert_emitter_disabled(self, tmp_path: Path) -> None:
        """When push_on_refusal=False, the emitter suppresses pushes."""
        cfg = BackupCapacityConfig(
            max_total_bytes=12 * _GIB,
            min_free_bytes=20 * _GIB,
            push_on_refusal=False,
        )
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)

        assert gr.alert_emitter.push_on_refusal is False
