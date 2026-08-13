"""Backup metrics tracking — refusalCount and lastRefusalAt (NFM-3016).

Also exposes the single :func:`_should_push_on_refusal` config gate that
the SRE-push observer (sibling task) calls to decide whether to emit the
``[SRE-WARNING]`` line on a refusal (NFM-3024-E AC2). The refusal is
**always** recorded on this class regardless of the gate — the suppression
is SRE-push-only, so the stats endpoint never goes silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .config import BackupCapacityConfig


# Public default constant so the observer can rely on a stable symbol name.
DEFAULT_PUSH_ON_REFUSAL: bool = True


def _should_push_on_refusal() -> bool:
    """Single, named gate for SRE-push emission on backup refusal.

    Reads ``NFM_BACKUP_PUSH_ON_REFUSAL`` (defaulting to :data:`DEFAULT_PUSH_ON_REFUSAL`)
    via :meth:`BackupCapacityConfig.from_env`. The observer (sibling task)
    calls this predicate; the guardrails module also calls it before
    emitting ``[SRE-WARNING]``.

    The default is *True* so existing behaviour is preserved. When the
    operator sets ``NFM_BACKUP_PUSH_ON_REFUSAL=false``, refusal events are
    still recorded on ``/api/admin/backups/stats`` (via
    :class:`BackupMetrics`) but the SRE push is suppressed.
    """
    return BackupCapacityConfig.from_env().push_on_refusal


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
        """Record a refused write. Returns the refusal timestamp.

        Recording is independent of the SRE-push gate — the stats endpoint
        must always see the refusal, even when ``pushOnRefusal: false``.
        """
        now = datetime.now(timezone.utc)
        self._refusal_count += 1
        self._last_refusal_at = now
        return now

    def snapshot(self) -> BackupMetricsSnapshot:
        return BackupMetricsSnapshot(
            refusal_count=self._refusal_count,
            last_refusal_at=self._last_refusal_at,
        )
