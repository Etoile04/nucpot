"""Tests for admin backup API endpoints — NFM-3044.

Validates:

- GET /api/admin/backups returns snapshots with the ``tier`` field.
- GET /api/admin/backups/stats returns the documented schema
  (camelCase fields + ``tiers`` per-tier breakdown).
- Existing restore path is unaffected (no restore endpoint changes).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupSnapshotResponse,
    BackupStatsResponse,
    BackupTier,
    TierBreakdown,
    TierStats,
)
from nfm_db.services.backup_service import (
    get_backup_stats,
    list_snapshots,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    """Create a temporary backup directory."""
    backups = tmp_path / "backups"
    backups.mkdir()
    return backups


@pytest.fixture
def populated_backup_dir(backup_dir: Path) -> Path:
    """Populate backup directory with tiered snapshot files."""
    (backup_dir / "nucpot-20260813T050000.hourly.sql.gz").write_bytes(b"h" * 100)
    (backup_dir / "nucpot-20260813T060000.hourly.sql.gz").write_bytes(b"h" * 200)
    (backup_dir / "nucpot-20260813T070000.hourly.sql.gz").write_bytes(b"h" * 300)
    (backup_dir / "nucpot-20260812T020000.daily.sql.gz").write_bytes(b"d" * 400)
    (backup_dir / "nucpot-20260811T020000.daily.sql.gz").write_bytes(b"d" * 500)
    (backup_dir / "nucpot-20260810T020000.weekly.sql.gz").write_bytes(b"w" * 600)
    (backup_dir / "nucpot-20260809T020000.sql.gz").write_bytes(b"l" * 50)
    (backup_dir / "README.txt").write_text("not a backup")
    return backup_dir


@pytest.fixture
def refusal_file(tmp_path: Path) -> Path:
    """Create a refusal tracking JSON sidecar."""
    path = tmp_path / "refusals.json"
    path.write_text(
        json.dumps({
            "count": 3,
            "last_refusal_at": "2026-08-13T04:30:00+00:00",
        }),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# AC #1 — Each backup snapshot includes tier field
# ---------------------------------------------------------------------------


class TestListSnapshotsTier:
    """Tests for the tier field on backup snapshots (AC #1)."""

    def test_empty_directory_returns_empty(self, backup_dir: Path) -> None:
        """Non-existent directory returns empty list with total=0."""
        result = list_snapshots(backup_dir / "nonexistent")
        assert result.total == 0
        assert result.snapshots == []

    def test_filters_by_extension(self, backup_dir: Path) -> None:
        """Only files matching the extension are included."""
        (backup_dir / "dump.sql.gz").write_bytes(b"data")
        (backup_dir / "dump.tar").write_bytes(b"tar")

        result = list_snapshots(backup_dir, extension=".sql.gz")
        assert result.total == 1
        assert result.snapshots[0].filename == "dump.sql.gz"

    def test_tier_hourly(self, populated_backup_dir: Path) -> None:
        """*.hourly.* files map to BackupTier.HOURLY."""
        result = list_snapshots(populated_backup_dir)
        hourly = [s for s in result.snapshots if ".hourly." in s.filename]
        assert len(hourly) == 3
        assert all(s.tier == BackupTier.HOURLY for s in hourly)

    def test_tier_daily(self, populated_backup_dir: Path) -> None:
        """*.daily.* files map to BackupTier.DAILY."""
        result = list_snapshots(populated_backup_dir)
        daily = [s for s in result.snapshots if ".daily." in s.filename]
        assert len(daily) == 2
        assert all(s.tier == BackupTier.DAILY for s in daily)

    def test_tier_weekly(self, populated_backup_dir: Path) -> None:
        """*.weekly.* files map to BackupTier.WEEKLY."""
        result = list_snapshots(populated_backup_dir)
        weekly = [s for s in result.snapshots if ".weekly." in s.filename]
        assert len(weekly) == 1
        assert weekly[0].tier == BackupTier.WEEKLY

    def test_size_bytes_populated(self, populated_backup_dir: Path) -> None:
        """size_bytes matches actual file size."""
        result = list_snapshots(populated_backup_dir)
        for snapshot in result.snapshots:
            actual = (populated_backup_dir / snapshot.filename).stat().st_size
            assert snapshot.size_bytes == actual

    def test_created_at_from_mtime(self, populated_backup_dir: Path) -> None:
        """created_at matches file modification time."""
        result = list_snapshots(populated_backup_dir)
        for snapshot in result.snapshots:
            actual = (populated_backup_dir / snapshot.filename).stat().st_mtime
            assert snapshot.created_at == datetime.fromtimestamp(actual)

    def test_sorted_newest_first(self, populated_backup_dir: Path) -> None:
        """Snapshots are sorted newest first."""
        result = list_snapshots(populated_backup_dir)
        if len(result.snapshots) >= 2:
            assert result.snapshots[0].created_at >= result.snapshots[1].created_at


# ---------------------------------------------------------------------------
# AC #2 — Stats endpoint returns accurate metrics + per-tier breakdown
# ---------------------------------------------------------------------------


class TestBackupStatsShape:
    """Tests for the documented stats response schema (AC #2)."""

    def test_top_level_fields_are_present(self, backup_dir: Path) -> None:
        """Stats response contains the documented top-level fields."""
        result = get_backup_stats(backup_dir)
        assert isinstance(result, BackupStatsResponse)
        dumped = result.model_dump()
        assert "totalBytes" in dumped
        assert "freeBytes" in dumped
        assert "refusalCount" in dumped
        assert "lastRefusalAt" in dumped
        assert "tiers" in dumped

    def test_no_snake_case_leak(self, backup_dir: Path) -> None:
        """Stats response does NOT leak snake_case names on the wire."""
        result = get_backup_stats(backup_dir)
        dumped = result.model_dump()
        for forbidden in ("total_bytes", "free_bytes", "refusal_count", "last_refusal_at"):
            assert forbidden not in dumped, (
                f"snake_case field {forbidden!r} leaked into stats response"
            )

    def test_disk_metrics_are_positive(self, backup_dir: Path) -> None:
        """totalBytes > 0, freeBytes >= 0, freeBytes <= totalBytes."""
        result = get_backup_stats(backup_dir)
        assert result.total_bytes > 0
        assert result.free_bytes >= 0
        assert result.free_bytes <= result.total_bytes

    def test_no_refusal_file_zero(self, backup_dir: Path) -> None:
        """Without refusal file: refusalCount=0, lastRefusalAt=None."""
        result = get_backup_stats(backup_dir)
        assert result.refusal_count == 0
        assert result.last_refusal_at is None

    def test_refusal_file_loaded(
        self, backup_dir: Path, refusal_file: Path,
    ) -> None:
        """Refusal sidecar populates refusalCount and lastRefusalAt."""
        result = get_backup_stats(backup_dir, refusal_file=refusal_file)
        assert result.refusal_count == 3
        assert result.last_refusal_at == datetime(
            2026, 8, 13, 4, 30, 0, tzinfo=UTC,
        )

    def test_corrupt_refusal_file_zero(self, backup_dir: Path, tmp_path: Path) -> None:
        """Corrupt refusal file is treated as zero refusals."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not-json", encoding="utf-8")

        result = get_backup_stats(backup_dir, refusal_file=bad_file)
        assert result.refusal_count == 0
        assert result.last_refusal_at is None


# ---------------------------------------------------------------------------
# AC #2 — Per-tier breakdown in stats
# ---------------------------------------------------------------------------


class TestBackupStatsTiersBreakdown:
    """The stats endpoint must include a ``tiers`` per-tier breakdown."""

    def test_tiers_breakdown_present(self, backup_dir: Path) -> None:
        """The ``tiers`` key exposes three tier entries."""
        result = get_backup_stats(backup_dir)
        assert isinstance(result.tiers, TierBreakdown)
        dumped = result.tiers.model_dump()
        assert set(dumped.keys()) == {"hourly", "daily", "weekly"}

    def test_tiers_count_matches_snapshots(
        self, populated_backup_dir: Path,
    ) -> None:
        """Per-tier counts equal the number of snapshots in that tier.

        populated_backup_dir has 3 hourly, 2 daily, 1 weekly, plus 1
        legacy file bucketed to the default tier (DAILY) → 3 daily total.
        """
        result = get_backup_stats(populated_backup_dir)
        assert result.tiers.hourly.count == 3
        assert result.tiers.daily.count == 3
        assert result.tiers.weekly.count == 1

    def test_tiers_bytes_matches_snapshots(
        self, populated_backup_dir: Path,
    ) -> None:
        """Per-tier bytes equal the sum of file sizes in that tier.

        The legacy file (50 bytes) is bucketed to DAILY alongside the two
        explicit daily files (400 + 500).
        """
        result = get_backup_stats(populated_backup_dir)
        assert result.tiers.hourly.bytes == 100 + 200 + 300
        assert result.tiers.daily.bytes == 400 + 500 + 50
        assert result.tiers.weekly.bytes == 600

    def test_empty_directory_zero_tiers(self, backup_dir: Path) -> None:
        """Empty directory: all tier counts and bytes are zero."""
        result = get_backup_stats(backup_dir)
        assert result.tiers.hourly.count == 0
        assert result.tiers.hourly.bytes == 0
        assert result.tiers.daily.count == 0
        assert result.tiers.daily.bytes == 0
        assert result.tiers.weekly.count == 0
        assert result.tiers.weekly.bytes == 0

    def test_legacy_files_bucketed_to_default(
        self, populated_backup_dir: Path,
    ) -> None:
        """Files without a tier suffix are bucketed to the default tier."""
        result = get_backup_stats(populated_backup_dir)
        # Default tier is DAILY, legacy file is 50 bytes → daily gets +50.
        assert result.tiers.daily.bytes == 400 + 500 + 50

    def test_tier_stats_schema(self) -> None:
        """TierStats schema has count and bytes fields with ge=0."""
        ts = TierStats(count=10, bytes=1024)
        dumped = ts.model_dump()
        assert dumped == {"count": 10, "bytes": 1024}

        with pytest.raises(Exception):
            TierStats(count=-1, bytes=0)
        with pytest.raises(Exception):
            TierStats(count=0, bytes=-1)


# ---------------------------------------------------------------------------
# AC #3 — Existing restore path unchanged
# ---------------------------------------------------------------------------


class TestRestorePathUnaffected:
    """Sanity tests: the restore endpoints/files are untouched."""

    def test_no_restore_endpoint_added(self) -> None:
        """The backups router must NOT add a restore endpoint.

        Restore is handled by a separate route; this task is purely
        additive for tier visibility + stats.
        """
        from nfm_db.api.admin.backups import router

        restore_paths = [
            r.path for r in router.routes
            if hasattr(r, "path") and "restore" in r.path.lower()
        ]
        assert restore_paths == [], (
            f"backups router must not own restore paths; found {restore_paths}"
        )

    def test_legacy_dump_extension_default_preserved(
        self, populated_backup_dir: Path,
    ) -> None:
        """list_snapshots still defaults to extension='.sql.gz'."""
        result = list_snapshots(populated_backup_dir)
        # populated_backup_dir contains 7 .sql.gz files (incl. the legacy one).
        assert result.total == 7


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestBackupSchemas:
    """Pydantic schema validation for backup response models."""

    def test_backup_tier_enum_values(self) -> None:
        """BackupTier has exactly three values: hourly, daily, weekly."""
        assert {t.value for t in BackupTier} == {"hourly", "daily", "weekly"}

    def test_snapshot_response_rejects_negative_size(self) -> None:
        """Negative size_bytes is rejected."""
        with pytest.raises(Exception):
            BackupSnapshotResponse(
                filename="test.sql.gz",
                size_bytes=-1,
                created_at=datetime(2026, 8, 13, 5, 0, 0),
                tier=BackupTier.HOURLY,
            )

    def test_snapshot_response_serializes_tier_value(self) -> None:
        """Tier serializes to its string value, not its enum name."""
        snapshot = BackupSnapshotResponse(
            filename="test.sql.gz",
            size_bytes=1024,
            created_at=datetime(2026, 8, 13, 5, 0, 0),
            tier=BackupTier.HOURLY,
        )
        dumped = snapshot.model_dump()
        assert dumped["tier"] == "hourly"

    def test_stats_response_full_shape(self) -> None:
        """Stats response accepts the documented wire shape."""
        stats = BackupStatsResponse(
            total_bytes=10737418240,
            free_bytes=21474836480,
            refusal_count=0,
            last_refusal_at=None,
            tiers=TierBreakdown(
                hourly=TierStats(count=24, bytes=7549747200),
                daily=TierStats(count=7, bytes=2202009600),
                weekly=TierStats(count=4, bytes=1258291200),
            ),
        )
        dumped = stats.model_dump()
        assert dumped["totalBytes"] == 10737418240
        assert dumped["freeBytes"] == 21474836480
        assert dumped["refusalCount"] == 0
        assert dumped["lastRefusalAt"] is None
        assert dumped["tiers"]["hourly"] == {"count": 24, "bytes": 7549747200}
        assert dumped["tiers"]["daily"] == {"count": 7, "bytes": 2202009600}
        assert dumped["tiers"]["weekly"] == {"count": 4, "bytes": 1258291200}

    def test_list_response_serializes_snapshots(self) -> None:
        """BackupListResponse wraps snapshots + total correctly."""
        snap = BackupSnapshotResponse(
            filename="a.sql.gz",
            size_bytes=10,
            created_at=datetime(2026, 8, 13),
            tier=BackupTier.DAILY,
        )
        result = BackupListResponse(snapshots=[snap], total=1)
        dumped = result.model_dump()
        assert dumped["total"] == 1
        assert dumped["snapshots"][0]["filename"] == "a.sql.gz"
        assert dumped["snapshots"][0]["tier"] == "daily"
