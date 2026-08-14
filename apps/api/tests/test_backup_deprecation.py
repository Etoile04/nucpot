"""Deprecation-warning tests for the legacy ``retentionDays`` alias.

NFM-3066 / NFM-3024-A1: emits one WARN-level log line at startup when only
``retentionDays`` is configured (no ``retention`` block). The WARN must fire
at most once per process start.

Covers AC2 and AC3.
"""

from __future__ import annotations

import logging

import pytest

from nfm_db.backup import config as backup_config
from nfm_db.backup.config import BackupConfig


@pytest.fixture(autouse=True)
def _reset_deprecation_flag() -> None:
    """Reset the module-level once-guard between tests.

    The deprecation log is fired at most once per process start. Each test
    needs to start from a clean slate so we can verify both the
    'fires once' contract and that multiple instances do not re-emit.
    """
    backup_config.reset_retention_days_warned()


def _legacy_payload(retention_days: int = 7) -> dict:
    return {
        "enabled": True,
        "intervalMinutes": 60,
        "dir": "/var/nfm-data/backups",
        "retentionDays": retention_days,
        "maxTotalBytes": 12884901888,
        "minFreeBytes": 21474836480,
        "refuseOnFloorBreach": True,
        "metrics": {"pushOnRefusal": True},
    }


class TestLegacyRetentionDays:
    """AC2: legacy ``retentionDays`` boots successfully + emits WARN."""

    def test_legacy_retention_days_boots_successfully(self) -> None:
        cfg = BackupConfig(**_legacy_payload(retention_days=7))
        # Downstream consumers (NFM-3050 scheduler) MUST see a derived
        # ``retention`` block; the legacy flat mode is implemented as
        # ``retentionDays`` daily tiers.
        assert cfg.retention is not None
        assert cfg.retention.daily.count == 7
        assert cfg.retention.hourly.count > 0
        assert cfg.retention.weekly.count > 0

    def test_legacy_retention_days_emits_single_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=backup_config.__name__):
            BackupConfig(**_legacy_payload(retention_days=7))

        # AC2: exactly one WARN log line naming ``retentionDays``.
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) == 1
        assert "retentionDays" in warn_records[0].getMessage()

    def test_legacy_retention_days_warn_message_shape(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=backup_config.__name__):
            BackupConfig(**_legacy_payload(retention_days=14))

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) == 1
        message = warn_records[0].getMessage()
        # AC2: must call out the deprecated field AND the migration target.
        assert "retentionDays" in message
        assert "backup.retention" in message
        assert "deprecated" in message


class TestDeprecationOnceGuard:
    """AC3: at most one WARN per process start."""

    def test_multiple_instances_emit_only_one_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=backup_config.__name__):
            for _ in range(5):
                BackupConfig(**_legacy_payload(retention_days=7))

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) == 1

    def test_canonical_retention_emits_no_warn(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        payload = {
            "enabled": True,
            "intervalMinutes": 60,
            "dir": "/var/nfm-data/backups",
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
            "maxTotalBytes": 12884901888,
            "minFreeBytes": 21474836480,
            "refuseOnFloorBreach": True,
            "metrics": {"pushOnRefusal": True},
        }
        with caplog.at_level(logging.WARNING, logger=backup_config.__name__):
            cfg = BackupConfig(**payload)

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warn_records == []
        assert cfg.retention is not None
        assert cfg.retention.daily.count == 7

    def test_explicit_retention_with_legacy_alias_uses_explicit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When both fields are present, ``retention`` wins and no WARN fires."""
        payload = {
            "enabled": True,
            "intervalMinutes": 60,
            "dir": "/var/nfm-data/backups",
            "retentionDays": 7,
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
            "maxTotalBytes": 12884901888,
            "minFreeBytes": 21474836480,
            "refuseOnFloorBreach": True,
            "metrics": {"pushOnRefusal": True},
        }
        with caplog.at_level(logging.WARNING, logger=backup_config.__name__):
            BackupConfig(**payload)

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warn_records == []
