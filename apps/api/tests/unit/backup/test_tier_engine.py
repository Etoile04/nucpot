"""Unit tests for the retention tier classification engine.

NFM-3024-T1 — config schema + retention tier engine (hourly/daily/weekly).

The tier engine consumes a sorted list of backup file records (each carrying a
``created_at`` timestamp) and assigns each one to a retention tier:
``hourly`` for the freshest N files, ``daily`` for the next M, ``weekly``
for the next K, and ``prunable`` for the rest (oldest first).

These tests cover the pure classification function first, then the Pydantic
schema that carries the retention configuration, and finally the loader that
warns when an operator still uses the legacy ``retentionDays`` alias.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nfm_db.backup.schema import BackupConfig, RetentionConfig, TierSpec

# The module under test does not exist yet — these imports intentionally
# fail under RED. They will become GREEN once ``tier_engine`` lands.
from nfm_db.backup.tier_engine import (
    Tier,
    classify_tier,
    sort_by_age_desc,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(created_at: datetime, path: str | None = None) -> dict:
    """Build a minimal backup file record dict for tests."""
    return {
        "path": path or f"backup-{int(created_at.timestamp())}.zip",
        "created_at": created_at,
        "size_bytes": 1024,
    }


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _default_config() -> RetentionConfig:
    """Match the schema example in NFM-3024-T1."""
    return RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=24),
        daily=TierSpec(interval_minutes=1440, count=7),
        weekly=TierSpec(interval_minutes=10080, count=4),
    )


# ---------------------------------------------------------------------------
# sort_by_age_desc
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sort_by_age_desc_newest_first() -> None:
    """Sorting places the newest record at index 0 and oldest at the end."""
    base = _now()
    files = [
        _file(base - timedelta(hours=5)),  # 5h old
        _file(base - timedelta(hours=1)),  # 1h old (newest)
        _file(base - timedelta(hours=10)),  # 10h old
    ]
    sorted_files = sort_by_age_desc(files)
    assert [f["path"] for f in sorted_files] == [
        files[1]["path"],  # 1h
        files[0]["path"],  # 5h
        files[2]["path"],  # 10h
    ]


@pytest.mark.unit
def test_sort_by_age_desc_empty() -> None:
    """Sorting an empty list returns an empty list."""
    assert sort_by_age_desc([]) == []


@pytest.mark.unit
def test_sort_by_age_desc_does_not_mutate_input() -> None:
    """The original list must remain in its original order."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(1, 4)]
    original_paths = [f["path"] for f in files]
    _ = sort_by_age_desc(files)
    assert [f["path"] for f in files] == original_paths


# ---------------------------------------------------------------------------
# classify_tier — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_classify_tier_empty_returns_empty() -> None:
    """An empty file list maps to an empty classification result."""
    assert classify_tier([], _default_config()) == []


@pytest.mark.unit
def test_classify_tier_fewer_files_than_hourly_all_hourly() -> None:
    """When we have fewer files than hourly.count, every file is hourly."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(5)]  # 5 files
    config = _default_config()  # hourly.count = 24
    result = classify_tier(files, config)
    assert [r.tier for r in result] == [Tier.HOURLY] * 5


@pytest.mark.unit
def test_classify_tier_exact_hourly_count_all_hourly() -> None:
    """When file count equals hourly.count, every file is hourly."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(24)]  # exactly 24
    config = _default_config()
    result = classify_tier(files, config)
    assert [r.tier for r in result] == [Tier.HOURLY] * 24


@pytest.mark.unit
def test_classify_tier_hourly_boundary_overflow() -> None:
    """The 25th file (index 24) becomes daily — boundary check."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(25)]
    config = _default_config()
    result = classify_tier(files, config)
    tiers = [r.tier for r in result]
    assert tiers[:24] == [Tier.HOURLY] * 24
    assert tiers[24] == Tier.DAILY


@pytest.mark.unit
def test_classify_tier_daily_boundary_overflow() -> None:
    """After hourly.count + daily.count files, the next one is weekly."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(31)]
    config = _default_config()
    result = classify_tier(files, config)
    tiers = [r.tier for r in result]
    assert tiers[:24] == [Tier.HOURLY] * 24
    assert tiers[24:31] == [Tier.DAILY] * 7


