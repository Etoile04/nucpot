"""Tests for BackupCapacityConfig (NFM-3016)."""

from __future__ import annotations

import pytest

from nfm_db.services.backup.config import (
    BackupCapacityConfig,
    _DEFAULT_MAX_TOTAL_BYTES,
    _DEFAULT_MIN_FREE_BYTES,
    _DEFAULT_REFUSE_ON_FLOOR,
)


class TestBackupCapacityDefaults:
    """AC: default values match spec — 12 GiB cap, 20 GiB floor, refuse=True."""

    def test_default_max_total_bytes_is_12_gib(self) -> None:
        assert BackupCapacityConfig().max_total_bytes == 12 * 1024**3

    def test_default_min_free_bytes_is_20_gib(self) -> None:
        assert BackupCapacityConfig().min_free_bytes == 20 * 1024**3

    def test_default_refuse_on_floor_is_true(self) -> None:
        assert BackupCapacityConfig().refuse_on_floor_breach is True

    def test_config_is_frozen(self) -> None:
        cfg = BackupCapacityConfig()
        with pytest.raises(AttributeError):
            cfg.max_total_bytes = 0  # type: ignore[misc]


class TestBackupCapacityFromEnv:
    """Config reads NFM_BACKUP_* env vars correctly."""

    def test_reads_custom_values(self) -> None:
        env = {
            "NFM_BACKUP_MAX_TOTAL_BYTES": "100",
            "NFM_BACKUP_MIN_FREE_BYTES": "200",
            "NFM_BACKUP_REFUSE_ON_FLOOR": "false",
        }
        cfg = BackupCapacityConfig.from_env(env)
        assert cfg.max_total_bytes == 100
        assert cfg.min_free_bytes == 200
        assert cfg.refuse_on_floor_breach is False

    def test_ignores_missing_keys(self) -> None:
        cfg = BackupCapacityConfig.from_env({})
        assert cfg.max_total_bytes == _DEFAULT_MAX_TOTAL_BYTES
        assert cfg.min_free_bytes == _DEFAULT_MIN_FREE_BYTES
        assert cfg.refuse_on_floor_breach == _DEFAULT_REFUSE_ON_FLOOR

    def test_invalid_int_falls_back_to_default(self) -> None:
        cfg = BackupCapacityConfig.from_env({"NFM_BACKUP_MAX_TOTAL_BYTES": "not-a-number"})
        assert cfg.max_total_bytes == _DEFAULT_MAX_TOTAL_BYTES

    def test_bool_true_variants(self) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
            cfg = BackupCapacityConfig.from_env({"NFM_BACKUP_REFUSE_ON_FLOOR": val})
            assert cfg.refuse_on_floor_breach is True

    def test_bool_false_variants(self) -> None:
        for val in ("0", "false", "False", "FALSE", "no", "NO", "off", "OFF", "anything"):
            cfg = BackupCapacityConfig.from_env({"NFM_BACKUP_REFUSE_ON_FLOOR": val})
            assert cfg.refuse_on_floor_breach is False
