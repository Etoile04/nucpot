"""Tests for the pushOnRefusal config gate (NFM-3024-E AC2).

AC: With ``pushOnRefusal: false``, the refusal remains visible on
``/api/admin/backups/stats`` (via :class:`BackupMetrics`) and the SRE
push (``[SRE-WARNING]`` log line) is suppressed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
    CapacityGuardrails,
    DiskUsage,
    FloorBreachEvent,
)
from nfm_db.services.backup.metrics import (
    DEFAULT_PUSH_ON_REFUSAL,
    BackupMetrics,
    _should_push_on_refusal,
)

_GIB = 1024**3


def _make_disk(*, free: int, total_backup: int) -> DiskUsage:
    return DiskUsage(free_bytes=free, total_backup_bytes=total_backup)


# ---------------------------------------------------------------------------
# Predicate surface — single gate point
# ---------------------------------------------------------------------------


class TestShouldPushOnRefusalPredicate:
    """The predicate is the single gate point called by the observer."""

    def test_predicate_is_callable_with_no_args(self) -> None:
        # The observer (sibling task) calls _should_push_on_refusal() with
        # no arguments. The function must accept that calling convention.
        result = _should_push_on_refusal()
        assert isinstance(result, bool)

    def test_default_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NFM_BACKUP_PUSH_ON_REFUSAL", raising=False)
        assert _should_push_on_refusal() is True

    def test_returns_true_when_explicitly_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NFM_BACKUP_PUSH_ON_REFUSAL", "true")
        assert _should_push_on_refusal() is True

    def test_returns_false_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NFM_BACKUP_PUSH_ON_REFUSAL", "false")
        assert _should_push_on_refusal() is False

    def test_exported_default_constant_is_true(self) -> None:
        assert DEFAULT_PUSH_ON_REFUSAL is True


# ---------------------------------------------------------------------------
# BackupCapacityConfig: push_on_refusal field
# ---------------------------------------------------------------------------


class TestBackupCapacityConfigPushOnRefusal:
    """The new config field defaults to True and honors NFM_BACKUP_PUSH_ON_REFUSAL."""

    def test_default_push_on_refusal_is_true(self) -> None:
        assert BackupCapacityConfig().push_on_refusal is True

    def test_from_env_default_is_true(self) -> None:
        cfg = BackupCapacityConfig.from_env({})
        assert cfg.push_on_refusal is True

    def test_from_env_reads_false(self) -> None:
        cfg = BackupCapacityConfig.from_env({"NFM_BACKUP_PUSH_ON_REFUSAL": "false"})
        assert cfg.push_on_refusal is False

    def test_from_env_reads_true(self) -> None:
        cfg = BackupCapacityConfig.from_env({"NFM_BACKUP_PUSH_ON_REFUSAL": "true"})
        assert cfg.push_on_refusal is True

    def test_config_is_still_frozen(self) -> None:
        cfg = BackupCapacityConfig()
        with pytest.raises(AttributeError):
            cfg.push_on_refusal = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC: refusal recorded on stats AND [SRE-WARNING] suppressed when gate closed
# ---------------------------------------------------------------------------


class TestRefusalRecordedButSrePushSuppressed:
    """AC2: When push_on_refusal is False, refusal is still recorded on
    the stats endpoint but the [SRE-WARNING] log line is suppressed.
    """

    def test_floor_breach_refuses_and_records_when_push_off(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("NFM_BACKUP_PUSH_ON_REFUSAL", "false")
        cfg = BackupCapacityConfig.from_env()
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            result = gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        # Refusal is still returned and recorded for the stats endpoint.
        assert result is not None
        assert isinstance(result, FloorBreachEvent)
        assert metrics.refusal_count == 1
        assert metrics.last_refusal_at is not None

        # SRE push is suppressed.
        assert "[SRE-WARNING]" not in caplog.text

    def test_post_pruner_breach_suppresses_sre_push_when_gate_off(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("NFM_BACKUP_PUSH_ON_REFUSAL", "false")
        cfg = BackupCapacityConfig.from_env()
        metrics = BackupMetrics()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path, metrics=metrics)
        disk = _make_disk(free=5 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            result = gr.recheck_floor_after_pruner(disk=disk)

        assert result is not None
        assert metrics.refusal_count == 1
        assert "[SRE-WARNING]" not in caplog.text


class TestRefusalStillEmitsSrePushWhenGateOn:
    """The default (push_on_refusal=True) preserves existing behaviour."""

    def test_floor_breach_emits_sre_warning_by_default(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cfg = BackupCapacityConfig()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert "[SRE-WARNING]" in caplog.text

    def test_floor_breach_emits_sre_warning_when_explicitly_enabled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("NFM_BACKUP_PUSH_ON_REFUSAL", "true")
        cfg = BackupCapacityConfig.from_env()
        gr = CapacityGuardrails(config=cfg, backup_dir=tmp_path)
        disk = _make_disk(free=22 * _GIB, total_backup=0)

        with caplog.at_level(logging.WARNING, logger="nfm_db.services.backup.guardrails"):
            gr.check_floor_before_write(backup_size=5 * _GIB, disk=disk)

        assert "[SRE-WARNING]" in caplog.text
