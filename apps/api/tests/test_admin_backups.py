"""Tests for admin backup API endpoints — NFM-3017 / NFM-3070.

Unit tests for the backup service (filesystem scanning, tier derivation,
disk stats, refusal tracking), schema validation, and path whitelisting
(defense-in-depth for admin backup endpoints).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from nfm_db.api.admin.backups import (
    _validate_backup_dir,
    _validate_refusal_file,
)
from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupSnapshotResponse,
    BackupStatsResponse,
    BackupTier,
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
    (backup_dir / "nucpot-20260813T050000.hourly.sql.gz").write_bytes(b"hourly")
    (backup_dir / "nucpot-20260812T020000.daily.sql.gz").write_bytes(b"daily")
    (backup_dir / "nucpot-20260810T020000.weekly.sql.gz").write_bytes(b"weekly")
    (backup_dir / "nucpot-20260809T020000.sql.gz").write_bytes(b"legacy")
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
# Unit tests — backup_service.list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    """Tests for backup_service.list_snapshots."""

    def test_empty_directory(self, backup_dir: Path) -> None:
        """Non-existent directory returns empty list."""
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

    def test_populated_directory(self, populated_backup_dir: Path) -> None:
        """All backup files are returned with correct metadata."""
        result = list_snapshots(populated_backup_dir)
        assert result.total == 4

        filenames = {s.filename for s in result.snapshots}
        assert "nucpot-20260813T050000.hourly.sql.gz" in filenames
        assert "nucpot-20260812T020000.daily.sql.gz" in filenames
        assert "nucpot-20260810T020000.weekly.sql.gz" in filenames
        assert "nucpot-20260809T020000.sql.gz" in filenames

    def test_tier_derived_from_filename(self, populated_backup_dir: Path) -> None:
        """Tier is correctly derived from filename suffix."""
        result = list_snapshots(populated_backup_dir)
        by_name = {s.filename: s for s in result.snapshots}

        assert by_name["nucpot-20260813T050000.hourly.sql.gz"].tier == BackupTier.HOURLY
        assert by_name["nucpot-20260812T020000.daily.sql.gz"].tier == BackupTier.DAILY
        assert by_name["nucpot-20260810T020000.weekly.sql.gz"].tier == BackupTier.WEEKLY

    def test_default_tier_for_legacy_files(self, populated_backup_dir: Path) -> None:
        """Files without tier suffix get the default tier."""
        result = list_snapshots(populated_backup_dir)
        by_name = {s.filename: s for s in result.snapshots}
        assert by_name["nucpot-20260809T020000.sql.gz"].tier == BackupTier.DAILY

    def test_custom_default_tier(self, populated_backup_dir: Path) -> None:
        """Default tier can be overridden."""
        result = list_snapshots(
            populated_backup_dir, default_tier=BackupTier.WEEKLY,
        )
        by_name = {s.filename: s for s in result.snapshots}
        assert by_name["nucpot-20260809T020000.sql.gz"].tier == BackupTier.WEEKLY

    def test_size_bytes_populated(self, populated_backup_dir: Path) -> None:
        """Size bytes matches actual file size."""
        result = list_snapshots(populated_backup_dir)
        for snapshot in result.snapshots:
            actual_size = (populated_backup_dir / snapshot.filename).stat().st_size
            assert snapshot.size_bytes == actual_size

    def test_created_at_from_mtime(self, populated_backup_dir: Path) -> None:
        """Created_at matches file modification time."""
        result = list_snapshots(populated_backup_dir)
        for snapshot in result.snapshots:
            actual_mtime = (populated_backup_dir / snapshot.filename).stat().st_mtime
            assert snapshot.created_at == datetime.fromtimestamp(actual_mtime)

    def test_sorted_newest_first(self, populated_backup_dir: Path) -> None:
        """Snapshots are sorted newest first."""
        result = list_snapshots(populated_backup_dir)
        if len(result.snapshots) >= 2:
            assert result.snapshots[0].created_at >= result.snapshots[1].created_at


# ---------------------------------------------------------------------------
# Unit tests — backup_service.get_backup_stats
# ---------------------------------------------------------------------------

class TestGetBackupStats:
    """Tests for backup_service.get_backup_stats."""

    def test_returns_disk_metrics(self, backup_dir: Path) -> None:
        """Stats include total and free bytes from real disk."""
        result = get_backup_stats(backup_dir)
        assert result.total_bytes > 0
        assert result.free_bytes > 0
        assert result.free_bytes <= result.total_bytes

    def test_no_refusal_file(self, backup_dir: Path) -> None:
        """Without refusal file, refusal count is zero."""
        result = get_backup_stats(backup_dir)
        assert result.refusal_count == 0
        assert result.last_refusal_at is None

    def test_with_refusal_file(self, backup_dir: Path, refusal_file: Path) -> None:
        """Refusal file is read correctly."""
        result = get_backup_stats(backup_dir, refusal_file=refusal_file)
        assert result.refusal_count == 3
        assert result.last_refusal_at == datetime(
            2026, 8, 13, 4, 30, 0, tzinfo=UTC,
        )

    def test_corrupt_refusal_file(self, backup_dir: Path, tmp_path: Path) -> None:
        """Corrupt refusal file is treated as zero refusals."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not-json", encoding="utf-8")

        result = get_backup_stats(backup_dir, refusal_file=bad_file)
        assert result.refusal_count == 0
        assert result.last_refusal_at is None

    def test_nonexistent_refusal_file(self, backup_dir: Path) -> None:
        """Non-existent refusal file is treated as zero refusals."""
        result = get_backup_stats(
            backup_dir, refusal_file="/nonexistent/refusals.json",
        )
        assert result.refusal_count == 0
        assert result.last_refusal_at is None

    def test_fallback_on_bad_path(self, tmp_path: Path) -> None:
        """Falls back to root disk if backup path is invalid."""
        result = get_backup_stats("/nonexistent/path/12345")
        assert result.total_bytes > 0


