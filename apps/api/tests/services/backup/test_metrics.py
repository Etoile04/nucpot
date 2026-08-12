"""Tests for BackupMetrics (NFM-3016)."""

from __future__ import annotations

from datetime import datetime, timezone

from nfm_db.services.backup.metrics import BackupMetrics, BackupMetricsSnapshot


class TestBackupMetricsInitial:
    def test_initial_refusal_count_is_zero(self) -> None:
        assert BackupMetrics().refusal_count == 0

    def test_initial_last_refusal_at_is_none(self) -> None:
        assert BackupMetrics().last_refusal_at is None


class TestBackupMetricsRecordRefusal:
    def test_refusal_increments_count(self) -> None:
        metrics = BackupMetrics()
        metrics.record_refusal(free_bytes=100, backup_size=50, floor=200)
        assert metrics.refusal_count == 1

    def test_multiple_refusals_accumulate(self) -> None:
        metrics = BackupMetrics()
        metrics.record_refusal(free_bytes=100, backup_size=50, floor=200)
        metrics.record_refusal(free_bytes=80, backup_size=50, floor=200)
        assert metrics.refusal_count == 2

    def test_records_timestamp(self) -> None:
        before = datetime.now(timezone.utc)
        metrics = BackupMetrics()
        metrics.record_refusal(free_bytes=100, backup_size=50, floor=200)
        after = datetime.now(timezone.utc)
        assert before <= metrics.last_refusal_at <= after

    def test_snapshot_returns_immutable_copy(self) -> None:
        metrics = BackupMetrics()
        metrics.record_refusal(free_bytes=100, backup_size=50, floor=200)
        snap = metrics.snapshot()
        assert isinstance(snap, BackupMetricsSnapshot)
        assert snap.refusal_count == 1
        assert snap.last_refusal_at == metrics.last_refusal_at
