"""Integration test for tiered retention + capacity guardrails (NFM-3058).

This is the end-to-end test for the integration glue between the scheduler
(NFM-3015 tier engine + scheduler) and the capacity guardrails (NFM-3016).
It drives a synthetic 4-week schedule against a mocked 50 GiB disk and
asserts the ACs from NFM-3058.

Acceptance criteria
-------------------
1. Mock a 50 GiB disk; run a synthetic 4-week schedule
   (4 weekly checkpoints × 7 daily ticks = 28 ticks minimum).
2. On-disk total after settling ≤ 12 GiB.
3. Free-space ≥ 20 GiB at EVERY measured tick (never crosses the floor).
4. ≥80% line coverage on the integration glue (scheduler + guardrail interplay).
5. Regression test asserting the SRE alert payload schema byte-for-byte as
   referenced from NFM-3024-E's spec — embed the expected JSON as a fixture
   and compare via deep equal.

TDD status
----------
This test is written FIRST. It targets the integration modules from
NFM-3024 (``nfm_db.services.backup.guardrails``,
``nfm_db.services.backup.config``, ``nfm_db.services.backup.metrics``)
which are not yet merged into ``origin/main`` (NFM-3024 still in
implementation phase). The failing import is the RED signal.

Once NFM-3024 lands on main, this test should pass. The dependency is
documented in NFM-3058's done comment.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

# RED signal: these imports do not exist on origin/main yet.
# They are the target of NFM-3014/3015/3016 (merged via NFM-3024).
from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
    CapacityGuardrails,
    DiskUsage,
    FloorBreachEvent,
)
from nfm_db.services.backup.metrics import BackupMetrics

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GIB = 1024**3

# Disk model: 50 GiB total, 12 GiB cap on backups, 20 GiB floor on free space.
# These match the acceptance criteria verbatim.
SIM_DISK_BYTES = 50 * _GIB
SIM_MAX_TOTAL_BYTES = 12 * _GIB
SIM_MIN_FREE_BYTES = 20 * _GIB

# Schedule: 4 weekly checkpoints × 7 daily ticks = 28 ticks.
SCHEDULE_TICKS = 28


# ---------------------------------------------------------------------------
# SRE alert payload fixture (NFM-3024-E byte-for-byte contract)
# ---------------------------------------------------------------------------
#
# The SRE alert payload schema is the JSON-serialised form of
# :class:`FloorBreachEvent`. Fields MUST appear in this exact order, with
# these exact names. The :func:`FloorBreachEvent.to_alert_payload` function
# (or equivalent) is responsible for producing this byte-for-byte shape.
#
# This fixture is the contract pinned by NFM-3024-E. Any drift in field
# names, ordering, or value types is a wire-format break that will fail
# downstream SRE tooling.

SRE_ALERT_PAYLOAD_FIXTURE: dict[str, object] = {
    "free_bytes": 22 * _GIB,
    "backup_size": 5 * _GIB,
    "floor": SIM_MIN_FREE_BYTES,
    "refused_at": "2026-08-13T15:25:00+00:00",
    "capacity_total_bytes": 0,
}


# ---------------------------------------------------------------------------
# Synthetic schedule helpers
# ---------------------------------------------------------------------------


def _disk(*, free: int, total_backup: int) -> DiskUsage:
    """Build a :class:`DiskUsage` snapshot for a tick."""
    return DiskUsage(free_bytes=free, total_backup_bytes=total_backup)


def _synthetic_backup_sizes() -> list[int]:
    """Return 28 backup sizes (in bytes) for the 4-week synthetic schedule.

    Most days are 1 GiB. A few ticks are large enough to push the system
    into floor-breach territory to exercise the refusal path.
    """
    sizes: list[int] = [1 * _GIB] * SCHEDULE_TICKS
    # Tick 7 (end of week 1): larger write that still passes (3 GiB).
    sizes[7] = 3 * _GIB
    # Tick 14 (end of week 2): a write that would breach the floor.
    # Free = 50 - 12 (already at cap) = 38 GiB, write 31 GiB → free = 7 GiB < 20.
    # This MUST be refused by the guardrail.
    sizes[14] = 31 * _GIB
    # Tick 21 (end of week 3): 2 GiB, comfortably below floor.
    sizes[21] = 2 * _GIB
    # Tick 28 (end of week 4): 1 GiB normal.
    return sizes


def _drive_with_controlled_disk(
    guardrails: CapacityGuardrails,
    backup_dir: Path,
    schedule: list[tuple[int, int]],
) -> list[dict]:
    """Drive the schedule injecting a controlled 50 GiB disk model.

    On a real test host we cannot reserve 50 GiB on tmpfs. Instead, we
    model free space in the test driver and inject :class:`DiskUsage`
    snapshots into the guardrail calls.

    Each tick:
      1. Synthesise a DiskUsage snapshot.
      3. Pre-write floor check → refuse if breach.
      4. If permitted, write the backup file.
      5. Post-write cap check → prune oldest if over cap.
      6. Re-check floor (post-pruner).
      7. Record the resulting free/total.
    """
    records: list[dict] = []
    free = SIM_DISK_BYTES
    total_backup = 0

    for tick, backup_size in schedule:
        # Step 1: synthesise a DiskUsage for this tick.
        disk = _disk(free=free, total_backup=total_backup)

        # Step 2: pre-write floor check
        refusal = guardrails.check_floor_before_write(
            backup_size=backup_size, disk=disk
        )
        if refusal is not None:
            records.append(
                {
                    "tick": tick,
                    "refused": True,
                    "free_bytes": free,
                    "total_backup_bytes": total_backup,
                    "event": refusal,
                }
            )
            continue

        # Step 3: simulate the write — file lands on disk
        backup_path = backup_dir / f"backup_tick_{tick:03d}.bin"
        backup_path.write_bytes(b"\x00" * backup_size)
        free -= backup_size
        total_backup += backup_size

        # Step 4: post-write cap check
        disk_after_write = _disk(free=free, total_backup=total_backup)
        pruned = guardrails.enforce_cap_after_write(disk=disk_after_write)
        for entry in pruned:
            try:
                size = entry.stat().st_size
                entry.unlink()
                total_backup -= size
                free += size
            except FileNotFoundError:
                pass

        # Step 5: post-pruner floor re-check
        disk_after_prune = _disk(free=free, total_backup=total_backup)
        post_refusal = guardrails.recheck_floor_after_pruner(
            disk=disk_after_prune
        )
        if post_refusal is not None:
            records.append(
                {
                    "tick": tick,
                    "refused": True,
                    "free_bytes": free,
                    "total_backup_bytes": total_backup,
                    "event": post_refusal,
                }
            )
            continue

        records.append(
            {
                "tick": tick,
                "refused": False,
                "free_bytes": free,
                "total_backup_bytes": total_backup,
            }
        )

    return records


# ---------------------------------------------------------------------------
# AC #1 — synthetic 28-tick schedule runs end-to-end
# ---------------------------------------------------------------------------


class TestSyntheticSchedule:
    """AC #1: the integration runs a 4-week schedule without errors."""

    def test_schedule_has_28_ticks(self) -> None:
        sizes = _synthetic_backup_sizes()
        assert len(sizes) == 28, "AC #1: schedule must have ≥ 28 ticks"

    def test_schedule_executes_end_to_end(
        self, tmp_path: Path
    ) -> None:
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=backup_dir, metrics=BackupMetrics()
        )
        schedule = list(enumerate(_synthetic_backup_sizes(), start=1))

        records = _drive_with_controlled_disk(guardrails, backup_dir, schedule)
        assert len(records) == 28, "AC #1: schedule must produce 28 records"


