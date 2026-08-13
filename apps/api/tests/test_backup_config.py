"""Schema validator tests for the backup config layer.

NFM-3066 / NFM-3024-A1: retention config object + startup deprecation warning.
Covers acceptance criteria AC1 and AC4 (canonical shape accepted, malformed
retention rejected).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.backup.config import BackupConfig, RetentionConfig, TierSpec


def _canonical_retention_payload() -> dict:
    """Return the canonical retention block from NFM-3024 PRD."""
    return {
        "hourly": {"intervalMinutes": 60, "count": 24},
        "daily": {"intervalMinutes": 1440, "count": 7},
        "weekly": {"intervalMinutes": 10080, "count": 4},
    }


def _minimal_backup_payload(retention: dict | None) -> dict:
    """Return a minimal BackupConfig payload honouring ``retention``."""
    return {
        "enabled": True,
        "intervalMinutes": 60,
        "dir": "/var/nfm-data/backups",
        "retention": retention,
        "maxTotalBytes": 12884901888,  # 12 GiB
        "minFreeBytes": 21474836480,  # 20 GiB
        "refuseOnFloorBreach": True,
        "metrics": {"pushOnRefusal": True},
    }


class TestTierSpec:
    """Per-tier retention sub-object: ``intervalMinutes`` and ``count``."""

    def test_accepts_positive_interval_and_count(self) -> None:
        spec = TierSpec(intervalMinutes=60, count=24)
        assert spec.intervalMinutes == 60
        assert spec.count == 24

    def test_rejects_zero_interval(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(intervalMinutes=0, count=24)

    def test_rejects_negative_interval(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(intervalMinutes=-60, count=24)

    def test_rejects_zero_count(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(intervalMinutes=60, count=0)

    def test_rejects_negative_count(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(intervalMinutes=60, count=-7)


class TestRetentionConfig:
    """Top-level retention object: hourly / daily / weekly tiers."""

    def test_accepts_canonical_three_tier_shape(self) -> None:
        cfg = RetentionConfig(**_canonical_retention_payload())
        assert cfg.hourly.intervalMinutes == 60
        assert cfg.daily.count == 7
        assert cfg.weekly.intervalMinutes == 10080

    def test_rejects_missing_tier(self) -> None:
        partial = _canonical_retention_payload()
        partial.pop("weekly")
        with pytest.raises(ValidationError):
            RetentionConfig(**partial)

    def test_rejects_malformed_inner_tier(self) -> None:
        # Negative count nested inside one tier must propagate.
        payload = _canonical_retention_payload()
        payload["daily"] = {"intervalMinutes": 1440, "count": -7}
        with pytest.raises(ValidationError):
            RetentionConfig(**payload)

    def test_rejects_zero_interval_nested(self) -> None:
        payload = _canonical_retention_payload()
        payload["hourly"] = {"intervalMinutes": 0, "count": 24}
        with pytest.raises(ValidationError):
            RetentionConfig(**payload)


class TestBackupConfigAcceptsCanonicalShape:
    """AC1: ``config.json`` validates with the new ``retention`` object schema."""

    def test_full_payload_with_retention_validates(self) -> None:
        payload = _minimal_backup_payload(_canonical_retention_payload())
        cfg = BackupConfig(**payload)
        assert cfg.enabled is True
        assert cfg.retention is not None
        assert cfg.retention.daily.count == 7


class TestBackupConfigRejectsMalformedRetention:
    """AC4: schema validator rejects malformed retention."""

    def test_negative_count_in_nested_tier_rejected(self) -> None:
        payload = _minimal_backup_payload(_canonical_retention_payload())
        assert payload["retention"] is not None
        payload["retention"]["weekly"] = {"intervalMinutes": 10080, "count": -4}
        with pytest.raises(ValidationError):
            BackupConfig(**payload)

    def test_zero_interval_in_nested_tier_rejected(self) -> None:
        payload = _minimal_backup_payload(_canonical_retention_payload())
        assert payload["retention"] is not None
        payload["retention"]["hourly"] = {"intervalMinutes": 0, "count": 24}
        with pytest.raises(ValidationError):
            BackupConfig(**payload)

    def test_missing_tier_in_retention_rejected(self) -> None:
        payload = _minimal_backup_payload(
            {"hourly": {"intervalMinutes": 60, "count": 24}}
        )
        with pytest.raises(ValidationError):
            BackupConfig(**payload)
