"""Backup metrics tracking — refusalCount and lastRefusalAt (NFM-3016)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class BackupMetricsSnapshot:
    """Point-in-time snapshot of backup capacity metrics."""

    refusal_count: int
    last_refusal_at: datetime | None


class BackupMetrics:
    """Mutable tracker for backup refusal events.

    Usage::

        metrics = BackupMetrics()
        metrics.record_refusal(free_bytes=5_000_000_000, backup_size=1_000_000_000, floor=20_000_000_000)
        snapshot = metrics.snapshot()
    """

    def __init__(self) -> None:
        self._refusal_count: int = 0
        self._last_refusal_at: datetime | None = None

    @property
    def refusal_count(self) -> int:
        return self._refusal_count

    @property
    def last_refusal_at(self) -> datetime | None:
        return self._last_refusal_at

    def record_refusal(
        self, *, free_bytes: int, backup_size: int, floor: int
    ) -> datetime:
        """Record a refused write. Returns the refusal timestamp."""
        now = datetime.now(timezone.utc)
        self._refusal_count += 1
        self._last_refusal_at = now
        return now

    def snapshot(self) -> BackupMetricsSnapshot:
        return BackupMetricsSnapshot(
            refusal_count=self._refusal_count,
            last_refusal_at=self._last_refusal_at,
        )
