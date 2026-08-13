"""Focused refusal-path tests for capacity-floor breach (NFM-3057 / NFM-3024-F2).

Complements the existing test_guardrails.py (which covers basic refusal, cap
enforcement, and immutability). This file adds:

- AC1: Explicit ``refused=True`` contract — the FloorBreachEvent fields
  (free_bytes, backup_size, floor) constitute a structured reason code
  that uniquely identifies a capacity-floor breach.
- AC2: refusalCount increments by exactly 1 and lastRefusalAt is set,
  verified with a **frozen clock** via ``freezegun`` for determinism.
- AC3: Two consecutive refusals produce refusalCount=2 and lastRefusalAt
  reflecting the **second** call (overwrite-not-append).
- AC4: ≥80% line coverage on the refusal code-path.
- AC5: pytest + tmp_path + freezegun.freeze_time conventions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from freezegun import freeze_time

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
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
    push_on_refusal: bool = True,
) -> BackupCapacityConfig:
    return BackupCapacityConfig(
        max_total_bytes=max_total,
        min_free_bytes=min_free,
        refuse_on_floor_breach=refuse_on_floor,
        push_on_refusal=push_on_refusal,
    )


def _make_disk(*, free: int, total_backup: int) -> DiskUsage:
    return DiskUsage(free_bytes=free, total_backup_bytes=total_backup)


# ---------------------------------------------------------------------------
# AC1: FloorBreachEvent is the structured refusal contract
# ---------------------------------------------------------------------------


class TestRefusalContract:
    """AC1: A refusal returns a FloorBreachEvent whose fields constitute
    a structured reason code identifying the floor breach."""

    def test_refusal_event_is_not_none_means_refused(self, tmp_path: Path) -> None:
        """Non-None FloorBreachEvent ≡ refused=True (the spec's boolean contract)."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        # The refusal contract: a FloorBreachEvent is the refusal signal.
        # "refused=True" in the spec maps to "result is not None and is FloorBreachEvent".
        assert result is not None, "Expected a FloorBreachEvent (refused=True)"
        assert isinstance(result, FloorBreachEvent)

    def test_refusal_event_fields_identify_floor_breach(self, tmp_path: Path) -> None:
        """The event's free_bytes, backup_size, and floor fields encode the
        structured reason: 'projected free space would cross the 20 GiB floor'."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        free = 22 * _GIB
        backup_size = 5 * _GIB
        floor = 20 * _GIB
        disk = _make_disk(free=free, total_backup=0)

        result = gr.check_floor_before_write(backup_size=backup_size, disk=disk)

        assert result is not None
        # Structured reason code fields:
        assert result.free_bytes == free
        assert result.backup_size == backup_size
        assert result.floor == floor
        # The breach is derivable: free - backup_size < floor → 17 GiB < 20 GiB
        assert result.free_bytes - result.backup_size < result.floor

    def test_no_refusal_returns_none(self, tmp_path: Path) -> None:
        """When the write is permitted, None is returned (refused=False)."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=30 * _GIB, total_backup=0)

        result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert result is None, "Expected None (refused=False)"

    def test_post_pruner_refusal_also_returns_structured_event(
        self, tmp_path: Path,
    ) -> None:
        """Post-pruner floor breach returns the same FloorBreachEvent contract
        with backup_size=0 (no write attempted after pruning)."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=5 * _GIB, total_backup=0)

        result = gr.recheck_floor_after_pruner(disk=disk)

        assert isinstance(result, FloorBreachEvent)
        assert result.backup_size == 0
        assert result.floor == 20 * _GIB
        assert result.free_bytes < result.floor


# ---------------------------------------------------------------------------
# AC2: refusalCount increments by exactly 1, lastRefusalAt via frozen clock
# ---------------------------------------------------------------------------


class TestRefusalCountAndClock:
    """AC2: A successful refusal increments refusalCount by exactly 1 and
    sets lastRefusalAt to the current clock (frozen via freezegun)."""

    @freeze_time("2026-08-13T12:00:00Z")
    def test_single_refusal_count_and_timestamp(self, tmp_path: Path) -> None:
        """First refusal: count=1, lastRefusalAt matches frozen clock."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 1
        assert metrics.last_refusal_at == datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)

    @freeze_time("2026-08-13T12:00:00Z")
    def test_event_refused_at_matches_frozen_clock(self, tmp_path: Path) -> None:
        """The FloorBreachEvent.refused_at also reflects the frozen clock."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        event = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert event is not None
        assert event.refused_at == datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)

    @freeze_time("2026-08-13T12:00:00Z")
    def test_refusal_does_not_increment_when_permitted(self, tmp_path: Path) -> None:
        """When the write is allowed, refusalCount stays at 0."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=30 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 0
        assert metrics.last_refusal_at is None


