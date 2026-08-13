"""Tests for admin backup API endpoints (NFM-3024-D / NFM-3052).

Acceptance criteria covered:

* AC1 — ``GET /api/admin/backups`` returns snapshots each with a ``tier``
  field; existing fields unchanged.
* AC2 — ``GET /api/admin/backups/stats`` returns the documented shape
  (``total_bytes``, ``free_bytes``, ``refusal_count``, ``last_refusal_at``)
  with non-negative integer numerics.
* AC3 — Pre-migration snapshots surface ``tier=None``; new snapshots
  surface the correct tier (placeholder for the NFM-3024 tier engine).
* AC4 — Fresh install hits both endpoints without 5xx; legacy state
  hits both endpoints without 5xx.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pydantic
import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupSnapshotResponse,
    BackupStatsResponse,
    BackupTier,
)
from nfm_db.services import backup_service

LIST_ENDPOINT = "/api/admin/backups"
STATS_ENDPOINT = "/api/admin/backups/stats"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    """Empty temporary backup directory."""
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


@pytest.fixture
def populated_backup_dir(backup_dir: Path) -> Path:
    """Populate the backup directory with tier-suffixed and legacy files."""
    files = {
        "nucpot-20260813T050000.hourly.sql.gz": b"hourly-data",
        "nucpot-20260812T020000.daily.sql.gz": b"daily-data",
        "nucpot-20260810T020000.weekly.sql.gz": b"weekly-data",
        # Pre-migration: no tier suffix in the filename (AC3).
        "nucpot-20260809T020000.sql.gz": b"legacy-data",
        # Non-backup file (should be filtered out by extension).
        "README.txt": b"notes",
    }
    for name, payload in files.items():
        (backup_dir / name).write_bytes(payload)
    return backup_dir


@pytest.fixture
def refusal_file(tmp_path: Path) -> Path:
    """Refusal-tracking sidecar JSON file with two refusals."""
    path = tmp_path / "refusals.json"
    path.write_text(
        json.dumps(
            {
                "count": 2,
                "last_refusal_at": "2026-08-13T04:30:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset module-level singletons between tests for isolation."""
    # Ensure a clean enabled flag unless a test sets it explicitly.
    if not hasattr(backup_service, "_BACKUP_ENABLED"):
        backup_service._BACKUP_ENABLED = True
    backup_service._BACKUP_ENABLED = True
    backup_service._STATS_CACHE = {}
    backup_service._REFUSALS = (0, None)
    monkeypatch.delenv("NFM_BACKUP_ENABLED", raising=False)
    yield