# ---------------------------------------------------------------------------
# Unit tests — schema validation
# ---------------------------------------------------------------------------

class TestBackupSchemas:
    """Tests for Pydantic schema validation."""

    def test_backup_tier_values(self) -> None:
        """BackupTier enum has exactly three values."""
        assert set(BackupTier) == {"hourly", "daily", "weekly"}

    def test_snapshot_response_model(self) -> None:
        """BackupSnapshotResponse validates correctly."""
        snapshot = BackupSnapshotResponse(
            filename="test.sql.gz",
            size_bytes=1024,
            created_at=datetime(2026, 8, 13, 5, 0, 0),
            tier=BackupTier.HOURLY,
        )
        assert snapshot.tier == BackupTier.HOURLY
        assert snapshot.size_bytes == 1024

    def test_snapshot_response_rejects_negative_size(self) -> None:
        """Negative size_bytes is rejected."""
        with pytest.raises(Exception):
            BackupSnapshotResponse(
                filename="test.sql.gz",
                size_bytes=-1,
                created_at=datetime(2026, 8, 13, 5, 0, 0),
                tier=BackupTier.HOURLY,
            )

    def test_stats_response_model(self) -> None:
        """BackupStatsResponse validates correctly."""
        stats = BackupStatsResponse(
            total_bytes=100_000_000,
            free_bytes=50_000_000,
            refusal_count=2,
            last_refusal_at=datetime(2026, 8, 13, 4, 30, 0),
        )
        assert stats.total_bytes == 100_000_000
        assert stats.refusal_count == 2

    def test_stats_response_last_refusal_nullable(self) -> None:
        """last_refusal_at can be None."""
        stats = BackupStatsResponse(
            total_bytes=100_000_000,
            free_bytes=50_000_000,
            refusal_count=0,
        )
        assert stats.last_refusal_at is None

    def test_list_response_empty(self) -> None:
        """BackupListResponse can be empty."""
        response = BackupListResponse(snapshots=[], total=0)
        assert response.total == 0
        assert response.snapshots == []


# ---------------------------------------------------------------------------
# Unit tests — path whitelisting (NFM-3070)
# ---------------------------------------------------------------------------

