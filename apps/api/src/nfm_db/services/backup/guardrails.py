"""Capacity guardrails for backup writes (NFM-3016).

Core logic:
1. **Pre-write floor check** — refuse if ``free_bytes - backup_size < min_free_bytes``.
2. **Post-write cap check** — prune oldest until ``total_bytes ≤ max_total_bytes``.
3. **Re-check floor after pruner** — a pruner run that frees space but
   leaves the system below the floor is NOT tolerated.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import BackupCapacityConfig
from .metrics import BackupMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiskUsage:
    """Immutable snapshot of disk usage for a backup directory."""

    free_bytes: int
    total_backup_bytes: int


@dataclass(frozen=True)
class FloorBreachEvent:
    """Immutable record of a floor breach refusal."""

    free_bytes: int
    backup_size: int
    floor: int
    refused_at: datetime
    capacity_total_bytes: int


@dataclass(frozen=True)
class BackupEntry:
    """A single backup file on disk, ordered by age."""

    path: Path
    size_bytes: int
    modified_at: float  # epoch timestamp


class CapacityGuardrails:
    """Enforces capacity guardrails around backup writes.

    The sequence is: *pre-write floor check → write → post-write cap check
    → re-check floor*.

    Parameters:
        config:  Capacity configuration (caps, floors, flags).
        backup_dir:  Directory where backup files live.
        metrics:  Shared metrics tracker for refusal events.
    """

    def __init__(
        self,
        *,
        config: BackupCapacityConfig,
        backup_dir: Path,
        metrics: BackupMetrics | None = None,
    ) -> None:
        self._config = config
        self._backup_dir = backup_dir
        self._metrics = metrics or BackupMetrics()

    # -- public properties ---------------------------------------------------

    @property
    def config(self) -> BackupCapacityConfig:
        return self._config

    @property
    def metrics(self) -> BackupMetrics:
        return self._metrics

    # -- pre-write floor check ------------------------------------------------

    def check_floor_before_write(
        self, *, backup_size: int, disk: DiskUsage
    ) -> FloorBreachEvent | None:
        """Check whether writing *backup_size* would breach the free-space floor.

        Returns a ``FloorBreachEvent`` if the write must be refused,
        or ``None`` if the write is permitted.
        """
        if not self._config.refuse_on_floor_breach:
            return None

        projected_free = disk.free_bytes - backup_size
        if projected_free >= self._config.min_free_bytes:
            return None

        refused_at = self._metrics.record_refusal(
            free_bytes=disk.free_bytes,
            backup_size=backup_size,
            floor=self._config.min_free_bytes,
        )

        event = FloorBreachEvent(
            free_bytes=disk.free_bytes,
            backup_size=backup_size,
            floor=self._config.min_free_bytes,
            refused_at=refused_at,
            capacity_total_bytes=disk.total_backup_bytes,
        )

        logger.warning(
            "[SRE-WARNING] Backup write refused: floor breach. "
            "free=%d backup_size=%d floor=%d total=%d",
            disk.free_bytes,
            backup_size,
            self._config.min_free_bytes,
            disk.total_backup_bytes,
        )

        return event

    # -- post-write cap check --------------------------------------------------

    def enforce_cap_after_write(self, *, disk: DiskUsage) -> list[Path]:
        """Prune oldest backups until ``total_backup_bytes ≤ max_total_bytes``.

        Returns a list of pruned file paths (may be empty).
        """
        if disk.total_backup_bytes <= self._config.max_total_bytes:
            return []

        entries = self._list_backup_entries()
        entries.sort(key=lambda e: e.modified_at)

        pruned: list[Path] = []
        running_total = disk.total_backup_bytes

        for entry in entries:
            if running_total <= self._config.max_total_bytes:
                break
            entry.path.unlink(missing_ok=True)
            running_total -= entry.size_bytes
            pruned.append(entry.path)
            logger.info(
                "Pruned backup %s (%d bytes) to enforce cap (running total now %d)",
                entry.path,
                entry.size_bytes,
                running_total,
            )

        return pruned

    # -- post-pruner floor re-check ---------------------------------------------

    def recheck_floor_after_pruner(self, *, disk: DiskUsage) -> FloorBreachEvent | None:
        """Re-check the floor after pruning. Returns a refusal event if
        free space is still below the floor, even after pruning.

        This handles the edge case where a stale snapshot run already
        pushed free to 0 — such a state must not be tolerated.
        """
        if not self._config.refuse_on_floor_breach:
            return None

        if disk.free_bytes >= self._config.min_free_bytes:
            return None

        refused_at = self._metrics.record_refusal(
            free_bytes=disk.free_bytes,
            backup_size=0,
            floor=self._config.min_free_bytes,
        )

        event = FloorBreachEvent(
            free_bytes=disk.free_bytes,
            backup_size=0,
            floor=self._config.min_free_bytes,
            refused_at=refused_at,
            capacity_total_bytes=disk.total_backup_bytes,
        )

        logger.warning(
            "[SRE-WARNING] Post-pruner floor breach: free=%d floor=%d total=%d",
            disk.free_bytes,
            self._config.min_free_bytes,
            disk.total_backup_bytes,
        )

        return event

    # -- helpers ---------------------------------------------------------------

    def _list_backup_entries(self) -> list[BackupEntry]:
        """List backup files in *backup_dir* sorted by age."""
        if not self._backup_dir.exists():
            return []

        entries: list[BackupEntry] = []
        for child in self._backup_dir.iterdir():
            if child.is_file():
                stat = child.stat()
                entries.append(
                    BackupEntry(
                        path=child,
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                    )
                )

        return entries


def measure_disk(backup_dir: Path) -> DiskUsage:
    """Measure free space on the filesystem and total backup size.

    This is the canonical way to build a :class:`DiskUsage` for passing
    into guardrail checks.
    """
    usage = shutil.disk_usage(str(backup_dir.parent))
    total_backup = sum(
        f.stat().st_size for f in backup_dir.iterdir() if f.is_file()
    ) if backup_dir.exists() else 0
    return DiskUsage(free_bytes=usage.free, total_backup_bytes=total_backup)
