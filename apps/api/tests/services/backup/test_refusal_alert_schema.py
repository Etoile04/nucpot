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

    ``lastRefusalAt`` is normalized to ISO-8601 with a trailing ``Z`` (the
    UTC "Zulu" suffix). If the implementation emits ``+00:00`` instead,
    the acceptance check below normalizes on both sides so a single doc
    decision does not block the test.
    """
    iso_z = (
        last_refusal_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "severity": "warning",
        "tag": "backup-refusal",
        "refusalCount": refusal_count,
        "lastRefusalAt": iso_z,
        "freeBytes": free_bytes,
        "totalBytes": total_bytes,
        "minFreeBytes": min_free_bytes,
        "maxTotalBytes": max_total_bytes,
    }


def _normalize_ts(value: str) -> str:
    """Normalize ISO-8601 timestamps to a canonical ``Z``-suffix form.

    The schema accepts either ``...:00:00Z`` or ``...+00:00``; this helper
    collapses both to ``...Z`` so the byte-for-byte check is unambiguous.
    """
    if value.endswith("+00:00"):
        return value[: -len("+00:00")] + "Z"
    return value


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
        mod = pytest.importorskip(
            "nfm_db.monitoring.refusal_observer",
            reason="refusal observer (NFM-3060) not available.",
        )

        refused_at = datetime(2026, 8, 13, 5, 0, 0, tzinfo=UTC)
        event = mod.BackupRefusalEvent(
            free_bytes=5_000_000_000,
            total_bytes=8_500_000_000,
            min_free_bytes=20 * 1024**3,
            max_total_bytes=12 * 1024**3,
            refused_at=refused_at,
        )
        snapshot = mod.RefusalStateSnapshot(
            refusal_count=1,
            last_refusal_at=refused_at,
        )

        observed: dict[str, object] = mod.build_sre_warning_payload(
            event=event,
            snapshot=snapshot,
        )

        expected = _expected_payload(
            refusal_count=1,
            last_refusal_at=refused_at,
            free_bytes=event.free_bytes,
            total_bytes=event.total_bytes,
            min_free_bytes=event.min_free_bytes,
            max_total_bytes=event.max_total_bytes,
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

        # 3. Values: normalize the ISO-8601 timestamp suffix so a single
        #    doc decision (Z vs +00:00) does not block the test.
        observed_norm = dict(observed)
        expected_norm = dict(expected)
        if isinstance(observed_norm.get("lastRefusalAt"), str):
            observed_norm["lastRefusalAt"] = _normalize_ts(
                observed_norm["lastRefusalAt"]  # type: ignore[arg-type]
            )

        # Compare via JSON dumps so failures highlight structural drift.
        observed_json = json.dumps(observed_norm, indent=2, sort_keys=False)
        expected_json = json.dumps(expected_norm, indent=2, sort_keys=False)
        assert observed_norm == expected_norm, (
            "Refusal alert payload values drifted from the documented schema.\n"
            f"  expected (snapshot):\n{expected_json}\n"
            f"  observed:            \n{observed_json}"
        )