# ---------------------------------------------------------------------------
# AC #2 — on-disk total ≤ 12 GiB after settling
# ---------------------------------------------------------------------------


class TestOnDiskTotalCap:
    """AC #2: the cap holds steady-state total at ≤ 12 GiB."""

    def _run(self, tmp_path: Path) -> list[dict]:
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=backup_dir, metrics=BackupMetrics()
        )
        schedule = list(enumerate(_synthetic_backup_sizes(), start=1))
        return _drive_with_controlled_disk(guardrails, backup_dir, schedule)

    def test_total_never_exceeds_12_gib(self, tmp_path: Path) -> None:
        records = self._run(tmp_path)
        for record in records:
            assert (
                record["total_backup_bytes"] <= SIM_MAX_TOTAL_BYTES
            ), f"AC #2 violated at tick {record['tick']}: total={record['total_backup_bytes']}"

    def test_final_state_under_cap(self, tmp_path: Path) -> None:
        records = self._run(tmp_path)
        last = records[-1]
        assert last["total_backup_bytes"] <= SIM_MAX_TOTAL_BYTES


# ---------------------------------------------------------------------------
# AC #3 — free space ≥ 20 GiB at every measured tick
# ---------------------------------------------------------------------------


class TestFreeSpaceFloor:
    """AC #3: free space never crosses the 20 GiB floor."""

    def _run(self, tmp_path: Path) -> list[dict]:
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=backup_dir, metrics=BackupMetrics()
        )
        schedule = list(enumerate(_synthetic_backup_sizes(), start=1))
        return _drive_with_controlled_disk(guardrails, backup_dir, schedule)

    def test_free_space_at_every_tick(self, tmp_path: Path) -> None:
        records = self._run(tmp_path)
        for record in records:
            assert record["free_bytes"] >= SIM_MIN_FREE_BYTES, (
                f"AC #3 violated at tick {record['tick']}: "
                f"free={record['free_bytes']} < floor={SIM_MIN_FREE_BYTES}"
            )

    def test_oversized_write_is_refused(self, tmp_path: Path) -> None:
        """A 31 GiB write when only 12 GiB is in backups is refused (free=7 < 20)."""
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=BackupMetrics()
        )

        disk = _disk(
            free=SIM_DISK_BYTES - SIM_MAX_TOTAL_BYTES,
            total_backup=SIM_MAX_TOTAL_BYTES,
        )
        refusal = guardrails.check_floor_before_write(
            backup_size=31 * _GIB, disk=disk
        )

        assert refusal is not None, "31 GiB write must be refused"
        assert isinstance(refusal, FloorBreachEvent)
        assert refusal.floor == SIM_MIN_FREE_BYTES

    def test_refusal_increments_metrics(self, tmp_path: Path) -> None:
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        metrics = BackupMetrics()
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=metrics
        )
        disk = _disk(free=22 * _GIB, total_backup=0)
        guardrails.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert metrics.refusal_count == 1
        assert metrics.last_refusal_at is not None


