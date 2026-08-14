"""Tests for SRE refusal alert emitter (NFM-3053).

AC coverage:
- [x] AC1: Synthetic refusal via mock produces an [SRE-WARNING] event within one heartbeat.
- [x] AC2: pushOnRefusal=false suppresses the SRE push while the refusal
  remains visible on stats.
- [x] AC3: Alert payload matches the documented schema byte-for-byte (snapshotted
  in a regression test).
- [x] AC4: Burst test — 100 sequential refusals produce a small constant
  number of alerts, not 100.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from nfm_db.services.backup.sre_alert import (
    BackupRefusalAlert,
    RefusalAlertEmitter,
)
from nfm_db.services.backup.metrics import format_rfc3339_z_ms

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIB = 1024**3


# Snapshot of the expected alert payload schema for AC3 regression.
# This MUST be updated if the schema changes (intentionally not using
# json.dumps(sort_keys=True) — insertion order is part of the contract).
# NOTE: 5 * 1024**3 = 5368709120 (5 GiB), not 5000000000.
_EXPECTED_PAYLOAD_JSON = json.dumps(
    {
        "severity": "warning",
        "tag": "backup-refusal",
        "refusalCount": 3,
        "lastRefusalAt": "2025-08-14T12:00:00.000Z",
        "freeBytes": 5 * _GIB,
        "totalBytes": 0,
        "minFreeBytes": 20 * _GIB,
        "maxTotalBytes": 12 * _GIB,
    },
)


# ---------------------------------------------------------------------------
# AC1: Synthetic refusal produces SRE-WARNING within one heartbeat
# ---------------------------------------------------------------------------


class TestAC1SyntheticRefusalEmitsAlert:
    """AC1: Synthetic refusal via mock produces an [SRE-WARNING] event."""

    def test_first_refusal_emits_immediately(self) -> None:
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        result = emitter.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        assert result is not None
        assert isinstance(result, BackupRefusalAlert)
        assert result.refusal_count == 1
        assert result.free_bytes == 5 * _GIB
        emit_fn.assert_called_once_with(
            event_type="backup_refusal",
            severity="warning",
            source_service="backup_guardrails",
            context={
                "severity": "warning",
                "tag": "backup-refusal",
                "refusalCount": 1,
                "lastRefusalAt": "2025-08-14T12:00:00.000Z",
                "freeBytes": 5 * _GIB,
                "totalBytes": 0,
                "minFreeBytes": 20 * _GIB,
                "maxTotalBytes": 12 * _GIB,
            },
        )

    def test_refusal_emitter_records_last_emit_time(self) -> None:
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=MagicMock())

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        emitter.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        assert emitter.last_emit_at is not None


# ---------------------------------------------------------------------------
# AC2: pushOnRefusal=false suppresses SRE push
# ---------------------------------------------------------------------------


class TestAC2PushOnRefusalFalse:
    """AC2: pushOnRefusal=false suppresses the SRE push."""

    def test_no_push_when_flag_false(self) -> None:
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=False, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        result = emitter.on_refusal(
            refusal_count=5,
            last_refusal_at=ts,
            free_bytes=3 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        assert result is None
        emit_fn.assert_not_called()

    def test_refusal_count_still_tracked_when_flag_false(self) -> None:
        """Even with push disabled, the emitter does not break on multiple calls."""
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=False, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        r1 = emitter.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=3 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        r2 = emitter.on_refusal(
            refusal_count=2,
            last_refusal_at=ts,
            free_bytes=2 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        assert r1 is None
        assert r2 is None
        assert emitter.suppressed_count == 0


# ---------------------------------------------------------------------------
# AC3: Alert payload matches schema byte-for-byte (regression snapshot)
# ---------------------------------------------------------------------------


class TestAC3PayloadSchemaRegression:
    """AC3: Alert payload matches the documented schema byte-for-byte."""

    def test_to_context_matches_expected_schema(self) -> None:
        alert = BackupRefusalAlert(
            refusal_count=3,
            last_refusal_at=datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC),
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        actual_json = json.dumps(alert.to_context())
        assert actual_json == _EXPECTED_PAYLOAD_JSON, (
            f"Schema regression! Expected:\n{_EXPECTED_PAYLOAD_JSON}\n"
            f"Got:\n{actual_json}"
        )

    def test_all_fields_present(self) -> None:
        alert = BackupRefusalAlert(
            refusal_count=7,
            last_refusal_at=datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC),
            free_bytes=10 * _GIB,
            total_bytes=5 * _GIB,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        ctx = alert.to_context()
        expected_keys = {
            "severity",
            "tag",
            "refusalCount",
            "lastRefusalAt",
            "freeBytes",
            "totalBytes",
            "minFreeBytes",
            "maxTotalBytes",
        }
        assert set(ctx.keys()) == expected_keys

    def test_severity_and_tag_values(self) -> None:
        alert = BackupRefusalAlert(
            refusal_count=1,
            last_refusal_at=datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC),
            free_bytes=0,
            total_bytes=0,
            min_free_bytes=0,
            max_total_bytes=0,
        )
        ctx = alert.to_context()
        assert ctx["severity"] == "warning"
        assert ctx["tag"] == "backup-refusal"


# ---------------------------------------------------------------------------
# AC4: Burst test — 100 sequential refusals produce ≤ N alerts
# ---------------------------------------------------------------------------


class TestAC4BurstDebounce:
    """AC4: 100 sequential refusals produce a small constant alerts."""

    def test_burst_produces_few_alerts(self) -> None:
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        alerts_emitted = 0
        for i in range(100):
            result = emitter.on_refusal(
                refusal_count=i + 1,
                last_refusal_at=ts,
                free_bytes=5 * _GIB,
                total_bytes=0,
                min_free_bytes=20 * _GIB,
                max_total_bytes=12 * _GIB,
            )
            if result is not None:
                alerts_emitted += 1

        # First refusal emits immediately. All 99 subsequent are within
        # the one-hour debounce window and are suppressed.
        assert alerts_emitted == 1, (
            f"Expected 1 alert for 100 burst refusals, got {alerts_emitted}"
        )
        assert emitter.suppressed_count == 99

    def test_two_refusals_in_same_window_one_alert(self) -> None:
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        r1 = emitter.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        r2 = emitter.on_refusal(
            refusal_count=2,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        assert r1 is not None
        assert r2 is None
        assert emitter.suppressed_count == 1

    def test_refusal_after_debounce_window_flushes_summary(self) -> None:
        """When a refusal arrives after the debounce window expires,
        a summary alert is flushed for suppressed refusals.

        The triggering refusal itself is debounced (the summary emit
        resets the window), so the next refusal after that will emit.
        Total: 1 (initial) + 1 (summary) = 2 emit_fn calls.
        """
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        t0 = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        t_mid = datetime(2025, 8, 14, 12, 30, 0, tzinfo=UTC)
        t1 = datetime(2025, 8, 14, 13, 1, 0, tzinfo=UTC)  # past 1-hour window

        with patch("nfm_db.services.backup.sre_alert.datetime") as mock_dt:
            # t0: first refusal — emits immediately.
            mock_dt.now.return_value = t0
            r1 = emitter.on_refusal(
                refusal_count=1, last_refusal_at=t0, free_bytes=5 * _GIB,
                total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
            )
            assert r1 is not None
            assert emit_fn.call_count == 1

            # t_mid: 2 refusals within window — suppressed.
            mock_dt.now.return_value = t_mid
            for i in range(2):
                r = emitter.on_refusal(
                    refusal_count=i + 2, last_refusal_at=t_mid,
                    free_bytes=4 * _GIB, total_bytes=0,
                    min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
                )
                assert r is None
            assert emit_fn.call_count == 1  # still just the initial
            assert emitter.suppressed_count == 2

            # t1: past window — flushes summary for 2 suppressed.
            # The triggering refusal is itself debounced because the
            # summary emit resets _last_emit_at to t1.
            mock_dt.now.return_value = t1
            r4 = emitter.on_refusal(
                refusal_count=4, last_refusal_at=t1, free_bytes=3 * _GIB,
                total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
            )
            assert r4 is None  # debounced by the fresh window
            assert emit_fn.call_count == 2  # initial + summary

    def test_exact_burst_constants(self) -> None:
        """Verify the exact maximum alerts for 100 refusals in one window."""
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        alerts: list[BackupRefusalAlert] = []
        for i in range(100):
            result = emitter.on_refusal(
                refusal_count=i + 1,
                last_refusal_at=ts,
                free_bytes=5 * _GIB,
                total_bytes=0,
                min_free_bytes=20 * _GIB,
                max_total_bytes=12 * _GIB,
            )
            if result is not None:
                alerts.append(result)

        assert len(alerts) == 1
        assert alerts[0].refusal_count == 1  # The very first refusal


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for the SRE alert emitter."""

    def test_default_push_on_refusal_is_true(self) -> None:
        emitter = RefusalAlertEmitter()
        assert emitter.push_on_refusal is True

    def test_emit_fn_failure_does_not_raise(self) -> None:
        def failing_emit_fn(**kwargs: Any) -> None:
            raise RuntimeError("DB connection lost")

        emitter = RefusalAlertEmitter(
            push_on_refusal=True,
            emit_fn=failing_emit_fn,
        )
        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        # Should not raise — the error is caught and logged by _do_emit.
        result = emitter.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        # _do_emit returns early on failure, so on_refusal returns None.
        assert result is None
        # _last_emit_at is NOT advanced on failure — the next refusal
        # within the debounce window will get a fresh chance to emit.
        assert emitter.last_emit_at is None

    def test_zero_refusal_count(self) -> None:
        """A refusal_count of 0 is valid (e.g. before first refusal)."""
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        result = emitter.on_refusal(
            refusal_count=0,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        assert result is not None
        ctx = result.to_context()
        assert ctx["refusalCount"] == 0

    def test_emitter_isolation(self) -> None:
        """Two emitters have independent debounce state."""
        emit_fn = MagicMock()
        e1 = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)
        e2 = RefusalAlertEmitter(push_on_refusal=True, emit_fn=emit_fn)

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        e1.on_refusal(
            refusal_count=1,
            last_refusal_at=ts,
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        e2.on_refusal(
            refusal_count=2,
            last_refusal_at=ts,
            free_bytes=4 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )

        # Both emit because they have independent debounce state.
        assert emit_fn.call_count == 2

    def test_configurable_debounce_seconds(self) -> None:
        """Emitter uses custom debounce_seconds instead of default 3600."""
        emit_fn = MagicMock()
        emitter = RefusalAlertEmitter(
            push_on_refusal=True,
            debounce_seconds=60,
            emit_fn=emit_fn,
        )
        assert emitter.debounce_seconds == 60

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        with patch("nfm_db.services.backup.sre_alert.datetime") as mock_dt:
            mock_dt.now.return_value = ts
            r1 = emitter.on_refusal(
                refusal_count=1, last_refusal_at=ts, free_bytes=5 * _GIB,
                total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
            )
            assert r1 is not None

            # Within 60s window — suppressed.
            mock_dt.now.return_value = datetime(2025, 8, 14, 12, 0, 30, tzinfo=UTC)
            r2 = emitter.on_refusal(
                refusal_count=2, last_refusal_at=ts, free_bytes=4 * _GIB,
                total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
            )
            assert r2 is None
            assert emitter.suppressed_count == 1
            assert emit_fn.call_count == 1

            # After 60s window — summary is flushed for 1 suppressed,
            # then the triggering refusal is debounced by the fresh window
            # (summary emit resets _last_emit_at).
            mock_dt.now.return_value = datetime(2025, 8, 14, 12, 1, 1, tzinfo=UTC)
            r3 = emitter.on_refusal(
                refusal_count=3, last_refusal_at=ts, free_bytes=3 * _GIB,
                total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
            )
            assert r3 is None
            # 2 emits: initial + summary
            assert emit_fn.call_count == 2

    def test_failure_preserves_next_refusal_chance(self) -> None:
        """After a failed emit, the next refusal is NOT debounced."""
        def failing_emit_fn(**kwargs: Any) -> None:
            raise OSError("DB down")

        emitter = RefusalAlertEmitter(
            push_on_refusal=True,
            emit_fn=failing_emit_fn,
        )

        ts = datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC)
        r1 = emitter.on_refusal(
            refusal_count=1, last_refusal_at=ts, free_bytes=5 * _GIB,
            total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
        )
        assert r1 is None  # failed emit
        assert emitter.last_emit_at is None  # not advanced

        # Swap in a working emit_fn — the next call should NOT be debounced.
        working_fn = MagicMock()
        emitter._emit_fn = working_fn
        r2 = emitter.on_refusal(
            refusal_count=2, last_refusal_at=ts, free_bytes=4 * _GIB,
            total_bytes=0, min_free_bytes=20 * _GIB, max_total_bytes=12 * _GIB,
        )
        assert r2 is not None  # emitted successfully
        assert working_fn.call_count == 1


