"""Debounce policy for backup-refusal SRE alerts (NFM-3024-E / NFM-3063).

The capacity guardrail added by NFM-3024 can refuse a backup many times in
quick succession — once per scheduled attempt. Emitting one ``[SRE-WARNING]``
per refusal would page the SRE Monitor dozens of times an hour for what is a
single ongoing condition.

Policy implemented here (hour-bucketed, "1 first alert + 1 summary"):

* The **first** refusal observed in a given wall-clock hour emits one
  ``[SRE-WARNING]`` alert immediately — SRE learns about the condition inside
  one heartbeat, which is the whole point of NFM-3024-E.
* Every subsequent refusal in that same hour is **suppressed** and only
  counted.
* When the hour rolls over (or when the observer explicitly flushes), a single
  ``[SRE-WARNING]`` *summary* alert is emitted for the closed hour if — and
  only if — at least one refusal was suppressed in it.

So a burst of N refusals inside one hour yields at most **2** alerts
regardless of N, and each distinct hour gets its own first alert.

The clock is injected (``now`` argument on :meth:`observe_refusal`) so tests
and the observer can drive synthetic time without coupling to production
timing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "MAX_ALERTS_PER_HOUR",
    "RefusalAlert",
    "RefusalAlertDebouncer",
]

#: Upper bound on alerts emitted for any single wall-clock hour, whatever the
#: refusal count in that hour: one first alert + one debounced summary.
MAX_ALERTS_PER_HOUR = 2

_ALERT_PREFIX = "[SRE-WARNING]"


@dataclass(frozen=True)
class RefusalAlert:
    """A single alert the observer should hand to the SRE Monitor."""

    kind: str  # "first" | "summary"
    hour: datetime  # the hour bucket this alert describes
    refusal_count: int  # refusals seen in that bucket at emit time
    message: str

    @property
    def is_summary(self) -> bool:
        return self.kind == "summary"


def _hour_bucket(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class RefusalAlertDebouncer:
    """Turns a stream of refusal events into a debounced alert stream.

    Usage from the observer::

        debouncer = RefusalAlertDebouncer(emit=sre_monitor.send)
        debouncer.observe_refusal(reason="capacity guardrail", now=clock())
        ...
        debouncer.flush()  # e.g. at observer shutdown
    """

    def __init__(self, emit: Callable[[RefusalAlert], None]) -> None:
        self._emit = emit
        self._open_hour: datetime | None = None
        self._count_in_hour = 0

    def observe_refusal(self, *, reason: str, now: datetime) -> None:
        """Record one refusal, emitting alerts per the debounce policy."""
        bucket = _hour_bucket(now)

        if self._open_hour is not None and bucket != self._open_hour:
            self._close_open_hour()

        if self._open_hour is None:
            self._open_hour = bucket
            self._count_in_hour = 1
            self._emit(
                RefusalAlert(
                    kind="first",
                    hour=bucket,
                    refusal_count=1,
                    message=(
                        f"{_ALERT_PREFIX} Backup refused at "
                        f"{now.isoformat()}: {reason}"
                    ),
                )
            )
            return

        # Same hour as the open bucket — suppress, just count.
        self._count_in_hour += 1

    def flush(self) -> None:
        """Close the open hour, emitting its summary if anything was suppressed."""
        self._close_open_hour()

    def _close_open_hour(self) -> None:
        hour, count = self._open_hour, self._count_in_hour
        self._open_hour = None
        self._count_in_hour = 0

        if hour is None or count <= 1:
            # Nothing suppressed — the first alert already told the whole story.
            return

        suppressed = count - 1
        self._emit(
            RefusalAlert(
                kind="summary",
                hour=hour,
                refusal_count=count,
                message=(
                    f"{_ALERT_PREFIX} Backup refusals during "
                    f"{hour.isoformat()}: {count} total "
                    f"({suppressed} suppressed after the first alert)"
                ),
            )
        )
