"""Tests for backup retention tier engine (NFM-3036).

Covers:
- Pydantic config schema validation (new + deprecated old schema)
- Deprecation warning emission
- classifyTier correctness for all tiers
- Edge cases: too few files, empty list, exact tier boundaries
- Tier promotion as files age
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nfm_db.backup.retention_config import (
    BackupConfig,
    RetentionConfig,
    TierConfig,
    load_backup_config,
)
from nfm_db.backup.retention_tier import (
    BackupFile,
    ClassifiedFile,
    Tier,
    classify_tier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(hours_ago: float) -> datetime:
    """Return a UTC datetime *hours_ago* hours before now."""
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


def _make_files(count: int, newest_hours_ago: float = 0.5) -> list[BackupFile]:
    """Create *count* BackupFile objects spaced 1 hour apart, newest first."""
    return [
        BackupFile(
            path=f"/data/backup/db_{i:04d}.sql.gz",
            size_bytes=1024 * 1024,
            modified_at=_ts(newest_hours_ago + i),
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# TierConfig
# ---------------------------------------------------------------------------


class TestTierConfig:
    def test_accepts_valid_config(self) -> None:
        cfg = TierConfig(interval_minutes=60, count=24)
        assert cfg.interval_minutes == 60
        assert cfg.count == 24

    def test_rejects_zero_interval(self) -> None:
        with pytest.raises(ValidationError):
            TierConfig(interval_minutes=0, count=24)

    def test_rejects_negative_count(self) -> None:
        with pytest.raises(ValidationError):
            TierConfig(interval_minutes=60, count=-1)


# ---------------------------------------------------------------------------
# RetentionConfig
# ---------------------------------------------------------------------------


class TestRetentionConfig:
    def _default_retention(self) -> RetentionConfig:
        return RetentionConfig(
            hourly=TierConfig(interval_minutes=60, count=24),
            daily=TierConfig(interval_minutes=1440, count=7),
            weekly=TierConfig(interval_minutes=10080, count=4),
        )

    def test_total_slots(self) -> None:
        cfg = self._default_retention()
        assert cfg.total_slots == 35  # 24 + 7 + 4

    def test_from_dict_new_schema(self) -> None:
        data = {
            "hourly": {"intervalMinutes": 60, "count": 24},
            "daily": {"intervalMinutes": 1440, "count": 7},
            "weekly": {"intervalMinutes": 10080, "count": 4},
        }
        cfg = RetentionConfig.model_validate(data)
        assert cfg.hourly.count == 24
        assert cfg.daily.count == 7
        assert cfg.weekly.count == 4

    def test_from_dict_deprecated_retention_days(self) -> None:
        """When only retentionDays is present, derive hourly count."""
        data = {"retention_days": 7}
        cfg = RetentionConfig.model_validate(data)
        assert cfg.hourly.interval_minutes == 60
        assert cfg.hourly.count == 168
        assert cfg.daily.count == 0
        assert cfg.weekly.count == 0


# ---------------------------------------------------------------------------
# BackupConfig (top-level)
# ---------------------------------------------------------------------------


class TestBackupConfig:
    def test_full_config_round_trip(self) -> None:
        data = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
            "maxTotalBytes": 12884901888,
            "minFreeBytes": 21474836480,
            "refuseOnFloorBreach": True,
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg.retention.hourly.count == 24
        assert cfg.max_total_bytes == 12884901888
        assert cfg.min_free_bytes == 21474836480
        assert cfg.refuse_on_floor_breach is True

    def test_deprecated_retention_days_alias(self) -> None:
        data = {
            "retention_days": 7,
            "maxTotalBytes": 5000000000,
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg.retention.hourly.count == 168
        assert cfg.max_total_bytes == 5000000000

    def test_deprecated_flag_set_when_retention_days_used(self) -> None:
        data = {"retention_days": 7}
        cfg = BackupConfig.model_validate(data)
        assert cfg._uses_deprecated_retention_days is True

    def test_deprecated_flag_unset_when_new_schema_used(self) -> None:
        data = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg._uses_deprecated_retention_days is False


# ---------------------------------------------------------------------------
# load_backup_config — deprecation warning
# ---------------------------------------------------------------------------


class TestLoadBackupConfig:
    def test_load_new_schema_no_warning(self, tmp_path: Path) -> None:
        config_data = {
            "backup": {
                "retention": {
                    "hourly": {"intervalMinutes": 60, "count": 24},
                    "daily": {"intervalMinutes": 1440, "count": 7},
                    "weekly": {"intervalMinutes": 10080, "count": 4},
                },
                "maxTotalBytes": 12884901888,
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_backup_config(config_file)
            assert len(w) == 0
        assert cfg.retention.hourly.count == 24

    def test_load_deprecated_emits_warning(self, tmp_path: Path) -> None:
        config_data = {
            "backup": {
                "retention_days": 7,
                "maxTotalBytes": 5000000000,
            }
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_backup_config(config_file)
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 1
            assert "retentionDays" in str(deprecation_warnings[0].message)
        assert cfg.retention.hourly.count == 168

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_backup_config(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# classifyTier
# ---------------------------------------------------------------------------


class TestClassifyTier:
    def _default_retention(self) -> RetentionConfig:
        return RetentionConfig(
            hourly=TierConfig(interval_minutes=60, count=24),
            daily=TierConfig(interval_minutes=1440, count=7),
            weekly=TierConfig(interval_minutes=10080, count=4),
        )

    def test_all_tiers_filled(self) -> None:
        """35 files: 24 hourly, 7 daily, 4 weekly."""
        files = _make_files(35)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]

        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:31] == [Tier.DAILY] * 7
        assert tiers[31:35] == [Tier.WEEKLY] * 4

    def test_extra_files_are_expired(self) -> None:
        """Files beyond total_slots (35) should be EXPIRED."""
        files = _make_files(40)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]

        assert tiers[35:] == [Tier.EXPIRED] * 5

    def test_fewer_than_hourly_slots(self) -> None:
        """Only 10 files, all should be hourly."""
        files = _make_files(10)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]

        assert all(t == Tier.HOURLY for t in tiers)
        assert len(tiers) == 10

    def test_fewer_than_daily_slots(self) -> None:
        """28 files: 24 hourly + 4 daily (no weekly)."""
        files = _make_files(28)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]

        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:28] == [Tier.DAILY] * 4

    def test_fewer_than_weekly_slots(self) -> None:
        """33 files: 24 hourly + 7 daily + 2 weekly."""
        files = _make_files(33)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]

        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:31] == [Tier.DAILY] * 7
        assert tiers[31:33] == [Tier.WEEKLY] * 2

    def test_empty_file_list(self) -> None:
        result = classify_tier([], self._default_retention())
        assert result == []

    def test_single_file(self) -> None:
        files = _make_files(1)
        result = classify_tier(files, self._default_retention())
        assert len(result) == 1
        assert result[0].tier == Tier.HOURLY

    def test_exact_boundary_24_hourly(self) -> None:
        """Exactly 24 files fills hourly, no daily/weekly."""
        files = _make_files(24)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]
        assert all(t == Tier.HOURLY for t in tiers)

    def test_exact_boundary_31_hourly_plus_daily(self) -> None:
        """31 files: 24 hourly + 7 daily."""
        files = _make_files(31)
        result = classify_tier(files, self._default_retention())
        tiers = [r.tier for r in result]
        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:31] == [Tier.DAILY] * 7

    def test_result_preserves_file_info(self) -> None:
        """Each ClassifiedFile carries the original BackupFile data."""
        files = _make_files(35)
        result = classify_tier(files, self._default_retention())

        assert result[0].file.path == files[0].path
        assert result[0].file.size_bytes == files[0].size_bytes
        assert result[0].file.modified_at == files[0].modified_at
        assert result[0].tier == Tier.HOURLY

    def test_only_daily_weekly_configured(self) -> None:
        """When hourly count is 0, files go to daily then weekly."""
        retention = RetentionConfig(
            hourly=TierConfig(interval_minutes=60, count=0),
            daily=TierConfig(interval_minutes=1440, count=7),
            weekly=TierConfig(interval_minutes=10080, count=4),
        )
        files = _make_files(10)
        result = classify_tier(files, retention)
        tiers = [r.tier for r in result]

        assert tiers[:7] == [Tier.DAILY] * 7
        assert tiers[7:10] == [Tier.WEEKLY] * 3

    def test_tier_promotion_with_more_files(self) -> None:
        """As more files accumulate, earlier files promote to higher tiers."""
        retention = RetentionConfig(
            hourly=TierConfig(interval_minutes=60, count=3),
            daily=TierConfig(interval_minutes=1440, count=2),
            weekly=TierConfig(interval_minutes=10080, count=2),
        )

        # 3 files: all hourly
        result_3 = classify_tier(_make_files(3), retention)
        assert all(r.tier == Tier.HOURLY for r in result_3)

        # 6 files: 3 hourly + 2 daily + 1 weekly
        result_6 = classify_tier(_make_files(6), retention)
        tiers_6 = [r.tier for r in result_6]
        assert tiers_6[:3] == [Tier.HOURLY] * 3
        assert tiers_6[3:5] == [Tier.DAILY] * 2
        assert tiers_6[5] == Tier.WEEKLY

        # 8 files: 3 hourly + 2 daily + 2 weekly + 1 expired
        result_8 = classify_tier(_make_files(8), retention)
        tiers_8 = [r.tier for r in result_8]
        assert tiers_8[7] == Tier.EXPIRED

    def test_files_sorted_by_age_newest_first(self) -> None:
        """Files are sorted internally regardless of input order."""
        files = list(reversed(_make_files(35)))  # oldest first
        result = classify_tier(files, self._default_retention())
        assert result[0].tier == Tier.HOURLY
        assert result[-1].tier == Tier.WEEKLY
