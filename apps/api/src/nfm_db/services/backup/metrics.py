"""Backup metrics tracking — refusalCount and lastRefusalAt (NFM-3016).

Also exposes the single :func:`_should_push_on_refusal` config gate that
the SRE-push observer (sibling task) calls to decide whether to emit the
``[SRE-WARNING]`` line on a refusal (NFM-3024-E AC2). The refusal is
**always** recorded on this class regardless of the gate — the suppression
is SRE-push-only, so the stats endpoint never goes silent.

Amendment 5 (ADR D4): :func:`format_rfc3339_z_ms` produces the canonical
``lastRefusalAt`` string in RFC-3339 UTC Z with millisecond precision,
used by the gated SRE-WARNING log line and exposed on the snapshot for
observer consistency (NFM-3060, NFM-3062).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .config import BackupCapacityConfig

# ---------------------------------------------------------------------------
# RFC-3339 UTC Z with millisecond precision (ADR D4 — Amendment 5)
# ---------------------------------------------------------------------------


def format_rfc3339_z_ms(dt: datetime) -> str:
    """Format *dt* as RFC-3339 UTC with a ``Z`` suffix and millisecond precision.

    Output example: ``2026-08-13T07:18:59.123Z``.

    Raises:
        ValueError: If *dt* is not timezone-aware or not UTC.
    """
    if dt.tzinfo is None:
        raise ValueError(
            "format_rfc3339_z_ms requires a timezone-aware datetime"
        )
    if dt.tzinfo is not UTC:
        raise ValueError(
            "format_rfc3339_z_ms requires UTC timezone, "
            f"got offset {dt.utcoffset()}"
        )
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


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
    last_refusal_at_rfc3339: str | None = None


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
        now = datetime.now(UTC)
        self._refusal_count += 1
        self._last_refusal_at = now
        return now

    def snapshot(self) -> BackupMetricsSnapshot:
        formatted = (
            format_rfc3339_z_ms(self._last_refusal_at)
            if self._last_refusal_at is not None
            else None
        )
        return BackupMetricsSnapshot(
            refusal_count=self._refusal_count,
            last_refusal_at=self._last_refusal_at,
            last_refusal_at_rfc3339=formatted,
        )
