"""AC1 regression test for the backup-refusal SRE observer (NFM-3024-E / NFM-3060).

AC1: A synthetic refusal produced by mock state must generate exactly one
``[SRE-WARNING]`` payload to the SRE Monitor channel within one heartbeat.

The observer is read-only w.r.t. the writer — it consumes a ``BackupRefusalEvent``
and a ``RefusalStateSnapshot`` and forwards the AC1 payload through an explicit
``emit`` callable that the integration task (NFM-3064) wires in.  The observer
has **no default log sink** to avoid double-alerting with the capacity
guardrails (NFM-3043), which already emit ``[SRE-WARNING]`` at refusal time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from nfm_db.monitoring.refusal_observer import (
    BackupRefusalEvent,
    BackupRefusalObserver,
    RefusalStateSnapshot,
    build_sre_warning_payload,
)
from nfm_db.services.backup.metrics import format_rfc3339_z_ms

# A fixed synthetic moment — no coupling to wall-clock time.
FROZEN_AT = datetime(2026, 8, 13, 9, 0, 0, tzinfo=UTC)


def _collecting_observer() -> tuple[BackupRefusalObserver, list[dict[str, Any]]]:
    """Build an observer whose emit sink appends payloads to a list."""
    emitted: list[dict[str, Any]] = []
    return BackupRefusalObserver(emit=emitted.append), emitted


def _synthetic_event() -> BackupRefusalEvent:
    return BackupRefusalEvent(
        free_bytes=5_000_000_000,
        total_bytes=12_000_000_000,
        min_free_bytes=20_000_000_000,
        max_total_bytes=12 * 1024**3,
        refused_at=FROZEN_AT,
    )


def _synthetic_snapshot() -> RefusalStateSnapshot:
    return RefusalStateSnapshot(
        refusal_count=1,
        last_refusal_at=FROZEN_AT,
    )


def test_synthetic_refusal_emits_exactly_one_sre_warning_within_one_heartbeat() -> None:
    """AC1: one synthetic refusal -> exactly one [SRE-WARNING] payload, AC1 schema."""
    observer, emitted = _collecting_observer()

    observer.observe(event=_synthetic_event(), snapshot=_synthetic_snapshot())

    assert len(emitted) == 1
    payload = emitted[0]

    # Severity + tag are the contract — SRE Monitor scans on tag.
    assert payload["severity"] == "warning"
    assert payload["tag"] == "backup-refusal"

    # Counters come from the writer-side snapshot.
    assert payload["refusalCount"] == 1
    assert payload["lastRefusalAt"] == format_rfc3339_z_ms(FROZEN_AT)

    # Byte fields match the event the writer emitted.
    assert payload["freeBytes"] == 5_000_000_000
    assert payload["totalBytes"] == 12_000_000_000
    assert payload["minFreeBytes"] == 20_000_000_000
    assert payload["maxTotalBytes"] == 12 * 1024**3


def test_payload_keys_are_exactly_the_ac1_set_with_no_extras() -> None:
    """The AC1 schema is byte-for-byte the eight fields listed in NFM-3024-E."""
    observer, emitted = _collecting_observer()

    observer.observe(event=_synthetic_event(), snapshot=_synthetic_snapshot())

    payload = emitted[0]
    assert set(payload.keys()) == {
        "severity",
        "tag",
        "refusalCount",
        "lastRefusalAt",
        "freeBytes",
        "totalBytes",
        "minFreeBytes",
        "maxTotalBytes",
    }


def test_two_refusals_emit_two_payloads_debounce_handled_by_sibling() -> None:
    """AC1 is 'one alert per refusal event'. Burst debouncing is the AC4 sibling's
    job — this observer must not silently swallow additional events."""
    observer, emitted = _collecting_observer()

    observer.observe(event=_synthetic_event(), snapshot=_synthetic_snapshot())
    observer.observe(event=_synthetic_event(), snapshot=_synthetic_snapshot())

    assert len(emitted) == 2
    assert all(p["severity"] == "warning" for p in emitted)


def test_observer_requires_explicit_emit_to_prevent_double_alerting() -> None:
    """NFM-3043 conflict resolution: observer has no default log sink.

    The capacity guardrails already emit ``[SRE-WARNING]`` at refusal time.
    Constructing the observer without an explicit ``emit`` must raise
    ``TypeError`` so integration (NFM-3064) is forced to wire the transport
    explicitly, preventing double-alerting.
    """
    with pytest.raises(TypeError, match="missing.*required.*argument.*emit"):
        BackupRefusalObserver()  # type: ignore[call-arg]


def test_observer_does_not_emit_to_log_channel_by_default(
    caplog: Any,
) -> None:
    """With an explicit emit sink (required), the observer must NOT produce
    any log output on the observer's logger — the log channel is the
    guardrails' domain (NFM-3043)."""
    emitted: list[dict[str, Any]] = []
    observer = BackupRefusalObserver(emit=emitted.append)

    with caplog.at_level(logging.DEBUG, logger="nfm_db.monitoring.refusal_observer"):
        observer.observe(event=_synthetic_event(), snapshot=_synthetic_snapshot())

    # Observer's own logger must be silent — no [SRE-WARNING] leak.
    observer_records = [
        r for r in caplog.records
        if r.name == "nfm_db.monitoring.refusal_observer"
    ]
    assert len(observer_records) == 0

    # The injected emit sink still received the AC1 payload.
    assert len(emitted) == 1
    assert emitted[0]["tag"] == "backup-refusal"


def test_build_sre_warning_payload_is_pure_and_reusable() -> None:
    """The pure builder is exposed so callers / tests can compose payloads
    without standing up an observer instance."""
    payload = build_sre_warning_payload(
        event=_synthetic_event(), snapshot=_synthetic_snapshot()
    )

    assert payload == {
        "severity": "warning",
        "tag": "backup-refusal",
        "refusalCount": 1,
        "lastRefusalAt": format_rfc3339_z_ms(FROZEN_AT),
        "freeBytes": 5_000_000_000,
        "totalBytes": 12_000_000_000,
        "minFreeBytes": 20_000_000_000,
        "maxTotalBytes": 12 * 1024**3,
    }


def test_snapshot_with_no_prior_refusals_emits_zero_count_and_null_timestamp() -> None:
    """First refusal in a fresh process must still emit — refusalCount starts at
    whatever the writer recorded (here: 1). Pre-observer state is the writer's
    problem; the observer just relays the snapshot it's handed."""
    observer, emitted = _collecting_observer()
    snapshot = RefusalStateSnapshot(refusal_count=1, last_refusal_at=FROZEN_AT)

    observer.observe(event=_synthetic_event(), snapshot=snapshot)

    assert emitted[0]["refusalCount"] == 1
    assert emitted[0]["lastRefusalAt"] == format_rfc3339_z_ms(FROZEN_AT)


def test_observer_does_not_mutate_event_or_snapshot() -> None:
    """Frozen dataclasses stay frozen — observer only reads."""
    event = _synthetic_event()
    snapshot = _synthetic_snapshot()

    observer, _ = _collecting_observer()
    observer.observe(event=event, snapshot=snapshot)

    assert event.refused_at == FROZEN_AT
    assert snapshot.refusal_count == 1
