"""Snapshot regression test for the [SRE-WARNING] refusal alert payload.

NFM-3024-E AC3: Alert payload must match the documented schema byte-for-byte.

This test freezes the alert payload shape in a regression test that asserts
byte-for-byte equality with the documented spec (see [NFM-3053] and
[NFM-3024]). Any rename, addition, or removal of a key MUST fail this test.

[NFM-3024]: /NFM/issues/NFM-3024
[NFM-3053]: /NFM/issues/NFM-3053

The observer that emits the structured payload ships in the sibling task
[NFM-3060]. The integration task [NFM-3055] merges all NFM-3024-E siblings,
at which point the observer-emits test in this file becomes a hard
byte-for-byte gate. Until that integration, the test skips gracefully with
a clear marker so this branch's CI stays green.

[NFM-3060]: /NFM/issues/NFM-3060
[NFM-3055]: /NFM/issues/NFM-3055
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import FloorBreachEvent
from nfm_db.services.backup.metrics import BackupMetrics, format_rfc3339_z_ms

# ---------------------------------------------------------------------------
# Documented schema (NFM-3024-E spec, see parent issue NFM-3053 description).
# Keys MUST appear in the order listed here in the serialized payload.
# ---------------------------------------------------------------------------

PAYLOAD_KEY_ORDER: tuple[str, ...] = (
    "severity",
    "tag",
    "refusalCount",
    "lastRefusalAt",
    "freeBytes",
    "totalBytes",
    "minFreeBytes",
    "maxTotalBytes",
)


def _expected_payload(
    *,
    refusal_count: int,
    last_refusal_at: datetime,
    free_bytes: int,
    total_bytes: int,
    min_free_bytes: int,
    max_total_bytes: int,
) -> dict[str, object]:
    """Build the exact payload the observer MUST produce.

    ``lastRefusalAt`` is formatted via ``format_rfc3339_z_ms`` — RFC-3339
    UTC with a ``Z`` suffix and millisecond precision.  Byte-for-byte
    equality; no normalization at compare time.
    """
    return {
        "severity": "warning",
        "tag": "backup-refusal",
        "refusalCount": refusal_count,
        "lastRefusalAt": format_rfc3339_z_ms(last_refusal_at),
        "freeBytes": free_bytes,
        "totalBytes": total_bytes,
        "minFreeBytes": min_free_bytes,
        "maxTotalBytes": max_total_bytes,
    }


# ---------------------------------------------------------------------------
# AC3: snapshot regression test
# ---------------------------------------------------------------------------


class TestRefusalAlertSchemaSnapshot:
    """NFM-3024-E AC3: payload matches the documented schema byte-for-byte."""

    def test_documented_keys_are_in_documented_order(self) -> None:
        """Freeze the spec — no observer dependency.

        Any rename, addition, or removal of a key MUST be reflected here
        AND in the observer. This test does not require the observer; it
        is the schema-of-record.
        """
        sample = _expected_payload(
            refusal_count=1,
            last_refusal_at=datetime(2026, 8, 13, 5, 0, 0, tzinfo=UTC),
            free_bytes=5_000_000_000,
            total_bytes=8_500_000_000,
            min_free_bytes=20 * 1024**3,
            max_total_bytes=12 * 1024**3,
        )
        assert list(sample.keys()) == list(PAYLOAD_KEY_ORDER)

    def test_documented_keys_are_exactly_eight(self) -> None:
        """Belt and suspenders: the spec lists exactly eight keys."""
        sample = _expected_payload(
            refusal_count=0,
            last_refusal_at=datetime(2026, 1, 1, tzinfo=UTC),
            free_bytes=0,
            total_bytes=0,
            min_free_bytes=0,
            max_total_bytes=0,
        )
        assert len(sample) == 8
        assert set(sample.keys()) == set(PAYLOAD_KEY_ORDER)

    def test_observer_emits_byte_for_byte_payload(self) -> None:
        """Snapshot the observer's emitted SRE event against the schema.

        Invokes the observer with a synthetic refusal, captures the
        emitted SRE event, and asserts it matches the documented schema
        byte-for-byte — keys, values, AND ordering. Fails if any key is
        renamed, added, or removed.

        Skipped when the NFM-3060 observer module is not yet on this
        branch (integration lands it via NFM-3055).
        """
        # Local importorskip so only THIS test skips when NFM-3060's
        # observer hasn't yet been merged into the integration branch.
        # The two pure-spec tests above must always execute.
        refusal_observer = pytest.importorskip(
            "nfm_db.services.backup.refusal_observer",
            reason=(
                "refusal observer (NFM-3060) not yet merged into this "
                "branch; activates once NFM-3055 merges the NFM-3024-E "
                "siblings."
            ),
        )

        cfg = BackupCapacityConfig(
            max_total_bytes=12 * 1024**3,
            min_free_bytes=20 * 1024**3,
            refuse_on_floor_breach=True,
        )
        metrics = BackupMetrics()
        refused_at = datetime(2026, 8, 13, 5, 0, 0, tzinfo=UTC)
        event = FloorBreachEvent(
            free_bytes=5_000_000_000,
            backup_size=3_000_000_000,
            floor=cfg.min_free_bytes,
            refused_at=refused_at,
            capacity_total_bytes=8_500_000_000,
        )
        # Mirror what CapacityGuardrails.check_floor_before_write does.
        metrics._refusal_count = 1  # type: ignore[attr-defined]
        metrics._last_refusal_at = refused_at  # type: ignore[attr-defined]

        observed: dict[str, object] = refusal_observer.build_refusal_alert_payload(  # type: ignore[attr-defined]
            metrics=metrics,
            event=event,
            config=cfg,
        )

        expected = _expected_payload(
            refusal_count=1,
            last_refusal_at=refused_at,
            free_bytes=event.free_bytes,
            total_bytes=event.capacity_total_bytes,
            min_free_bytes=cfg.min_free_bytes,
            max_total_bytes=cfg.max_total_bytes,
        )

        # 1. Key set: must match exactly (no extras, no missing).
        assert set(observed) == set(expected), (
            "Refusal alert payload key set drifted from the documented schema.\n"
            f"  snapshot keys = {sorted(expected)}\n"
            f"  observed keys = {sorted(observed)}\n"
            f"  missing       = {sorted(set(expected) - set(observed))}\n"
            f"  extra         = {sorted(set(observed) - set(expected))}"
        )

        # 2. Key order: must match exactly (renames, reorders, or
        #    alphabetization will break monitoring).
        assert list(observed) == list(expected), (
            "Refusal alert payload key order drifted from the documented schema.\n"
            f"  snapshot order = {list(expected)}\n"
            f"  observed order = {list(observed)}"
        )

        # 3. Values: byte-for-byte comparison — no normalization needed
        #    since both sides use format_rfc3339_z_ms.
        observed_json = json.dumps(observed, indent=2, sort_keys=False)
        expected_json = json.dumps(expected, indent=2, sort_keys=False)
        assert observed == expected, (
            "Refusal alert payload values drifted from the documented schema.\n"
            f"  expected (snapshot):\n{expected_json}\n"
            f"  observed:            \n{observed_json}"
        )
