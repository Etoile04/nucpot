"""Backup scanning and capacity-stats service — NFM-3017.

Provides pure functions for:

- Scanning the backup directory for snapshot files.
- Deriving a GFS retention tier per snapshot based on filename
  convention (``*.hourly.*``, ``*.daily.*``, ``*.weekly.*``) or
  sidecar JSON metadata.
- Computing real-time disk usage via ``shutil.disk_usage``.
- Reading refusal metrics from a simple JSON counter file.

The service is deliberately filesystem-oriented so it works even
without a database — snapshots are files on disk, and refusal state
is persisted as a lightweight JSON sidecar.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupSnapshotResponse,
    BackupStatsResponse,
    BackupTier,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filename convention helpers
# ---------------------------------------------------------------------------

_TIER_SUFFIX_MAP: dict[str, BackupTier] = {
    ".hourly": BackupTier.HOURLY,
    ".daily": BackupTier.DAILY,
    ".weekly": BackupTier.WEEKLY,
}


def _tier_from_filename(filename: str) -> Optional[BackupTier]:
    """Derive tier from a filename using the dot-suffix convention.

    A snapshot named ``nucpot-20260813T050000.hourly.sql.gz`` maps to
    ``BackupTier.HOURLY``.  Files without a recognised suffix return ``None``.
    """
    lower = filename.lower()
    for suffix, tier in _TIER_SUFFIX_MAP.items():
        if suffix in lower:
            return tier
    return None


# ---------------------------------------------------------------------------
# Refusal tracking
# ---------------------------------------------------------------------------

def _load_refusals(path: Path) -> tuple[int, Optional[datetime]]:
    """Read refusal counter and timestamp from a JSON sidecar.

    Returns (count, last_refusal_at).  Missing or corrupt file yields (0, None).
    """
    if not path.exists():
        return 0, None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        count = int(data.get("count", 0))
        ts_raw = data.get("last_refusal_at")
        last_at = datetime.fromisoformat(ts_raw) if ts_raw else None
        return count, last_at
    except (json.JSONDecodeError, ValueError, TypeError, OSError):
        logger.warning("Corrupt refusal tracking file: %s — resetting", path)
        return 0, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_snapshots(
    backup_dir: str | Path,
    *,
    extension: str = ".sql.gz",
    default_tier: BackupTier = BackupTier.DAILY,
) -> BackupListResponse:
    """Scan *backup_dir* for snapshot files and return metadata.

    Each file's modification time is used as ``created_at`` (creation
    time is not preserved over scp/ftp in most setups).  The tier is
    derived from the filename suffix convention described in
    :func:`_tier_from_filename`; files without a suffix fall back to
    *default_tier*.

    Args:
        backup_dir: Directory containing backup snapshot files.
        extension: File extension to filter (default ``.sql.gz``).
        default_tier: Tier assigned when filename has no recognised suffix.

    Returns:
        :class:`BackupListResponse` with matching snapshots.
    """
    dir_path = Path(backup_dir)
    if not dir_path.is_dir():
        logger.warning("Backup directory does not exist: %s", dir_path)
        return BackupListResponse(snapshots=[], total=0)

    snapshots: list[BackupSnapshotResponse] = []
    for entry in sorted(dir_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not entry.is_file() or not entry.name.endswith(extension):
            continue
        stat = entry.stat()
        tier = _tier_from_filename(entry.name) or default_tier
        snapshots.append(
            BackupSnapshotResponse(
                filename=entry.name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime),
                tier=tier,
            )
        )
    return BackupListResponse(snapshots=snapshots, total=len(snapshots))


def get_backup_stats(
    backup_dir: str | Path,
    refusal_file: str | Path | None = None,
) -> BackupStatsResponse:
    """Return real-time disk and refusal metrics.

    Uses ``shutil.disk_usage`` on the *backup_dir* volume.  Refusal
    metrics are loaded from a JSON sidecar file (if provided and present).

    Args:
        backup_dir: Directory whose volume is measured.
        refusal_file: Path to the refusal JSON sidecar (optional).

    Returns:
        :class:`BackupStatsResponse` with current metrics.
    """
    dir_path = Path(backup_dir)
    try:
        usage = shutil.disk_usage(str(dir_path))
    except OSError:
        logger.error("Cannot read disk usage for: %s", dir_path)
        usage = shutil.disk_usage("/")

    refusal_count = 0
    last_refusal_at: Optional[datetime] = None
    if refusal_file is not None:
        refusal_count, last_refusal_at = _load_refusals(Path(refusal_file))

    return BackupStatsResponse(
        total_bytes=usage.total,
        free_bytes=usage.free,
        refusal_count=refusal_count,
        last_refusal_at=last_refusal_at,
    )