@pytest.mark.unit
def test_classify_tier_weekly_boundary_overflow() -> None:
    """After hourly + daily + weekly counts, anything else is prunable."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(35)]
    config = _default_config()
    result = classify_tier(files, config)
    tiers = [r.tier for r in result]
    assert tiers[:24] == [Tier.HOURLY] * 24
    assert tiers[24:31] == [Tier.DAILY] * 7
    assert tiers[31:35] == [Tier.WEEKLY] * 4


@pytest.mark.unit
def test_classify_tier_excess_files_marked_prunable() -> None:
    """Files beyond the weekly count are prunable (oldest first)."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(40)]  # 5 prunable
    config = _default_config()
    result = classify_tier(files, config)
    tiers = [r.tier for r in result]
    assert tiers[35:] == [Tier.PRUNABLE] * 5


@pytest.mark.unit
def test_classify_tier_result_preserves_record_identity() -> None:
    """Each classified result references the original record (by path)."""
    base = _now()
    # Use a tiny config (hourly=1, daily=1, weekly=1) so 4 files clearly
    # exceed the keep window and the oldest lands in PRUNABLE.
    config = RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=1),
        daily=TierSpec(interval_minutes=1440, count=1),
        weekly=TierSpec(interval_minutes=10080, count=1),
    )
    files = [
        _file(base - timedelta(hours=1), "fresh.zip"),
        _file(base - timedelta(hours=2), "mid.zip"),
        _file(base - timedelta(hours=3), "older.zip"),
        _file(base - timedelta(hours=200), "old.zip"),
    ]
    result = classify_tier(files, config)
    by_path = {r.record["path"]: r for r in result}
    assert by_path["fresh.zip"].tier == Tier.HOURLY
    assert by_path["mid.zip"].tier == Tier.DAILY
    assert by_path["older.zip"].tier == Tier.WEEKLY
    assert by_path["old.zip"].tier == Tier.PRUNABLE


@pytest.mark.unit
def test_classify_tier_unsorted_input_is_sorted_internally() -> None:
    """The engine sorts by age itself — callers need not pre-sort."""
    base = _now()
    # Tiny config (1/1/1) so 4 files clearly overflow: HOURLY/DAILY/WEEKLY/PRUNABLE.
    config = RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=1),
        daily=TierSpec(interval_minutes=1440, count=1),
        weekly=TierSpec(interval_minutes=10080, count=1),
    )
    files = [
        _file(base - timedelta(hours=200), "very_old.zip"),
        _file(base - timedelta(hours=1), "fresh.zip"),
        _file(base - timedelta(hours=50), "middle.zip"),
        _file(base - timedelta(hours=100), "older.zip"),
    ]
    result = classify_tier(files, config)
    assert [r.record["path"] for r in result] == [
        "fresh.zip",
        "middle.zip",
        "older.zip",
        "very_old.zip",
    ]
    assert result[0].tier == Tier.HOURLY
    assert result[1].tier == Tier.DAILY
    assert result[2].tier == Tier.WEEKLY
    assert result[3].tier == Tier.PRUNABLE


@pytest.mark.unit
def test_classify_tier_custom_config_smaller_counts() -> None:
    """A config with smaller tier counts re-buckets files accordingly."""
    base = _now()
    files = [_file(base - timedelta(hours=i)) for i in range(10)]
    config = RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=3),
        daily=TierSpec(interval_minutes=1440, count=2),
        weekly=TierSpec(interval_minutes=10080, count=1),
    )
    result = classify_tier(files, config)
    tiers = [r.tier for r in result]
    assert tiers == (
        [Tier.HOURLY] * 3
        + [Tier.DAILY] * 2
        + [Tier.WEEKLY] * 1
        + [Tier.PRUNABLE] * 4
    )