@pytest.fixture
async def client(db_session):
    """Async test client with DB override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Service unit — list_snapshots / tier derivation (AC1, AC3)
# ---------------------------------------------------------------------------


class TestListSnapshotsTier:
    """Each snapshot must expose a ``tier`` field per AC1/AC3."""

    def test_returns_response_wrapper(self, populated_backup_dir: Path) -> None:
        result = backup_service.list_snapshots(populated_backup_dir)
        assert isinstance(result, BackupListResponse)
        assert result.total == 4

    def test_each_snapshot_has_tier_field(self, populated_backup_dir: Path) -> None:
        result = backup_service.list_snapshots(populated_backup_dir)
        assert len(result.snapshots) >= 1
        for snap in result.snapshots:
            assert "tier" in snap.model_dump()

    def test_pre_migration_snapshots_have_null_tier(
        self, populated_backup_dir: Path
    ) -> None:
        """Files without a tier suffix serialize as ``tier=None`` (AC3)."""
        result = backup_service.list_snapshots(populated_backup_dir)
        legacy = next(
            s for s in result.snapshots
            if s.filename == "nucpot-20260809T020000.sql.gz"
        )
        assert legacy.tier is None

    def test_known_tier_snapshots_have_correct_tier(
        self, populated_backup_dir: Path
    ) -> None:
        """Files with .hourly/.daily/.weekly derive the correct tier."""
        result = backup_service.list_snapshots(populated_backup_dir)
        by_name = {s.filename: s for s in result.snapshots}

        assert by_name["nucpot-20260813T050000.hourly.sql.gz"].tier == BackupTier.HOURLY
        assert by_name["nucpot-20260812T020000.daily.sql.gz"].tier == BackupTier.DAILY
        assert by_name["nucpot-20260810T020000.weekly.sql.gz"].tier == BackupTier.WEEKLY

    def test_filters_by_extension(self, populated_backup_dir: Path) -> None:
        result = backup_service.list_snapshots(
            populated_backup_dir, extension=".sql.gz"
        )
        filenames = {s.filename for s in result.snapshots}
        assert "README.txt" not in filenames
        assert all(f.endswith(".sql.gz") for f in filenames)

    def test_nonexistent_directory_returns_empty(self, tmp_path: Path) -> None:
        result = backup_service.list_snapshots(tmp_path / "missing")
        assert result.total == 0
        assert result.snapshots == []

    def test_size_bytes_is_positive(self, populated_backup_dir: Path) -> None:
        result = backup_service.list_snapshots(populated_backup_dir)
        for snap in result.snapshots:
            assert snap.size_bytes > 0

    def test_created_at_from_mtime(self, populated_backup_dir: Path) -> None:
        result = backup_service.list_snapshots(populated_backup_dir)
        for snap in result.snapshots:
            actual = (populated_backup_dir / snap.filename).stat().st_mtime
            assert snap.created_at == datetime.fromtimestamp(actual)


# ---------------------------------------------------------------------------
# Service unit — refusal counter (AC2 semantics)
# ---------------------------------------------------------------------------


class TestRefusalCounter:
    """Refusal counter persistence + first/last semantics."""

    def test_initial_state_zero_no_timestamp(self) -> None:
        snapshot = backup_service.snapshot_refusals()
        assert snapshot.refusal_count == 0
        assert snapshot.last_refusal_at is None

    def test_first_refusal_records_count_and_timestamp(self) -> None:
        when = backup_service.record_refusal()
        snapshot = backup_service.snapshot_refusals()
        assert snapshot.refusal_count == 1
        assert snapshot.last_refusal_at is not None
        assert when == snapshot.last_refusal_at

    def test_subsequent_refusals_increment_count(self) -> None:
        backup_service.record_refusal()
        backup_service.record_refusal()
        snapshot = backup_service.snapshot_refusals()
        # Autouse fixture resets _REFUSALS to (0, None) before this test,
        # so two ``record_refusal()`` calls surface as ``count == 2``.
        assert snapshot.refusal_count == 2
        assert snapshot.last_refusal_at is not None

    def test_load_existing_refusal_file(self, refusal_file: Path, monkeypatch) -> None:
        """Sidecar JSON load on startup preserves persisted counts."""
        monkeypatch.setattr(backup_service, "_REFUSAL_FILE", refusal_file)
        backup_service._load_refusals_from_disk()
        snapshot = backup_service.snapshot_refusals()
        assert snapshot.refusal_count == 2
        assert snapshot.last_refusal_at == datetime(
            2026, 8, 13, 4, 30, 0, tzinfo=UTC
        )

    def test_corrupt_refusal_file_is_treated_as_zero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not-json", encoding="utf-8")
        monkeypatch.setattr(backup_service, "_REFUSAL_FILE", bad)
        backup_service._load_refusals_from_disk()
        snapshot = backup_service.snapshot_refusals()
        assert snapshot.refusal_count == 0
        assert snapshot.last_refusal_at is None


# ---------------------------------------------------------------------------
# Service unit — TTL cache (AC2 perf constraint)
# ---------------------------------------------------------------------------


class TestStatsTTLCache:
    """The stats endpoint must cache with a 1-second TTL."""

    def test_cache_hit_within_one_second(self, tmp_path: Path) -> None:
        """Two calls within 1s reuse the cached stats (no extra disk I/O)."""
        first = backup_service.get_backup_stats(tmp_path)
        second = backup_service.get_backup_stats(tmp_path)
        assert first is second

    def test_cache_expires_after_one_second(self, tmp_path: Path) -> None:
        """After the TTL, the next call recomputes a fresh value."""
        first = backup_service.get_backup_stats(tmp_path)
        # Roll the cached timestamp backwards so it appears expired.
        cache = backup_service._STATS_CACHE
        key = next(iter(cache))
        ts, value = cache[key]
        cache[key] = (ts - 2.0, value)
        second = backup_service.get_backup_stats(tmp_path)
        assert first is not second

    def test_cache_ttl_is_one_second(self) -> None:
        assert backup_service.STATS_CACHE_TTL_SECONDS == 1.0


# ---------------------------------------------------------------------------
# Service unit — disk metrics (AC2 shape)
# ---------------------------------------------------------------------------


class TestGetBackupStatsShape:
    """Stats endpoint returns the documented envelope."""

    def test_returns_stats_response(self, tmp_path: Path) -> None:
        result = backup_service.get_backup_stats(tmp_path)
        assert isinstance(result, BackupStatsResponse)

    def test_numeric_fields_are_non_negative(self, tmp_path: Path) -> None:
        result = backup_service.get_backup_stats(tmp_path)
        assert result.total_bytes >= 0
        assert result.free_bytes >= 0
        assert result.refusal_count >= 0
        assert result.free_bytes <= result.total_bytes

    def test_last_refusal_at_is_none_when_count_zero(self, tmp_path: Path) -> None:
        result = backup_service.get_backup_stats(tmp_path)
        assert result.last_refusal_at is None

    def test_refusal_count_propagates(self, tmp_path: Path) -> None:
        backup_service.record_refusal()
        backup_service.record_refusal()
        result = backup_service.get_backup_stats(tmp_path)
        assert result.refusal_count == 2

    def test_disk_error_falls_back_to_root(self) -> None:
        result = backup_service.get_backup_stats("/__nope__/__nope__")
        assert result.total_bytes > 0


# ---------------------------------------------------------------------------
# Service unit — backup-enabled gate (404 envelope)
# ---------------------------------------------------------------------------


class TestBackupEnabledGate:
    def test_default_is_enabled(self, monkeypatch) -> None:
        monkeypatch.delenv("NFM_BACKUP_ENABLED", raising=False)
        monkeypatch.setattr(backup_service, "_BACKUP_ENABLED", None, raising=False)
        backup_service._BACKUP_ENABLED = None
        assert backup_service.is_backup_enabled() is True

    def test_disabled_via_env(self, monkeypatch) -> None:
        monkeypatch.setenv("NFM_BACKUP_ENABLED", "false")
        backup_service._BACKUP_ENABLED = None
        assert backup_service.is_backup_enabled() is False


# ---------------------------------------------------------------------------
# API integration — endpoints
# ---------------------------------------------------------------------------


class TestAdminBackupsListEndpoint:
    """GET /api/admin/backups."""

    @pytest.mark.asyncio
    async def test_returns_envelope_with_snapshots(
        self, client: AsyncClient, populated_backup_dir: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(backup_service, "BACKUP_DIR", populated_backup_dir)
        backup_service.BACKUP_DIR = populated_backup_dir
        resp = await client.get(LIST_ENDPOINT)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert "snapshots" in data
        assert "total" in data
        assert data["total"] == 4
        for snap in data["snapshots"]:
            assert "tier" in snap
            assert "filename" in snap
            assert "sizeBytes" in snap
            assert "createdAt" in snap

    @pytest.mark.asyncio
    async def test_404_when_backup_disabled(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        backup_service._BACKUP_ENABLED = False
        resp = await client.get(LIST_ENDPOINT)
        assert resp.status_code == 404
        backup_service._BACKUP_ENABLED = True


class TestAdminBackupsStatsEndpoint:
    """GET /api/admin/backups/stats."""

    @pytest.mark.asyncio
    async def test_returns_envelope_shape(
        self, client: AsyncClient, backup_dir: Path, monkeypatch
    ) -> None:
        backup_service.BACKUP_DIR = backup_dir
        resp = await client.get(STATS_ENDPOINT)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        for key in ("totalBytes", "freeBytes", "refusalCount", "lastRefusalAt"):
            assert key in data, f"missing key {key}"
        assert isinstance(data["totalBytes"], int)
        assert isinstance(data["freeBytes"], int)
        assert isinstance(data["refusalCount"], int)
        assert data["lastRefusalAt"] is None

    @pytest.mark.asyncio
    async def test_404_when_backup_disabled(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        backup_service._BACKUP_ENABLED = False
        resp = await client.get(STATS_ENDPOINT)
        assert resp.status_code == 404
        backup_service._BACKUP_ENABLED = True

    @pytest.mark.asyncio
    async def test_503_when_disk_usage_raises(
        self, client: AsyncClient, backup_dir: Path, monkeypatch
    ) -> None:
        """If shutil.disk_usage raises for the backup dir, return 503."""

        def boom(_path):
            raise OSError("simulated disk stat failure")

        monkeypatch.setattr(backup_service, "_BACKUP_ENABLED", True)
        backup_service._BACKUP_ENABLED = True
        backup_service.BACKUP_DIR = backup_dir
        monkeypatch.setattr(backup_service, "_DISK_USAGE_FN", boom)
        backup_service._DISK_USAGE_FN = boom
        resp = await client.get(STATS_ENDPOINT)
        assert resp.status_code == 503


class TestFreshAndLegacyInstallFixtures:
    """AC4: fresh-install and legacy-state both hit endpoints without 5xx."""

    @pytest.mark.asyncio
    async def test_fresh_install_no_backups_no_refusals(
        self, client: AsyncClient, backup_dir: Path, monkeypatch
    ) -> None:
        """A fresh install: no snapshots, no refusals — both endpoints succeed."""
        backup_service._BACKUP_ENABLED = True
        backup_service.BACKUP_DIR = backup_dir

        list_resp = await client.get(LIST_ENDPOINT)
        stats_resp = await client.get(STATS_ENDPOINT)

        assert list_resp.status_code == 200
        assert stats_resp.status_code == 200
        assert list_resp.json()["data"]["total"] == 0
        assert stats_resp.json()["data"]["refusalCount"] == 0
        assert stats_resp.json()["data"]["lastRefusalAt"] is None

    @pytest.mark.asyncio
    async def test_legacy_state_snapshots_without_tier_marker(
        self, client: AsyncClient, backup_dir: Path, monkeypatch
    ) -> None:
        """Legacy state: pre-migration snapshots surface ``tier: null``."""
        legacy_files = {
            "nucpot-20260809T020000.sql.gz": b"oldest",
            "nucpot-20260808T020000.sql.gz": b"old",
            "nucpot-20260807T020000.sql.gz": b"older",
        }
        for name, payload in legacy_files.items():
            (backup_dir / name).write_bytes(payload)
        backup_service._BACKUP_ENABLED = True
        backup_service.BACKUP_DIR = backup_dir

        list_resp = await client.get(LIST_ENDPOINT)
        stats_resp = await client.get(STATS_ENDPOINT)

        assert list_resp.status_code == 200
        assert stats_resp.status_code == 200
        snapshots = list_resp.json()["data"]["snapshots"]
        assert len(snapshots) == 3
        for snap in snapshots:
            assert snap["tier"] is None

    @pytest.mark.asyncio
    async def test_legacy_state_with_persisted_refusals(
        self, client: AsyncClient, backup_dir: Path, refusal_file: Path, monkeypatch
    ) -> None:
        """Legacy refusal sidecar preserves count + timestamp across restarts."""
        backup_service._BACKUP_ENABLED = True
        backup_service.BACKUP_DIR = backup_dir
        backup_service._REFUSAL_FILE = refusal_file
        backup_service._load_refusals_from_disk()

        resp = await client.get(STATS_ENDPOINT)
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["refusalCount"] == 2
        assert body["lastRefusalAt"] is not None
        assert "T" in body["lastRefusalAt"]
        assert body["lastRefusalAt"].endswith(("+00:00", "Z"))


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestBackupSchemas:
    def test_tier_enum_values(self) -> None:
        assert {t.value for t in BackupTier} == {"hourly", "daily", "weekly"}

    def test_snapshot_tier_is_optional(self) -> None:
        """Pre-migration snapshots must serialize with tier=None (AC3)."""
        snap = BackupSnapshotResponse(
            filename="x.sql.gz",
            size_bytes=10,
            created_at=datetime(2026, 1, 1),
        )
        assert snap.tier is None

    def test_stats_last_refusal_at_default_none(self) -> None:
        stats = BackupStatsResponse(total_bytes=1, free_bytes=1, refusal_count=0)
        assert stats.last_refusal_at is None

    def test_snapshot_rejects_negative_size(self) -> None:
        with pytest.raises(pydantic.ValidationError):
            BackupSnapshotResponse(
                filename="x.sql.gz",
                size_bytes=-1,
                created_at=datetime(2026, 1, 1),
                tier=BackupTier.HOURLY,
            )