class TestValidateBackupDir:
    """Tests for _validate_backup_dir path whitelisting."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self, tmp_path: Path) -> None:
        """Patch get_settings to use tmp_path as the allowed root."""
        root = tmp_path / "allowed_root"
        root.mkdir()

        self._settings_patcher = patch(
            "nfm_db.api.admin.backups.get_settings",
            return_value=type(
                "MockSettings",
                (),
                {"backup_dir_roots": [str(root)]},
            )(),
        )
        self._settings_patcher.start()
        self._allowed_root = root

    def teardown_method(self) -> None:
        """Ensure settings patcher is stopped."""
        if hasattr(self, "_settings_patcher"):
            self._settings_patcher.stop()

    def test_allows_path_inside_root(self) -> None:
        """Path inside the allowed root is accepted."""
        result = _validate_backup_dir(str(self._allowed_root / "subdir"))
        assert result == (self._allowed_root / "subdir").resolve()

    def test_allows_root_itself(self) -> None:
        """The root directory itself is accepted."""
        result = _validate_backup_dir(str(self._allowed_root))
        assert result == self._allowed_root.resolve()

    def test_rejects_parent_traversal(self) -> None:
        """../ escape is rejected."""
        inner = self._allowed_root / "deep"
        inner.mkdir()
        with pytest.raises(HTTPException, match="400"):
            _validate_backup_dir(str(inner / ".." / ".."))

    def test_rejects_absolute_path_outside(self) -> None:
        """Absolute path outside allowlist is rejected."""
        with pytest.raises(HTTPException, match="400"):
            _validate_backup_dir("/etc/passwd")

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        """Symlink pointing outside the root is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        link = self._allowed_root / "escape_link"
        link.symlink_to(outside)

        with pytest.raises(HTTPException, match="400"):
            _validate_backup_dir(str(link))

    def test_rejection_does_not_leak_path_existence(self) -> None:
        """Error message does not reveal whether the path exists."""
        inner = self._allowed_root / "real_subdir"
        inner.mkdir()

        with pytest.raises(HTTPException) as exc_info:
            _validate_backup_dir("/etc/shadow")

        detail = exc_info.value.detail
        assert "/etc/shadow" not in detail
        assert str(self._allowed_root) not in detail
        assert "not inside the configured backup directory" in detail

    def test_multiple_roots_first_match_wins(self, tmp_path: Path) -> None:
        """When multiple roots are configured, the first match wins."""
        root2 = tmp_path / "second_root"
        root2.mkdir()

        self._settings_patcher.stop()
        self._settings_patcher = patch(
            "nfm_db.api.admin.backups.get_settings",
            return_value=type(
                "MockSettings",
                (),
                {"backup_dir_roots": [str(root2), str(self._allowed_root)]},
            )(),
        )
        self._settings_patcher.start()

        result = _validate_backup_dir(str(self._allowed_root / "sub"))
        assert result == (self._allowed_root / "sub").resolve()

    def test_empty_roots_uses_default(self, tmp_path: Path) -> None:
        """When backup_dir_roots is empty, the default is used."""
        self._settings_patcher.stop()
        self._settings_patcher = patch(
            "nfm_db.api.admin.backups.get_settings",
            return_value=type(
                "MockSettings",
                (),
                {"backup_dir_roots": []},
            )(),
        )
        self._settings_patcher.start()

        # Default is "/var/backups/nucpot" — our tmp_path is not inside it,
        # so any path will be rejected.
        with pytest.raises(HTTPException, match="400"):
            _validate_backup_dir(str(tmp_path))


class TestValidateRefusalFile:
    """Tests for _validate_refusal_file path whitelisting."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self, tmp_path: Path) -> None:
        """Patch get_settings to use tmp_path as the allowed root."""
        root = tmp_path / "allowed_root"
        root.mkdir()

        self._settings_patcher = patch(
            "nfm_db.api.admin.backups.get_settings",
            return_value=type(
                "MockSettings",
                (),
                {"backup_dir_roots": [str(root)]},
            )(),
        )
        self._settings_patcher.start()
        self._allowed_root = root

    def teardown_method(self) -> None:
        """Ensure settings patcher is stopped."""
        if hasattr(self, "_settings_patcher"):
            self._settings_patcher.stop()

    def test_allows_refusal_inside_root(self) -> None:
        """Refusal file inside the root is accepted."""
        result = _validate_refusal_file(
            str(self._allowed_root / "refusals.json"),
            self._allowed_root,
        )
        assert result == (self._allowed_root / "refusals.json").resolve()

    def test_rejects_refusal_outside_root(self) -> None:
        """Refusal file outside the root is rejected."""
        with pytest.raises(HTTPException, match="400"):
            _validate_refusal_file(
                "/etc/passwd",
                self._allowed_root,
            )

    def test_rejects_refusal_traversal(self) -> None:
        """Refusal file with ../ is rejected."""
        with pytest.raises(HTTPException, match="400"):
            _validate_refusal_file(
                str(self._allowed_root / ".." / ".." / "etc" / "shadow"),
                self._allowed_root,
            )

    def test_rejection_does_not_leak_path(self) -> None:
        """Error message does not reveal whether the file exists."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_refusal_file(
                "/etc/shadow",
                self._allowed_root,
            )

        detail = exc_info.value.detail
        assert "/etc/shadow" not in detail
        assert "not inside the configured backup directory" in detail