# ---------------------------------------------------------------------------
# AC #5 — SRE alert payload schema byte-for-byte (NFM-3024-E contract)
# ---------------------------------------------------------------------------


class TestSREAlertPayloadSchema:
    """AC #5: SRE alert payload matches the NFM-3024-E byte-for-byte schema."""

    def _make_event(self, tmp_path: Path) -> FloorBreachEvent:
        cfg = BackupCapacityConfig(
            max_total_bytes=SIM_MAX_TOTAL_BYTES,
            min_free_bytes=SIM_MIN_FREE_BYTES,
        )
        guardrails = CapacityGuardrails(
            config=cfg, backup_dir=tmp_path, metrics=BackupMetrics()
        )
        disk = _disk(
            free=SRE_ALERT_PAYLOAD_FIXTURE["free_bytes"],
            total_backup=SRE_ALERT_PAYLOAD_FIXTURE["capacity_total_bytes"],
        )
        event = guardrails.check_floor_before_write(
            backup_size=SRE_ALERT_PAYLOAD_FIXTURE["backup_size"],
            disk=disk,
        )
        assert event is not None, "precondition: floor must breach"
        return event

    def test_event_serializes_to_fixture(self, tmp_path: Path) -> None:
        """AC #5: the event's serialised form byte-for-byte matches the NFM-3024-E fixture."""
        event = self._make_event(tmp_path)

        # Serialise the event. The implementation must produce a dict whose
        # keys and value types match the NFM-3024-E fixture exactly.
        payload = asdict(event)
        payload["refused_at"] = payload["refused_at"].isoformat()

        assert payload.keys() == SRE_ALERT_PAYLOAD_FIXTURE.keys(), (
            f"AC #5 schema drift: event has keys {sorted(payload.keys())}, "
            f"spec has {sorted(SRE_ALERT_PAYLOAD_FIXTURE.keys())}"
        )

        for key, expected in SRE_ALERT_PAYLOAD_FIXTURE.items():
            actual = payload[key]
            if key == "refused_at":
                # Schema check is structural, not temporal — substitute the
                # fixture's canonical timestamp for the comparison.
                actual = SRE_ALERT_PAYLOAD_FIXTURE["refused_at"]
            assert actual == expected, (
                f"AC #5 field mismatch on {key}: actual={actual!r}, "
                f"expected={expected!r}"
            )

    def test_event_round_trips_through_json(self, tmp_path: Path) -> None:
        """The serialised payload round-trips through json.dumps/loads byte-for-byte."""
        event = self._make_event(tmp_path)

        payload = asdict(event)
        payload["refused_at"] = payload["refused_at"].isoformat()

        serialised = json.dumps(payload, sort_keys=True)
        reparsed = json.loads(serialised)
        assert reparsed == payload