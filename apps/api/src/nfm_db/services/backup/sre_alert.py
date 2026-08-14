"""SRE refusal alert emitter for backup floor breaches (NFM-3053).

Emits structured ``[SRE-WARNING]`` health events when the backup
guardrails refuse a write due to a free-space floor breach.  Controlled
by ``push_on_refusal`` (default ``True``).  Refusal is NEVER silent —
the refusal counter and stats endpoint always reflect the event regardless
of this flag; only the SRE push is suppressed.

Debounce strategy:
- The first refusal in each hour-window emits immediately.
- Subsequent refusals within the same hour are suppressed (debounced).
- When a refusal arrives after the hour window has expired and there are
  suppressed refusals pending, a single summary alert is emitted for the
  accumulated count before the new refusal alert fires.

Alert payload schema (exposed via ``build_alert_payload``):

.. code-block:: json

    {
        "severity": "warning",
        "tag": "backup-refusal",
        "refusalCount":    <number>,
        "lastRefusalAt":   "<rfc3339-z-ms>",
        "freeBytes":       <number>,
        "totalBytes":      <number>,
        "minFreeBytes":    <number>,
        "maxTotalBytes":   <number>
    }

The emitter depends on
:func:`nfm_db.services.health_event_emitter.emit_health_event_sync`
for the actual DB write.  That function is best-effort and never raises,
so a failure to persist the health event will not propagate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .metrics import format_rfc3339_z_ms

logger = logging.getLogger(__name__)

#: Default debounce window — one hour.
_DEFAULT_DEBOUNCE_SECONDS = 3600

#: SRE event type tag stored in ``health_events.event_type``.
_EVENT_TYPE = "backup_refusal"

#: Severity level for backup refusal alerts.
_SEVERITY = "warning"


@dataclass(frozen=True)
class BackupRefusalAlert:
    """Immutable alert payload produced by the emitter.

    Fields match the NFM-3053 schema exactly.  Serialised via
    :meth:`to_context` for storage in ``health_events.context`` JSONB.
    """

    refusal_count: int
    last_refusal_at: datetime
    free_bytes: int
    total_bytes: int
    min_free_bytes: int
    max_total_bytes: int

    def to_context(self) -> dict[str, Any]:
        """Serialise to the JSONB context dict stored in ``health_events``."""
        return {
            "severity": "warning",
            "tag": "backup-refusal",
            "refusalCount": self.refusal_count,
            "lastRefusalAt": format_rfc3339_z_ms(self.last_refusal_at),
            "freeBytes": self.free_bytes,
            "totalBytes": self.total_bytes,
            "minFreeBytes": self.min_free_bytes,
            "maxTotalBytes": self.max_total_bytes,
        }


class RefusalAlertEmitter:
    """Debouncing SRE alert emitter for backup floor-breach refusals.

    Parameters:
        push_on_refusal: When ``False``, the SRE push is suppressed while
            refusal counting and stats continue normally.
        debounce_seconds: Minimum seconds between consecutive SRE alert
            pushes (default 3600 = 1 hour).
        emit_fn: The function used to persist the alert.  Defaults to
            ``emit_health_event_sync`` from the health event emitter.
            Accepts a custom callable for test injection.
    """

    def __init__(
        self,
        *,
        push_on_refusal: bool = True,
        debounce_seconds: int = _DEFAULT_DEBOUNCE_SECONDS,
        emit_fn: Callable[..., None] | None = None,
    ) -> None:
        self._push_on_refusal = push_on_refusal
        self._debounce_window = timedelta(seconds=debounce_seconds)
        self._last_emit_at: datetime | None = None
        self._suppressed_count: int = 0
        self._emit_fn = emit_fn or self._default_emit_fn

    # -- public properties ---------------------------------------------------

    @property
    def push_on_refusal(self) -> bool:
        return self._push_on_refusal

    @property
    def last_emit_at(self) -> datetime | None:
        return self._last_emit_at

    @property
    def debounce_seconds(self) -> int:
        return int(self._debounce_window.total_seconds())

    @property
    def suppressed_count(self) -> int:
        return self._suppressed_count

    # -- public API -----------------------------------------------------------

    def on_refusal(
        self,
        *,
        refusal_count: int,
        last_refusal_at: datetime,
        free_bytes: int,
        total_bytes: int,
        min_free_bytes: int,
        max_total_bytes: int,
    ) -> BackupRefusalAlert | None:
        """Process a refusal event and possibly emit an SRE alert.

        Returns the :class:`BackupRefusalAlert` that was emitted, or
        ``None`` if the alert was suppressed (debounced or
        ``push_on_refusal=False``).

        This is the single entry point called by the guardrails layer
        after a floor-breach refusal.
        """
        if not self._push_on_refusal:
            return None

        now = datetime.now(UTC)
        alert = BackupRefusalAlert(
            refusal_count=refusal_count,
            last_refusal_at=last_refusal_at,
            free_bytes=free_bytes,
            total_bytes=total_bytes,
            min_free_bytes=min_free_bytes,
            max_total_bytes=max_total_bytes,
        )

        # Flush a debounced summary from the prior window if the window
        # has expired and suppressed refusals are pending.
        if (
            self._last_emit_at is not None
            and self._suppressed_count > 0
            and now - self._last_emit_at >= self._debounce_window
        ):
            summary = BackupRefusalAlert(
                refusal_count=self._suppressed_count,
                last_refusal_at=self._last_emit_at,
                free_bytes=free_bytes,
                total_bytes=total_bytes,
                min_free_bytes=min_free_bytes,
                max_total_bytes=max_total_bytes,
            )
            self._do_emit(summary, is_summary=True)
            self._suppressed_count = 0

        # Decide whether to emit or debounce the current refusal.
        if (
            self._last_emit_at is not None
            and now - self._last_emit_at < self._debounce_window
        ):
            self._suppressed_count += 1
            return None

        # Outside the window (or first ever) — emit immediately.
        ok = self._do_emit(alert, is_summary=False)
        return alert if ok else None

    # -- internals -------------------------------------------------------------

    def _do_emit(self, alert: BackupRefusalAlert, *, is_summary: bool) -> bool:
        """Persist the alert via the emit function (never raises).

        Returns ``True`` if the alert was persisted successfully,
        ``False`` if the emit function raised.  ``_last_emit_at`` is
        only advanced on success so that a DB-outage failure does not
        silently debounce the next real refusal.
        """
        context = alert.to_context()
        if is_summary:
            context["_debouncedSummary"] = True

        try:
            self._emit_fn(
                event_type=_EVENT_TYPE,
                severity=_SEVERITY,
                source_service="backup_guardrails",
                context=context,
            )
        except (OSError, RuntimeError, AttributeError):
            logger.exception(
                "Failed to persist backup-refusal SRE alert "
                "(alert data logged but not written to DB)"
            )
            return False

        self._last_emit_at = datetime.now(UTC)
        logger.warning(
            "[SRE-WARNING] backup-refusal alert emitted: "
            "refusalCount=%d freeBytes=%d totalBytes=%d "
            "minFreeBytes=%d maxTotalBytes=%d%s",
            alert.refusal_count,
            alert.free_bytes,
            alert.total_bytes,
            alert.min_free_bytes,
            alert.max_total_bytes,
            " (debounced summary)" if is_summary else "",
        )
        return True

    @staticmethod
    def _default_emit_fn(**kwargs: Any) -> None:
        """Default emit function — delegates to health_event_emitter."""
        from nfm_db.services.health_event_emitter import emit_health_event_sync

        emit_health_event_sync(**kwargs)


__all__ = ["BackupRefusalAlert", "RefusalAlertEmitter"]