# ---------------------------------------------------------------------------
# AC3: Two consecutive refusals — overwrite-not-append
# ---------------------------------------------------------------------------


class TestConsecutiveRefusalsOverwrite:
    """AC3: Two consecutive refusals produce refusalCount=2 and lastRefusalAt
    reflecting the SECOND call (proves overwrite-not-append)."""

    @freeze_time("2026-08-13T12:00:00Z")
    def test_first_refusal_sets_initial_state(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        event1 = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert event1 is not None
        assert metrics.refusal_count == 1
        assert metrics.last_refusal_at == datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)

    @freeze_time("2026-08-13T12:00:30Z")
    def test_second_refusal_overwrites_last_refusal_at(self, tmp_path: Path) -> None:
        """Advance the clock by 30s and trigger a second refusal.
        lastRefusalAt must reflect the second call, not the first."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        # First refusal at T+0s (frozen at 12:00:00)
        with freeze_time("2026-08-13T12:00:00Z"):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 1
        assert metrics.last_refusal_at == datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)

        # Second refusal at T+30s (frozen at 12:00:30)
        with freeze_time("2026-08-13T12:00:30Z"):
            event2 = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 2
        # Overwrite-not-append: lastRefusalAt is the SECOND call's time.
        assert metrics.last_refusal_at == datetime(2026, 8, 13, 12, 0, 30, tzinfo=UTC)
        assert event2 is not None
        assert event2.refused_at == datetime(2026, 8, 13, 12, 0, 30, tzinfo=UTC)

    @freeze_time("2026-08-13T12:00:00Z")
    def test_post_pruner_refusal_also_overwrites(self, tmp_path: Path) -> None:
        """Mixing pre-write and post-pruner refusals: both increment count
        and lastRefusalAt reflects the most recent refusal."""
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        # First refusal at T+0s (pre-write)
        with freeze_time("2026-08-13T12:00:00Z"):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 1

        # Second refusal at T+60s (post-pruner, different trigger)
        low_disk = _make_disk(free=5 * _GIB, total_backup=0)
        with freeze_time("2026-08-13T12:01:00Z"):
            event2 = gr.recheck_floor_after_pruner(disk=low_disk)

        assert metrics.refusal_count == 2
        assert metrics.last_refusal_at == datetime(2026, 8, 13, 12, 1, 0, tzinfo=UTC)
        assert event2 is not None
        assert event2.refused_at == datetime(2026, 8, 13, 12, 1, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# RFC-3339 format on snapshot (Amendment 5, ADR D4)
# ---------------------------------------------------------------------------


class TestRefusalSnapshotRFC3339:
    """Verify the snapshot's RFC-3339 string matches the frozen clock."""

    @freeze_time("2026-08-13T07:18:59.123Z")
    def test_snapshot_rfc3339_after_refusal(self, tmp_path: Path) -> None:
        cfg = _make_config(min_free=20 * _GIB)
        metrics = BackupMetrics()
        gr = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics,
        )
        disk = _make_disk(free=21 * _GIB, total_backup=0)

        gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)
        snap = metrics.snapshot()

        assert snap.refusal_count == 1
        assert snap.last_refusal_at_rfc3339 == "2026-08-13T07:18:59.123Z"
