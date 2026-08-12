"""Tests for backup config schema — NFM-3014.

Covers:
- Tiered retention model construction and validation
- Deprecation warning when legacy retentionDays is set without new retention
- Capacity guardrail defaults (maxTotalBytes, minFreeBytes, refuseOnFloorBreach)
- Validation rejection of invalid values (negative counts, zero intervals)
"""

import logging

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# RetentionTier
# ---------------------------------------------------------------------------
class TestRetentionTier:
    """Tests for the RetentionTier Pydantic model."""

    def test_default_hourly_tier(self):
        """Hourly tier: intervalMinutes=60, count=24."""
        from nfm_db.config.backup import RetentionTier

        tier = RetentionTier(interval_minutes=60, count=24)
        assert tier.interval_minutes == 60
        assert tier.count == 24

    def test_rejects_zero_interval(self):
        """Zero intervalMinutes must be rejected."""
        from nfm_db.config.backup import RetentionTier

        with pytest.raises(ValidationError, match="interval_minutes"):
            RetentionTier(interval_minutes=0, count=24)

    def test_rejects_negative_interval(self):
        """Negative intervalMinutes must be rejected."""
        from nfm_db.config.backup import RetentionTier

        with pytest.raises(ValidationError, match="interval_minutes"):
            RetentionTier(interval_minutes=-1, count=24)

    def test_rejects_zero_count(self):
        """Zero count must be rejected."""
        from nfm_db.config.backup import RetentionTier

        with pytest.raises(ValidationError, match="count"):
            RetentionTier(interval_minutes=60, count=0)

    def test_rejects_negative_count(self):
        """Negative count must be rejected."""
        from nfm_db.config.backup import RetentionTier

        with pytest.raises(ValidationError, match="count"):
            RetentionTier(interval_minutes=60, count=-1)

    def test_accepts_positive_values(self):
        """Valid positive values are accepted."""
        from nfm_db.config.backup import RetentionTier

        tier = RetentionTier(interval_minutes=10080, count=4)
        assert tier.interval_minutes == 10080
        assert tier.count == 4


# ---------------------------------------------------------------------------
# TieredRetention
# ---------------------------------------------------------------------------
class TestTieredRetention:
    """Tests for the TieredRetention model."""

    def test_default_tiers(self):
        """Default tiers: hourly=24, daily=7, weekly=4."""
        from nfm_db.config.backup import TieredRetention

        retention = TieredRetention()
        assert retention.hourly.interval_minutes == 60
        assert retention.hourly.count == 24
        assert retention.daily.interval_minutes == 1440
        assert retention.daily.count == 7
        assert retention.weekly.interval_minutes == 10080
        assert retention.weekly.count == 4

    def test_custom_tiers(self):
        """Custom tier values override defaults."""
        from nfm_db.config.backup import RetentionTier, TieredRetention

        retention = TieredRetention(
            hourly=RetentionTier(interval_minutes=60, count=48),
            daily=RetentionTier(interval_minutes=1440, count=14),
            weekly=RetentionTier(interval_minutes=10080, count=8),
        )
        assert retention.hourly.count == 48
        assert retention.daily.count == 14
        assert retention.weekly.count == 8

    def test_invalid_hourly_propagates(self):
        """Invalid hourly tier is caught at TieredRetention level."""
        from nfm_db.config.backup import TieredRetention

        with pytest.raises(ValidationError):
            TieredRetention(hourly={"interval_minutes": 0, "count": 24})


# ---------------------------------------------------------------------------
# BackupConfig
# ---------------------------------------------------------------------------
class TestBackupConfig:
    """Tests for the BackupConfig model."""

    def test_default_capacity_guardrails(self):
        """maxTotalBytes=12 GiB, minFreeBytes=20 GiB, refuseOnFloorBreach=true."""
        from nfm_db.config.backup import BackupConfig

        config = BackupConfig()
        assert config.max_total_bytes == 12_884_901_888  # 12 GiB
        assert config.min_free_bytes == 21_474_836_480  # 20 GiB
        assert config.refuse_on_floor_breach is True

    def test_retention_defaults_to_none(self):
        """retention defaults to None — operator must opt in."""
        from nfm_db.config.backup import BackupConfig

        config = BackupConfig()
        assert config.retention is None

    def test_retention_can_be_explicitly_set(self):
        """Retention can be explicitly configured."""
        from nfm_db.config.backup import BackupConfig, TieredRetention

        config = BackupConfig(retention=TieredRetention())
        assert config.retention is not None
        assert config.retention.hourly.count == 24

    def test_legacy_retention_days_default_none(self):
        """retentionDays defaults to None (not set)."""
        from nfm_db.config.backup import BackupConfig

        config = BackupConfig()
        assert config.retention_days is None

    def test_custom_max_total_bytes(self):
        """Custom maxTotalBytes overrides default."""
        from nfm_db.config.backup import BackupConfig

        config = BackupConfig(max_total_bytes=1_073_741_824)
        assert config.max_total_bytes == 1_073_741_824  # 1 GiB

    def test_rejects_negative_max_total_bytes(self):
        """Negative maxTotalBytes must be rejected."""
        from nfm_db.config.backup import BackupConfig

        with pytest.raises(ValidationError, match="max_total_bytes"):
            BackupConfig(max_total_bytes=-1)

    def test_rejects_negative_min_free_bytes(self):
        """Negative minFreeBytes must be rejected."""
        from nfm_db.config.backup import BackupConfig

        with pytest.raises(ValidationError, match="min_free_bytes"):
            BackupConfig(min_free_bytes=-1)


# ---------------------------------------------------------------------------
# Deprecation warning
# ---------------------------------------------------------------------------
class TestDeprecationWarning:
    """Tests for retentionDays deprecation detection."""

    def test_warns_when_retention_days_set_without_retention(self, caplog):
        """[DEPRECATION] logged when retentionDays is set but no retention tiers."""
        from nfm_db.config.backup import BackupConfig, check_retention_deprecation

        config = BackupConfig(retention_days=7, retention=None)
        with caplog.at_level(logging.WARNING):
            check_retention_deprecation(config)

        assert "[DEPRECATION]" in caplog.text
        assert "backup.retentionDays" in caplog.text
        assert "backup.retention.{hourly,daily,weekly}" in caplog.text

    def test_no_warning_when_retention_tiers_present(self, caplog):
        """No deprecation warning when new retention object is present."""
        from nfm_db.config.backup import BackupConfig, TieredRetention, check_retention_deprecation

        config = BackupConfig(retention_days=7, retention=TieredRetention())
        with caplog.at_level(logging.WARNING):
            check_retention_deprecation(config)

        assert "[DEPRECATION]" not in caplog.text

    def test_no_warning_when_retention_days_none(self, caplog):
        """No deprecation warning when retentionDays is None."""
        from nfm_db.config.backup import BackupConfig, check_retention_deprecation

        config = BackupConfig(retention_days=None)
        with caplog.at_level(logging.WARNING):
            check_retention_deprecation(config)

        assert "[DEPRECATION]" not in caplog.text

    def test_no_warning_when_both_present(self, caplog):
        """No warning when both retentionDays and retention object are set."""
        from nfm_db.config.backup import BackupConfig, TieredRetention, check_retention_deprecation

        config = BackupConfig(retention_days=7, retention=TieredRetention())
        with caplog.at_level(logging.WARNING):
            check_retention_deprecation(config)

        assert "[DEPRECATION]" not in caplog.text
