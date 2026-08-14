"""Tests for the NFM-3024-T1 GFS tiering module.

Covers:
- AC1: Tier promotion — synthetic file list with timestamps spanning
  hourly → daily → weekly windows; verify each file lands in the
  expected tier bucket per the NFM-3024 spec.
- AC2: Pruner precedence — with N synthetic files ordered by timestamp,
  files marked PRUNABLE are always the oldest; prove by checking
  remaining-file ordering after each classification pass.
- AC3: Idempotent restart — running the classifier twice on identical
  synthetic state produces the same output with zero mutations.
- AC4: ≥80% line coverage on tier_engine.py + schema.py + config_loader.py.

Uses only filesystem mocks via ``tmp_path`` (no external mock framework).
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nfm_db.backup.config_loader import load_backup_config
from nfm_db.backup.schema import BackupConfig, RetentionConfig, TierSpec
from nfm_db.backup.tier_engine import (
    Tier,
    TierAssignment,
    _created_at_key,
    classify_tier,
    sort_by_age_desc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc
_NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)


def _ts(hours_ago: float) -> datetime:
    """Return a fixed UTC datetime *hours_ago* before the frozen now."""
    return _NOW - timedelta(hours=hours_ago)


def _record(hours_ago: float, path: str = "", size: int = 1024) -> dict[str, Any]:
    """Build a minimal backup-file record dict."""
    return {
        "path": path or f"/data/backup/db_{hours_ago:.0f}.sql.gz",
        "created_at": _ts(hours_ago),
        "size_bytes": size,
    }


def _default_config() -> RetentionConfig:
    """Return the NFM-3024-T1 default retention config (24/7/4)."""
    return RetentionConfig()


def _small_config(
    hourly: int = 3,
    daily: int = 2,
    weekly: int = 2,
) -> RetentionConfig:
    """Return a compact retention config for focused tests."""
    return RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=hourly),
        daily=TierSpec(interval_minutes=1440, count=daily),
        weekly=TierSpec(interval_minutes=10080, count=weekly),
    )


# ===========================================================================
# AC1 — Tier promotion
# ===========================================================================


class TestTierPromotion:
    """Verify files land in the correct tier bucket per NFM-3024 spec.

    The classifier is deterministic and newest-first: the first N files
    by recency are HOURLY, the next M are DAILY, the next K are WEEKLY,
    and anything beyond is PRUNABLE.
    """

    def test_all_tiers_filled_exactly(self) -> None:
        """35 files with default 24/7/4 config: each tier filled precisely."""
        records = [_record(h) for h in range(35)]
        result = classify_tier(records, _default_config())
        tiers = [r.tier for r in result]

        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:31] == [Tier.DAILY] * 7
        assert tiers[31:35] == [Tier.WEEKLY] * 4

    def test_tier_promotion_as_files_age(self) -> None:
        """As more files accumulate, earlier files promote to higher tiers.

        Start with 3 files (all hourly), grow to 6 (hourly+daily+weekly),
        then 8 (hourly+daily+weekly+prunable).
        """
        cfg = _small_config(hourly=3, daily=2, weekly=2)

        # 3 files: all hourly
        r3 = classify_tier([_record(h) for h in range(3)], cfg)
        assert all(a.tier == Tier.HOURLY for a in r3)

        # 6 files: 3 hourly + 2 daily + 1 weekly
        r6 = classify_tier([_record(h) for h in range(6)], cfg)
        tiers_6 = [a.tier for a in r6]
        assert tiers_6[:3] == [Tier.HOURLY] * 3
        assert tiers_6[3:5] == [Tier.DAILY] * 2
        assert tiers_6[5] == Tier.WEEKLY

        # 8 files: 3 hourly + 2 daily + 2 weekly + 1 prunable
        r8 = classify_tier([_record(h) for h in range(8)], cfg)
        tiers_8 = [a.tier for a in r8]
        assert tiers_8[:3] == [Tier.HOURLY] * 3
        assert tiers_8[3:5] == [Tier.DAILY] * 2
        assert tiers_8[5:7] == [Tier.WEEKLY] * 2
        assert tiers_8[7] == Tier.PRUNABLE

    def test_specific_file_promotes_correctly(self) -> None:
        """A single file's tier changes as newer files are added ahead of it.

        Create file A at t-0.  Alone, it is HOURLY.  Add 3 newer files,
        and A should promote to DAILY.  Add 8 more (11 total with hourly=3),
        and A should promote to WEEKLY.
        """
        cfg = _small_config(hourly=3, daily=5, weekly=4)

        file_a = _record(10, path="file_a.sql.gz")

        # Alone → HOURLY
        r1 = classify_tier([file_a], cfg)
        assert r1[0].tier == Tier.HOURLY

        # 3 newer files push A to DAILY
        newer_3 = [_record(h, path=f"new_{h}.sql.gz") for h in (0.5, 1.5, 2.5)]
        r4 = classify_tier(newer_3 + [file_a], cfg)
        a_assignment = next(a for a in r4 if a.record["path"] == "file_a.sql.gz")
        assert a_assignment.tier == Tier.DAILY

        # 8 more newer files (11 total, hourly=3) → A at index 11 → WEEKLY
        more = [
            _record(h, path=f"more_{h}.sql.gz")
            for h in (0.1, 0.3, 0.7, 1.0, 1.3, 2.0, 4.0, 5.0)
        ]
        r12 = classify_tier(more + newer_3 + [file_a], cfg)
        a_assignment = next(a for a in r12 if a.record["path"] == "file_a.sql.gz")
        assert a_assignment.tier == Tier.WEEKLY

    def test_files_with_varied_timestamps_spanning_tiers(self) -> None:
        """Files with realistic backup intervals land in correct tiers.

        Simulate: 24 hourly backups (every hour), 7 daily (every 24h),
        4 weekly (every 168h), plus 2 expired.  Total = 37.
        """
        records: list[dict[str, Any]] = []

        # 24 hourly: created 0.5, 1.5, …, 23.5 hours ago
        for i in range(24):
            records.append(_record(0.5 + i))

        # 7 daily: created 25, 49, 73, 97, 121, 145, 169 hours ago
        for i in range(7):
            records.append(_record(25 + 24 * i))

        # 4 weekly: created 193, 361, 529, 697 hours ago
        for i in range(4):
            records.append(_record(193 + 168 * i))

        # 2 prunable: created 900 and 1100 hours ago
        records.append(_record(900))
        records.append(_record(1100))

        result = classify_tier(records, _default_config())
        tiers = [r.tier for r in result]

        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:31] == [Tier.DAILY] * 7
        assert tiers[31:35] == [Tier.WEEKLY] * 4
        assert tiers[35:] == [Tier.PRUNABLE] * 2

    def test_fewer_files_than_hourly_slots(self) -> None:
        """Only 10 files with 24 hourly slots: all hourly."""
        records = [_record(h) for h in range(10)]
        result = classify_tier(records, _default_config())
        assert all(r.tier == Tier.HOURLY for r in result)
        assert len(result) == 10

    def test_fewer_files_than_daily_slots(self) -> None:
        """28 files: 24 hourly + 4 daily, no weekly."""
        records = [_record(h) for h in range(28)]
        result = classify_tier(records, _default_config())
        tiers = [r.tier for r in result]
        assert tiers[:24] == [Tier.HOURLY] * 24
        assert tiers[24:28] == [Tier.DAILY] * 4

    def test_zero_hourly_count_rejected(self) -> None:
        """TierSpec rejects count=0 (gt=0 constraint)."""
        with pytest.raises(ValidationError):
            TierSpec(interval_minutes=60, count=0)

    def test_all_tiers_zero_rejected(self) -> None:
        """All tier counts at 0 are rejected by TierSpec validation."""
        with pytest.raises(ValidationError):
            RetentionConfig(
                hourly=TierSpec(interval_minutes=60, count=0),
                daily=TierSpec(interval_minutes=1440, count=0),
                weekly=TierSpec(interval_minutes=10080, count=0),
            )


# ===========================================================================
# AC2 — Pruner precedence
# ===========================================================================


class TestPrunerPrecedence:
    """Verify that files marked PRUNABLE are always the oldest files,
    regardless of size or tier.  The pruner deletes oldest-first.
    """

    def test_prunable_files_are_oldest(self) -> None:
        """PRUNABLE files must have the oldest created_at timestamps."""
        cfg = _small_config(hourly=2, daily=2, weekly=1)
        # 6 files: 2 hourly + 2 daily + 1 weekly + 1 prunable
        records = [_record(h) for h in (0.5, 2, 5, 30, 200, 500)]

        result = classify_tier(records, cfg)
        prunable = [r for r in result if r.tier == Tier.PRUNABLE]
        non_prunable = [r for r in result if r.tier != Tier.PRUNABLE]

        assert len(prunable) == 1
        # The prunable file must be older than every non-prunable file
        for np in non_prunable:
            assert prunable[0].record["created_at"] < np.record["created_at"]

    def test_prunable_ordering_newest_first(self) -> None:
        """Multiple PRUNABLE files are ordered newest-first (matching
        the overall result order).  The pruner deletes from the tail."""
        cfg = _default_config()
        # 40 files: 35 in tiers + 5 prunable
        records = [_record(h) for h in range(40)]
        result = classify_tier(records, cfg)
        prunable = [r for r in result if r.tier == Tier.PRUNABLE]

        assert len(prunable) == 5
        # Result is newest-first overall, so prunable subset is too
        for i in range(len(prunable) - 1):
            assert (
                prunable[i].record["created_at"]
                >= prunable[i + 1].record["created_at"]
            )

    def test_size_does_not_affect_pruner_order(self) -> None:
        """Size is irrelevant — the oldest file is pruned first even if small."""
        cfg = _small_config(hourly=2, daily=1, weekly=1)
        # 5 files: 2 hourly + 1 daily + 1 weekly + 1 prunable
        # The oldest file (500h ago) is tiny (1 byte), newer files are huge
        records = [
            _record(0.5, size=10_000_000),
            _record(2, size=9_000_000),
            _record(30, size=8_000_000),
            _record(200, size=7_000_000),
            _record(500, size=1),  # tiny but oldest → prunable
        ]

        result = classify_tier(records, cfg)
        prunable = [r for r in result if r.tier == Tier.PRUNABLE]

        assert len(prunable) == 1
        assert prunable[0].record["size_bytes"] == 1
        assert prunable[0].record["created_at"] == _ts(500)

    def test_remaining_files_preserve_age_ordering(self) -> None:
        """After removing prunable files, remaining files are newest-first."""
        cfg = _small_config(hourly=3, daily=2, weekly=1)
        # 8 files: 3 hourly + 2 daily + 1 weekly + 2 prunable
        records = [_record(h) for h in range(8)]
        result = classify_tier(records, cfg)
        retained = [r for r in result if r.tier != Tier.PRUNABLE]

        # Retained files must be in newest-first order
        for i in range(len(retained) - 1):
            assert (
                retained[i].record["created_at"]
                >= retained[i + 1].record["created_at"]
            )

    def test_pruner_deletes_oldest_across_multiple_passes(self) -> None:
        """Simulate successive pruner passes deleting oldest each time.

        Pass 1: classify 8 files (6 in tiers, 2 prunable). Delete 2 oldest.
        Pass 2: reclassify remaining 6 (all in tiers, 0 prunable).
        """
        cfg = _small_config(hourly=3, daily=2, weekly=1)
        records = [_record(h) for h in range(8)]

        # Pass 1
        result_1 = classify_tier(records, cfg)
        prunable_1 = [r for r in result_1 if r.tier == Tier.PRUNABLE]
        assert len(prunable_1) == 2
        # Prunable are the two oldest
        surviving_paths_1 = {
            r.record["path"] for r in result_1 if r.tier != Tier.PRUNABLE
        }

        # "Delete" the prunable files
        surviving_records = [r for r in records if r["path"] in surviving_paths_1]
        assert len(surviving_records) == 6

        # Pass 2: reclassify — no more prunable
        result_2 = classify_tier(surviving_records, cfg)
        prunable_2 = [r for r in result_2 if r.tier == Tier.PRUNABLE]
        assert len(prunable_2) == 0

    def test_single_prunable_boundary(self) -> None:
        """Exactly one file beyond the weekly budget is PRUNABLE."""
        cfg = _small_config(hourly=3, daily=2, weekly=2)
        # 8 files: 3+2+2 = 7 in tiers, 1 prunable
        records = [_record(h) for h in range(8)]
        result = classify_tier(records, cfg)
        tiers = [r.tier for r in result]

        assert tiers[:3] == [Tier.HOURLY] * 3
        assert tiers[3:5] == [Tier.DAILY] * 2
        assert tiers[5:7] == [Tier.WEEKLY] * 2
        assert tiers[7] == Tier.PRUNABLE


# ===========================================================================
# AC3 — Idempotent restart
# ===========================================================================


class TestIdempotentRestart:
    """Running the classifier twice on identical state must produce
    identical results with zero mutations to the input.
    """

    def test_double_classify_same_result(self) -> None:
        """Two classify_tier calls on the same input return identical output."""
        cfg = _default_config()
        records = [_record(h) for h in range(40)]

        result_1 = classify_tier(records, cfg)
        result_2 = classify_tier(records, cfg)

        assert len(result_1) == len(result_2)
        for a1, a2 in zip(result_1, result_2):
            assert a1.tier == a2.tier
            assert a1.record is a2.record  # same object reference

    def test_input_not_mutated(self) -> None:
        """The input list is not modified by classify_tier."""
        records = [_record(h) for h in range(10)]
        original_records = copy.deepcopy(records)

        classify_tier(records, _default_config())

        assert records == original_records
        assert len(records) == len(original_records)

    def test_sort_by_age_does_not_mutate(self) -> None:
        """sort_by_age_desc returns a new list; input is untouched."""
        records = [_record(h) for h in range(10)]
        original = list(records)  # shallow copy for comparison

        sorted_records = sort_by_age_desc(records)

        assert sorted_records is not records
        assert records == original  # input unchanged

    def test_scheduler_idempotent_full_cycle(self) -> None:
        """Simulate a full scheduler cycle: classify → no side effects.

        The scheduler would:
        1. Read file list (simulated as records)
        2. Classify into tiers
        3. Delete PRUNABLE files (simulated by filtering)
        4. Promote surviving files (re-classify)

        Running this cycle twice on the same initial state must produce
        the same surviving set and the same tier assignments.
        """
        cfg = _small_config(hourly=3, daily=2, weekly=2)
        initial_records = [_record(h) for h in range(10)]

        # Cycle 1
        result_1 = classify_tier(initial_records, cfg)
        surviving_1 = [r.record for r in result_1 if r.tier != Tier.PRUNABLE]
        # "Promote" = re-classify surviving files
        promoted_1 = classify_tier(surviving_1, cfg)

        # Cycle 2 on the same initial state
        result_2 = classify_tier(initial_records, cfg)
        surviving_2 = [r.record for r in result_2 if r.tier != Tier.PRUNABLE]
        promoted_2 = classify_tier(surviving_2, cfg)

        assert len(surviving_1) == len(surviving_2)
        for p1, p2 in zip(promoted_1, promoted_2):
            assert p1.tier == p2.tier
        # No PRUNABLE in the promoted set (by construction)
        assert all(p.tier != Tier.PRUNABLE for p in promoted_1)
        assert all(p.tier != Tier.PRUNABLE for p in promoted_2)

    def test_empty_state_idempotent(self) -> None:
        """Classifying an empty file list twice returns empty both times."""
        cfg = _default_config()
        assert classify_tier([], cfg) == classify_tier([], cfg) == []


# ===========================================================================
# Edge cases and schema tests (support ≥80% coverage)
# ===========================================================================


class TestTierEngineEdgeCases:
    """Additional edge cases for comprehensive coverage."""

    def test_empty_input(self) -> None:
        assert classify_tier([], _default_config()) == []

    def test_single_file(self) -> None:
        records = [_record(1)]
        result = classify_tier(records, _default_config())
        assert len(result) == 1
        assert result[0].tier == Tier.HOURLY

    def test_input_order_irrelevant(self) -> None:
        """Classification sorts internally — input order does not matter."""
        cfg = _small_config(hourly=2, daily=1, weekly=1)
        ascending = [_record(h) for h in range(5)]
        descending = list(reversed(ascending))
        shuffled = [ascending[2], ascending[0], ascending[4], ascending[1], ascending[3]]

        r_asc = classify_tier(ascending, cfg)
        r_desc = classify_tier(descending, cfg)
        r_shuf = classify_tier(shuffled, cfg)

        # All three produce the same tier sequence
        tiers_asc = [r.tier for r in r_asc]
        tiers_desc = [r.tier for r in r_desc]
        tiers_shuf = [r.tier for r in r_shuf]
        assert tiers_asc == tiers_desc == tiers_shuf

    def test_result_length_matches_input(self) -> None:
        """Output list length always equals input list length."""
        for n in (0, 1, 5, 35, 40, 100):
            records = [_record(h) for h in range(n)]
            result = classify_tier(records, _default_config())
            assert len(result) == n

    def test_result_preserves_record_reference(self) -> None:
        """Each TierAssignment carries the original record dict (same identity)."""
        records = [_record(h) for h in range(5)]
        result = classify_tier(records, _default_config())
        for assignment in result:
            assert assignment.record in records

    def test_created_at_key_valid_datetime(self) -> None:
        """_created_at_key returns the datetime from a valid record."""
        record = {"created_at": _ts(5)}
        assert _created_at_key(record) == _ts(5)

    def test_created_at_key_invalid_type_raises(self) -> None:
        """_created_at_key raises TypeError for non-datetime created_at."""
        with pytest.raises(TypeError, match="missing 'created_at' datetime"):
            _created_at_key({"created_at": "not-a-datetime"})

    def test_created_at_key_missing_field_raises(self) -> None:
        """_created_at_key raises TypeError when created_at is absent."""
        with pytest.raises(TypeError, match="missing 'created_at' datetime"):
            _created_at_key({"path": "/tmp"})

    def test_tier_enum_values(self) -> None:
        """Tier enum has the expected string values."""
        assert Tier.HOURLY == "hourly"
        assert Tier.DAILY == "daily"
        assert Tier.WEEKLY == "weekly"
        assert Tier.PRUNABLE == "prunable"

    def test_tier_assignment_is_immutable(self) -> None:
        """TierAssignment is a frozen dataclass — attribute reassignment raises."""
        record = _record(1)
        ta = TierAssignment(record=record, tier=Tier.HOURLY)
        with pytest.raises(AttributeError):
            ta.tier = Tier.DAILY  # type: ignore[misc]

    def test_exact_total_slots_boundary(self) -> None:
        """Exactly total_slots files: no PRUNABLE entries."""
        records = [_record(h) for h in range(35)]
        result = classify_tier(records, _default_config())
        assert all(r.tier != Tier.PRUNABLE for r in result)

    def test_one_over_total_slots_boundary(self) -> None:
        """total_slots + 1 files: exactly one PRUNABLE."""
        records = [_record(h) for h in range(36)]
        result = classify_tier(records, _default_config())
        prunable = [r for r in result if r.tier == Tier.PRUNABLE]
        assert len(prunable) == 1


# ===========================================================================
# Schema tests (config_loader.py + schema.py coverage)
# ===========================================================================


class TestSchema:
    """Tests for BackupConfig, RetentionConfig, TierSpec validation."""

    def test_default_retention_counts(self) -> None:
        cfg = RetentionConfig()
        assert cfg.hourly.count == 24
        assert cfg.daily.count == 7
        assert cfg.weekly.count == 4

    def test_tier_spec_rejects_zero_interval(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(interval_minutes=0, count=5)

    def test_tier_spec_rejects_zero_count(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(interval_minutes=60, count=0)

    def test_tier_spec_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TierSpec(interval_minutes=60, count=5, unknown_field=99)

    def test_tier_spec_frozen(self) -> None:
        ts = TierSpec(interval_minutes=60, count=24)
        with pytest.raises(ValidationError):
            ts.count = 10  # type: ignore[misc]

    def test_backup_config_requires_retention_or_legacy(self) -> None:
        with pytest.raises(ValidationError, match="requires either"):
            BackupConfig.model_validate({})

    def test_backup_config_accepts_new_retention(self) -> None:
        data = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg.retention.hourly.count == 24

    def test_backup_config_accepts_legacy_retention_days(self) -> None:
        data = {"retentionDays": 7}
        cfg = BackupConfig.model_validate(data)
        assert cfg.retention_days == 7

    def test_backup_config_new_retention_wins_over_legacy(self) -> None:
        """When both are present, the new retention object takes precedence."""
        data = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 10},
                "daily": {"intervalMinutes": 1440, "count": 3},
                "weekly": {"intervalMinutes": 10080, "count": 2},
            },
            "retentionDays": 30,
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg.retention.hourly.count == 10
        assert cfg.retention_days == 30

    def test_backup_config_max_total_bytes_alias(self) -> None:
        data = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
            "maxTotalBytes": 999999999,
        }
        cfg = BackupConfig.model_validate(data)
        assert cfg.max_total_bytes == 999999999

    def test_retention_config_frozen(self) -> None:
        cfg = RetentionConfig()
        with pytest.raises(ValidationError):
            cfg.hourly = TierSpec(interval_minutes=30, count=48)  # type: ignore[misc]


class TestConfigLoader:
    """Tests for load_backup_config (reads JSON from disk via tmp_path)."""

    def test_load_valid_new_schema(self, tmp_path: Path) -> None:
        config = {
            "backup": {
                "retention": {
                    "hourly": {"intervalMinutes": 60, "count": 24},
                    "daily": {"intervalMinutes": 1440, "count": 7},
                    "weekly": {"intervalMinutes": 10080, "count": 4},
                },
                "maxTotalBytes": 12884901888,
            }
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        result = load_backup_config(cfg_file)
        assert result.retention.hourly.count == 24
        assert result.max_total_bytes == 12884901888

    def test_load_valid_legacy_schema(self, tmp_path: Path) -> None:
        config = {"backup": {"retentionDays": 7}}
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        result = load_backup_config(cfg_file)
        assert result.retention_days == 7

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_backup_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "bad.json"
        cfg_file.write_text("{not valid json}")
        with pytest.raises(json.JSONDecodeError):
            load_backup_config(cfg_file)

    def test_load_without_backup_key_raises(self, tmp_path: Path) -> None:
        """JSON without a 'backup' key results in empty dict → validation error."""
        config = {
            "retention": {
                "hourly": {"intervalMinutes": 60, "count": 24},
                "daily": {"intervalMinutes": 1440, "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4},
            },
        }
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        with pytest.raises(ValidationError, match="requires either"):
            load_backup_config(cfg_file)

    def test_load_legacy_emits_log_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Legacy retentionDays triggers a warning via the config_loader logger."""
        config = {"backup": {"retentionDays": 7}}
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps(config))

        load_backup_config(cfg_file)
        assert "deprecated" in caplog.text.lower()
        assert "retentionDays" in caplog.text
