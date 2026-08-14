"""Backup-refusal observer that builds and forwards the AC1 ``[SRE-WARNING]`` payload.

NFM-3024-E / NFM-3060 (AC1).

This module is the *observer* side of NFM-3024-E:

* The refusal-state stream (NFM-3024-C, ``BackupMetrics`` + capacity
  guardrails) produces a refusal tuple and an updated snapshot.
* This observer reads that tuple, builds the AC1 schema payload, and forwards
  it to an explicit emit sink within one heartbeat.
* One alert per refusal event. Burst debouncing is handled by the sibling
  ``refusal_alert_debounce`` module (AC4 / NFM-3063).

**Architectural note (NFM-3043 conflict resolution):**

The capacity guardrails (NFM-3043 on ``main``) already emit a lightweight
``[SRE-WARNING]`` log line at refusal time.  This observer does **not**
duplicate that log-channel emission.  Instead, it builds the structured AC1
JSON payload and forwards it through an explicit ``emit`` callable that the
integration task (NFM-3064) is responsible for wiring.  This avoids
double-alerting while keeping the observer as the canonical AC1 payload
source.

The observer never touches the writer. Integration wires them together
(NFM-3064).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from nfm_db.services.backup.metrics import format_rfc3339_z_ms

__all__ = [
    "BackupRefusalEvent",
    "BackupRefusalObserver",
    "RefusalStateSnapshot",
    "build_sre_warning_payload",
]


#: Severity string on every backup-refusal payload.
SEVERITY_WARNING: Final[str] = "warning"

#: Tag the SRE Monitor filters on.
TAG_BACKUP_REFUSAL: Final[str] = "backup-refusal"


@dataclass(frozen=True)
class BackupRefusalEvent:
    """A single refusal-state tuple the observer reads from the writer.

    Mirrors the fields the capacity guardrails (NFM-3024-C) carry on a
    floor-breach refusal. The observer never writes this; it is consumed
    immutably.
    """

    free_bytes: int
    total_bytes: int
    min_free_bytes: int
    max_total_bytes: int
    refused_at: datetime


@dataclass(frozen=True)
class RefusalStateSnapshot:
    """Point-in-time snapshot of the writer-side refusal counters.

    Sourced from ``BackupMetrics.snapshot()`` (NFM-3024-C). Observer treats
    this as read-only state.
    """

    refusal_count: int
    last_refusal_at: datetime | None


def build_sre_warning_payload(
    *, event: BackupRefusalEvent, snapshot: RefusalStateSnapshot
) -> dict[str, Any]:
    """Compose the AC1 schema payload from a refusal event + state snapshot.

    Pure function — exposed so callers (and the debouncer sibling) can
    construct payloads without standing up an observer instance.
    """
    return {
        "severity": SEVERITY_WARNING,
        "tag": TAG_BACKUP_REFUSAL,
        "refusalCount": snapshot.refusal_count,
        "lastRefusalAt": (
            format_rfc3339_z_ms(snapshot.last_refusal_at)
            if snapshot.last_refusal_at is not None
            else None
        ),
        "freeBytes": event.free_bytes,
        "totalBytes": event.total_bytes,
        "minFreeBytes": event.min_free_bytes,
        "maxTotalBytes": event.max_total_bytes,
    }


class BackupRefusalObserver:
    """Forwards one AC1 ``[SRE-WARNING]`` payload to an explicit sink per refusal.

    AC1: synthetic refusal via mock state produces an ``[SRE-WARNING]`` event
    within one heartbeat end-to-end.

    The ``emit`` callable is the SRE Monitor transport.  It must be provided
    explicitly — the integration task (NFM-3064) wires the real one in.  This
    avoids double-alerting with the capacity guardrails (NFM-3043), which
    already emit a lightweight ``[SRE-WARNING]`` log line at refusal time.
    """

    def __init__(
        self, *, emit: Callable[[dict[str, Any]], None]
    ) -> None:
        self._emit = emit

    def observe(
        self, *, event: BackupRefusalEvent, snapshot: RefusalStateSnapshot
    ) -> None:
        """Build and forward exactly one AC1 payload for this refusal event."""
        payload = build_sre_warning_payload(event=event, snapshot=snapshot)
        self._emit(payload)