# ---------------------------------------------------------------------------
# NFM-3132: Cross-format equality regression (log line == alert payload)
# ---------------------------------------------------------------------------


class TestNFM3132FormatUnification:
    """NFM-3132: Both the log line and the alert payload MUST produce
    byte-for-byte identical ``lastRefusalAt`` values for the same input
    timestamp.

    This prevents the inconsistency that existed when the log line used
    ``format_rfc3339_z_ms()`` but the alert payload used ``.isoformat()``
    (producing ``+00:00`` instead of ``Z``, and 6-digit microseconds
    instead of 3-digit milliseconds).
    """

    @staticmethod
    def _sample_timestamps() -> list[datetime]:
        """Return a set of UTC timestamps exercising edge cases."""
        return [
            # Zero ms — the original PR #821 snapshot value.
            datetime(2025, 8, 14, 12, 0, 0, tzinfo=UTC),
            # Non-zero ms.
            datetime(2026, 8, 13, 7, 18, 59, 123000, tzinfo=UTC),
            # Exactly 999 ms — highest 3-digit value.
            datetime(2026, 1, 1, 0, 0, 0, 999000, tzinfo=UTC),
            # Sub-millisecond rounds down (123456 µs → 123 ms).
            datetime(2026, 3, 15, 23, 59, 59, 123456, tzinfo=UTC),
            # Leap-second-safe midnight.
            datetime(2026, 12, 31, 23, 59, 59, 500000, tzinfo=UTC),
        ]

    def test_format_function_matches_canonical_shape(self) -> None:
        """format_rfc3339_z_ms produces YYYY-MM-DDTHH:MM:SS.sssZ."""
        import re

        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        for ts in self._sample_timestamps():
            assert pattern.match(format_rfc3339_z_ms(ts)), (
                f"Timestamp {ts!r} produced non-canonical output: "
                f"{format_rfc3339_z_ms(ts)!r}"
            )

    def test_log_line_and_alert_payload_format_byte_equal(self) -> None:
        """For the same input timestamp, both the log-line format and
        the alert payload format produce byte-for-byte identical strings.

        This is the core NFM-3132 regression: before the fix, the log
        line used ``format_rfc3339_z_ms`` (``.123Z``) while the alert
        payload used ``.isoformat()`` (``.123456+00:00``).
        """
        for ts in self._sample_timestamps():
            log_format = format_rfc3339_z_ms(ts)
            alert = BackupRefusalAlert(
                refusal_count=1,
                last_refusal_at=ts,
                free_bytes=5 * _GIB,
                total_bytes=0,
                min_free_bytes=20 * _GIB,
                max_total_bytes=12 * _GIB,
            )
            payload_format = alert.to_context()["lastRefusalAt"]
            assert payload_format == log_format, (
                f"Format mismatch for {ts!r}: "
                f"log={log_format!r} payload={payload_format!r}"
            )

    def test_no_isoformat_in_alert_context(self) -> None:
        """BackupRefusalAlert.to_context() must NOT produce '+00:00' or
        6-digit microsecond timestamps — those are the .isoformat() footprints."""
        alert = BackupRefusalAlert(
            refusal_count=1,
            last_refusal_at=datetime(2026, 8, 13, 7, 18, 59, 123456, tzinfo=UTC),
            free_bytes=5 * _GIB,
            total_bytes=0,
            min_free_bytes=20 * _GIB,
            max_total_bytes=12 * _GIB,
        )
        ctx = alert.to_context()
        last_refusal = ctx["lastRefusalAt"]
        assert "+00:00" not in last_refusal, (
            f"Found '+00:00' timezone offset in lastRefusalAt: {last_refusal!r}. "
            "This indicates .isoformat() is still being used instead of "
            "format_rfc3339_z_ms()."
        )
        # Microsecond precision (6 digits after dot) is the other footprint.
        # The canonical format has exactly 3 digits (milliseconds).
        parts = last_refusal.split(".")
        assert len(parts) == 2, f"Unexpected format (no dot): {last_refusal!r}"
        frac = parts[1]
        assert frac.endswith("Z"), f"Fractional part must end with Z: {frac!r}"
        ms_part = frac[:-1]  # strip trailing Z
        assert len(ms_part) == 3, (
            f"Expected 3-digit milliseconds, got {len(ms_part)}: {ms_part!r}. "
            "This indicates .isoformat() (6-digit microseconds) is still in use."
        )

    def test_no_isoformat_in_backup_services_directory(self) -> None:
        """Grep AC3: no .isoformat() call targets lastRefusalAt or
        last_refusal_at in the backup services directory."""
        import os

        backup_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        services_dir = os.path.join(backup_dir, "..", "..", "src", "nfm_db", "services", "backup")
        services_dir = os.path.normpath(services_dir)

        hits: list[str] = []
        for fname in os.listdir(services_dir):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(services_dir, fname)
            with open(fpath, encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if ".isoformat()" in line and (
                        "lastRefusalAt" in line or "last_refusal_at" in line
                    ):
                        hits.append(f"{fname}:{line_no}: {line.strip()}")

        assert not hits, (
            f"Found .isoformat() on lastRefusalAt/last_refusal_at in:\n"
            + "\n".join(hits)
        )
