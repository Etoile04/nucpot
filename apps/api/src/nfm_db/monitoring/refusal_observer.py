"""Backup-refusal observer that emits one ``[SRE-WARNING]`` per refusal event.

NFM-3024-E / NFM-3060 (AC1).

This module is the *observer* side of NFM-3024-E:

* The refusal-state stream (NFM-3024-C, ``BackupMetrics`` + capacity
  guardrails) produces a refusal tuple and an updated snapshot.
* This observer reads that tuple, builds the AC1 schema payload, and forwards
  it to the SRE Monitor channel within one heartbeat.
* One alert per refusal event. Burst debouncing is handled by the sibling
  ``refusal_alert_debounce`` module (AC4 / NFM-3063).

The observer never touches the writer. Integration wires them together
(NFM-3064).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

__all__ = [
    "BackupRefusalEvent",
    "BackupRefusalObserver",
    "RefusalStateSnapshot",
    "build_sre_warning_payload",
]


#: Prefix the SRE Monitor agent scans for when it ingests alerts.
SRE_WARNING_PREFIX: Final[str] = "[SRE-WARNING]"


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
            snapshot.last_refusal_at.isoformat()
            if snapshot.last_refusal_at is not None
            else None
        ),
        "freeBytes": event.free_bytes,
        "totalBytes": event.total_bytes,
        "minFreeBytes": event.min_free_bytes,
        "maxTotalBytes": event.max_total_bytes,
    }


def _default_log_emit(payload: dict[str, Any]) -> None:
    """Default SRE Monitor channel: ``logging.warning("[SRE-WARNING] %s", json)``.

    Reuses the same log-scanning transport NFM-2915 / Hermes already watch for
    ``[SRE-WARNING]`` markers — no new transport invented. Logger name is
    scoped to this module so SRE Monitor filters can scope their ingestion.
    """
    logger = logging.getLogger("nfm_db.monitoring.refusal_observer")
    logger.warning("%s %s", SRE_WARNING_PREFIX, json.dumps(payload, sort_keys=True))


class BackupRefusalObserver:
    """Forwards one ``[SRE-WARNING]`` payload to the SRE channel per refusal.

    AC1: synthetic refusal via mock state produces an ``[SRE-WARNING]`` event
    within one heartbeat end-to-end.

    The ``emit`` callable is the SRE Monitor transport; the integration wires
    the real one in (Hermes log ingestion is the default here).
    """

    def __init__(
        self, *, emit: Callable[[dict[str, Any]], None] | None = None
    ) -> None:
        self._emit: Callable[[dict[str, Any]], None] = emit or _default_log_emit

    def observe(
        self, *, event: BackupRefusalEvent, snapshot: RefusalStateSnapshot
    ) -> None:
        """Emit exactly one ``[SRE-WARNING]`` payload for this refusal event."""
        payload = build_sre_warning_payload(event=event, snapshot=snapshot)
        self._emit(payload)
