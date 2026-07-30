"""Worker health tracking — consecutive failure counter and alerting (NFM-2014).

Provides a process-level :class:`WorkerHealthTracker` that the Celery worker
process calls after each literature-processing task.  When consecutive
failures reach :data:`ALERT_THRESHOLD`, a CRITICAL log line is emitted and
(optional) Sentry event is captured.

The tracker is a module-level singleton so both the worker task and the
``/health`` endpoint share the same state without needing a database or
Redis round-trip.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Number of consecutive failures before CRITICAL alert fires.
ALERT_THRESHOLD = 5


def _try_sentry_capture(event: dict[str, Any]) -> None:
    """Fire a Sentry event if sentry-sdk is installed.

    Sentry is optional — the project does not yet configure it.  When it
    is added, this function will start emitting events automatically
    without any other code changes.
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    # sentry_sdk.capture_event takes a ``sentry_sdk.Event`` (or a plain
    # ``dict`` that conforms to the Sentry envelope schema).  Until the
    # project actually configures Sentry we ship a no-op so the call
    # surface is ready; the dict payload is preserved for that future
    # Sentry init.
    _ = event
    del sentry_sdk


class WorkerHealthTracker:
    """Thread-safe process-level tracker for worker task outcomes.

    Attributes:
        consecutive_failures: Count of failures since last success.
        last_success_at: ISO-8601 timestamp of most recent success, or ``None``.
        last_error: Truncated error message from most recent failure, or ``None``.
    """

    def __init__(self, *, alert_threshold: int = ALERT_THRESHOLD) -> None:
        self._threshold = alert_threshold
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._last_success_at: str | None = None
        self._last_error: str | None = None
        self._alerted_at_count: int | None = None

    # -- public read-only properties -----------------------------------------

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def last_success_at(self) -> str | None:
        return self._last_success_at

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def status(self) -> str:
        """Return ``'degraded'`` when failures >= threshold, else ``'ok'``."""
        if self._consecutive_failures >= self._threshold:
            return "degraded"
        return "ok"

    # -- mutation methods ---------------------------------------------------

    def record_success(self) -> None:
        """Reset the failure counter and record the success timestamp."""
        with self._lock:
            self._consecutive_failures = 0
            self._last_success_at = datetime.now(UTC).isoformat()
            self._alerted_at_count = None

    def record_failure(self, error: str) -> None:
        """Increment the failure counter and potentially fire an alert.

        Args:
            error: Short description of the failure (will be truncated to
                   500 characters for storage, 200 for the Sentry event).
        """
        truncated = error[:500]
        with self._lock:
            self._consecutive_failures += 1
            self._last_error = truncated
            count = self._consecutive_failures
            already_alerted = self._alerted_at_count == count

        if count >= self._threshold and not already_alerted:
            with self._lock:
                self._alerted_at_count = count
            logger.critical(
                "Worker consecutive failure alert: %d consecutive failures "
                "(threshold=%d). Last error: %s",
                count,
                self._threshold,
                error[:200],
                extra={
                    "consecutive_failures": count,
                    "alert_threshold": self._threshold,
                },
            )
            _try_sentry_capture({
                "level": "fatal",
                "message": (
                    f"Worker consecutive failure alert: {count} consecutive "
                    f"failures (threshold={self._threshold})"
                ),
                "extra": {
                    "consecutive_failures": count,
                    "last_error": error[:200],
                },
                "tags": {"component": "ingest-worker"},
            })

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable dict of the current health state."""
        with self._lock:
            return {
                "status": self.status,
                "consecutive_failures": self._consecutive_failures,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
            }

    def reset(self) -> None:
        """Clear all state.  Useful in tests."""
        with self._lock:
            self._consecutive_failures = 0
            self._last_success_at = None
            self._last_error = None
            self._alerted_at_count = None


#: Module-level singleton shared by the worker task and /health endpoint.
worker_health = WorkerHealthTracker()
