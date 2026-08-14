"""Tests for Amendment 5 — lastRefusalAt RFC-3339 UTC Z format (ADR D4).

AC: The alert payload gated by ``_should_push_on_refusal()`` emits
``lastRefusalAt`` in RFC-3339 UTC with a ``Z`` suffix and millisecond
precision (e.g. ``2026-08-13T07:18:59.123Z``). One form only — no
normalisation of ``Z`` vs ``+00:00`` at compare time.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import CapacityGuardrails, DiskUsage
from nfm_db.services.backup.metrics import (
    BackupMetrics,
    format_rfc3339_z_ms,
)

_GIB = 1024**3

# RFC-3339 UTC Z with millisecond precision: 2026-08-13T07:18:59.123Z
_RFC3339_Z_MS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def _make_disk(*, free: int, total_backup: int) -> DiskUsage:
    return DiskUsage(free_bytes=free, total_backup_bytes=total_backup)


# ---------------------------------------------------------------------------
# format_rfc3339_z_ms — the canonical formatter
# ---------------------------------------------------------------------------


class TestFormatRfc3339ZMs:
    """The formatter produces exactly one form: RFC-3339 UTC Z with ms."""

    def test_output_matches_regex(self) -> None:
        dt = datetime(2026, 8, 13, 7, 18, 59, 123000, tzinfo=UTC)
        result = format_rfc3339_z_ms(dt)
        assert _RFC3339_Z_MS_RE.match(result), f"bad format: {result}"

    def test_exact_output(self) -> None:
        dt = datetime(2026, 8, 13, 7, 18, 59, 123000, tzinfo=UTC)
        assert format_rfc3339_z_ms(dt) == "2026-08-13T07:18:59.123Z"

    def test_single_digit_month_day_hour(self) -> None:
        dt = datetime(2026, 1, 5, 3, 4, 5, 6000, tzinfo=UTC)
        assert format_rfc3339_z_ms(dt) == "2026-01-05T03:04:05.006Z"

    def test_truncates_microseconds_to_millis(self) -> None:
        dt = datetime(2026, 8, 13, 7, 18, 59, 123456, tzinfo=UTC)
        assert format_rfc3339_z_ms(dt) == "2026-08-13T07:18:59.123Z"

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            format_rfc3339_z_ms(datetime(2026, 8, 13, 7, 18, 59))

    def test_rejects_non_utc_timezone(self) -> None:
        from datetime import timedelta

        tz_plus5 = timezone(timedelta(hours=5))
        dt = datetime(2026, 8, 13, 7, 18, 59, 123000, tzinfo=tz_plus5)
        with pytest.raises(ValueError, match="UTC"):
            format_rfc3339_z_ms(dt)

    def test_always_z_suffix(self) -> None:
        """Never emits +00:00 — always Z."""
        dt = datetime(2026, 8, 13, 0, 0, 0, 0, tzinfo=UTC)
        result = format_rfc3339_z_ms(dt)
        assert result.endswith("Z"), f"must end with Z, got: {result}"
        assert "+00:00" not in result


# ---------------------------------------------------------------------------
# BackupMetricsSnapshot.last_refusal_at_rfc3339
# ---------------------------------------------------------------------------


class TestSnapshotRfc3339Field:
    """The snapshot exposes last_refusal_at_rfc3339 for the observer."""

    def test_returns_formatted_string_after_refusal(self) -> None:
        metrics = BackupMetrics()
        metrics.record_refusal(
            free_bytes=5 * _GIB, backup_size=1 * _GIB, floor=20 * _GIB
        )
        snap = metrics.snapshot()
        assert snap.last_refusal_at_rfc3339 is not None
        assert _RFC3339_Z_MS_RE.match(snap.last_refusal_at_rfc3339)

    def test_returns_none_when_no_refusal(self) -> None:
        snap = BackupMetrics().snapshot()
        assert snap.last_refusal_at_rfc3339 is None

    def test_consistent_with_datetime_property(self) -> None:
        """Formatting the datetime property directly gives the same result."""
        metrics = BackupMetrics()
        dt = metrics.record_refusal(
            free_bytes=5 * _GIB, backup_size=1 * _GIB, floor=20 * _GIB
        )
        snap = metrics.snapshot()
        assert snap.last_refusal_at_rfc3339 == format_rfc3339_z_ms(dt)


# ---------------------------------------------------------------------------
# SRE-WARNING log line includes lastRefusalAt
# ---------------------------------------------------------------------------


class TestSreWarningIncludesLastRefusalAt:
    """The SRE-WARNING log line must include lastRefusalAt in RFC-3339 Z ms."""

    def test_floor_breach_log_has_last_refusal_at(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = BackupCapacityConfig()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        with caplog.at_level(
            logging.WARNING, logger="nfm_db.services.backup.guardrails"
        ):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert "[SRE-WARNING]" in caplog.text
        for record in caplog.records:
            if "[SRE-WARNING]" in record.message:
                assert "lastRefusalAt=" in record.message
                match = re.search(r"lastRefusalAt=(\S+)", record.message)
                assert match is not None
                assert _RFC3339_Z_MS_RE.match(match.group(1)), (
                    f"lastRefusalAt format wrong: {match.group(1)}"
                )
                break

    def test_post_pruner_log_has_last_refusal_at(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = BackupCapacityConfig()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=5 * _GIB, total_backup=0)

        with caplog.at_level(
            logging.WARNING, logger="nfm_db.services.backup.guardrails"
        ):
            gr.recheck_floor_after_pruner(disk=disk)

        assert "[SRE-WARNING]" in caplog.text
        for record in caplog.records:
            if "[SRE-WARNING]" in record.message:
                assert "lastRefusalAt=" in record.message
                break