# ---------------------------------------------------------------------------
# Pydantic schema validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retention_config_validates_full_schema() -> None:
    """A fully populated retention config validates."""
    cfg = RetentionConfig(
        hourly=TierSpec(interval_minutes=60, count=24),
        daily=TierSpec(interval_minutes=1440, count=7),
        weekly=TierSpec(interval_minutes=10080, count=4),
    )
    assert cfg.hourly.count == 24
    assert cfg.daily.interval_minutes == 1440
    assert cfg.weekly.count == 4


@pytest.mark.unit
def test_retention_config_rejects_non_positive_count() -> None:
    """A tier count of zero or negative must fail validation."""
    with pytest.raises(ValueError):
        TierSpec(interval_minutes=60, count=0)
    with pytest.raises(ValueError):
        TierSpec(interval_minutes=60, count=-1)


@pytest.mark.unit
def test_retention_config_rejects_non_positive_interval() -> None:
    """A tier interval of zero or negative must fail validation."""
    with pytest.raises(ValueError):
        TierSpec(interval_minutes=0, count=24)
    with pytest.raises(ValueError):
        TierSpec(interval_minutes=-10, count=24)


@pytest.mark.unit
def test_backup_config_accepts_new_retention_schema() -> None:
    """BackupConfig must accept the new retention object."""
    cfg = BackupConfig(
        retention=RetentionConfig(
            hourly=TierSpec(interval_minutes=60, count=24),
            daily=TierSpec(interval_minutes=1440, count=7),
            weekly=TierSpec(interval_minutes=10080, count=4),
        ),
        max_total_bytes=12884901888,
        min_free_bytes=21474836480,
        refuse_on_floor_breach=True,
    )
    assert cfg.retention.hourly.count == 24
    assert cfg.refuse_on_floor_breach is True


@pytest.mark.unit
def test_backup_config_accepts_legacy_retention_days() -> None:
    """The deprecated retentionDays alias must still parse (one release cycle)."""
    cfg = BackupConfig(retention_days=7)
    assert cfg.retention_days == 7


@pytest.mark.unit
def test_backup_config_retention_takes_precedence_over_legacy_alias() -> None:
    """When both are present, the new ``retention`` object wins."""
    cfg = BackupConfig(
        retention=RetentionConfig(
            hourly=TierSpec(interval_minutes=60, count=24),
            daily=TierSpec(interval_minutes=1440, count=7),
            weekly=TierSpec(interval_minutes=10080, count=4),
        ),
        retention_days=7,  # legacy — ignored when retention is set
    )
    assert cfg.retention is not None
    assert cfg.retention.hourly.count == 24


@pytest.mark.unit
def test_backup_config_requires_one_of_retention_or_retention_days() -> None:
    """An empty backup config (neither retention nor retentionDays) is invalid."""
    with pytest.raises(ValueError):
        BackupConfig()


# ---------------------------------------------------------------------------
# Loader: startup warning for the legacy alias
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_warns_when_only_retention_days_present(
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """A config with only retentionDays emits a deprecation warning on load."""
    import json

    from nfm_db.backup.config_loader import load_backup_config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"backup": {"retentionDays": 7}}))

    with caplog.at_level("WARNING"):
        cfg = load_backup_config(config_path)

    assert cfg.retention_days == 7
    assert any("retentionDays" in rec.message for rec in caplog.records)


@pytest.mark.unit
def test_load_does_not_warn_when_retention_object_present(
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """A config with the new retention object does NOT emit the warning."""
    import json

    from nfm_db.backup.config_loader import load_backup_config

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "backup": {
                    "retention": {
                        "hourly": {"intervalMinutes": 60, "count": 24},
                        "daily": {"intervalMinutes": 1440, "count": 7},
                        "weekly": {"intervalMinutes": 10080, "count": 4},
                    }
                }
            }
        )
    )

    with caplog.at_level("WARNING"):
        cfg = load_backup_config(config_path)

    assert cfg.retention is not None
    assert all("retentionDays" not in rec.message for rec in caplog.records)
