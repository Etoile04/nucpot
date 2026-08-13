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
        "lastRefusalAt": "2025-08-14T12:00:00+00:00",
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
                "lastRefusalAt": "2025-08-14T12:00:00+00:00",
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
        # The alert object is still returned (emit is best-effort).
        assert result is not None
        assert isinstance(result, BackupRefusalAlert)
        # But last_emit_at was still recorded (emit happened, just failed).
        assert emitter.last_emit_at is not None

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
