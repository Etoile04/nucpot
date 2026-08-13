"""Backup listing, capacity-stats and refusal-tracking service (NFM-3024-D / NFM-3052).

Public surface used by ``GET /api/admin/backups`` and
``GET /api/admin/backups/stats``:

- :func:`list_snapshots` — scan the backup directory and return snapshots
  with a per-file GFS tier.  Pre-migration snapshots (no metadata)
  serialize as ``tier=None`` per AC3.
- :func:`get_backup_stats` — disk-usage + refusal counter, cached via a
  module-level TTL cache (1s) so the endpoint stays cheap.
- :func:`record_refusal` / :func:`snapshot_refusals` — durability-
  backed refusal counter (persisted to a JSON sidecar).
- :func:`is_backup_enabled` — process-level gate controlled by the
  ``NFM_BACKUP_ENABLED`` env var.

Everything here is plain Python — no database dependency — so the
endpoints can stay read-only and FastAPI's admin role gate applies
upstream.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupRefusalsSnapshot,
    BackupSnapshotResponse,
    BackupStatsResponse,
    BackupTier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level configuration (introspected by tests; never rely on closures)
# ---------------------------------------------------------------------------

BACKUP_DIR: Path = Path("/var/backups/nucpot")
"""Default backup directory; overridable for tests and per-environment config."""

STATS_CACHE_TTL_SECONDS: float = 1.0
"""TTL for the ``get_backup_stats`` cache (per spec: 1 second)."""

_REFUSAL_FILE: Path | None = Path("/var/backups/nucpot/.refusals.json")
"""Path to the JSON sidecar that persists refusal counts across restarts."""

_STATS_CACHE: dict[str, tuple[float, BackupStatsResponse]] = {}
"""TTL cache for :func:`get_backup_stats`. Keyed by absolute backup path."""

_BACKUP_ENABLED: bool | None = None
"""Memoized override for the env-driven enabled gate."""

_REFUSALS: tuple[int, datetime | None] = (0, None)
"""Mutable (count, last_refusal_at) tuple for the refusal counter."""

_DISK_USAGE_FN: Callable[[str], Any] = shutil.disk_usage
"""Indirection so tests can fault-inject OSError from disk-usage calls."""


# ---------------------------------------------------------------------------
# Tier derivation
# ---------------------------------------------------------------------------

_TIER_SUFFIXES: dict[str, BackupTier] = {
    ".hourly": BackupTier.HOURLY,
    ".daily": BackupTier.DAILY,
    ".weekly": BackupTier.WEEKLY,
}


def _tier_from_filename(filename: str) -> BackupTier | None:
    """Derive a tier from a filename suffix, or ``None`` if absent.

    Filename convention: ``<base>.hourly.<ext>``, ``<base>.daily.<ext>``,
    ``<base>.weekly.<ext>``.  Files without a recognised tier suffix are
    **pre-migration** and must surface as ``tier=None`` per AC3 — never
    get auto-bucketed into a default tier.
    """
    lower = filename.lower()
    for suffix, tier in _TIER_SUFFIXES.items():
        if suffix in lower:
            return tier
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_snapshots(
    backup_dir: str | Path,
    *,
    extension: str = ".sql.gz",
) -> BackupListResponse:
    """Scan *backup_dir* and return its snapshots with per-file tier.

    Args:
        backup_dir: Directory containing backup snapshot files.
        extension: File extension filter (default ``.sql.gz``).

    Returns:
        :class:`BackupListResponse` with each snapshot's tier field.
        Pre-migration snapshots return ``tier=None``.
    """
    dir_path = Path(backup_dir)
    if not dir_path.is_dir():
        logger.warning("Backup directory does not exist: %s", dir_path)
        return BackupListResponse(snapshots=[], total=0)

    snapshots: list[BackupSnapshotResponse] = []
    for entry in sorted(
        dir_path.iterdir(),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        if not entry.is_file() or not entry.name.endswith(extension):
            continue
        stat = entry.stat()
        snapshots.append(
            BackupSnapshotResponse(
                filename=entry.name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime),
                tier=_tier_from_filename(entry.name),
            )
        )

    return BackupListResponse(snapshots=snapshots, total=len(snapshots))


def get_backup_stats(backup_dir: str | Path) -> BackupStatsResponse:
    """Return real-time disk + refusal metrics, cached for 1 second.

    Args:
        backup_dir: Directory whose volume is measured.

    Returns:
        :class:`BackupStatsResponse` carrying the four documented fields.

    Raises:
        OSError: Propagated from the disk-usage call when neither the
            backup path nor the root volume can be measured.  The API
            layer maps this to ``503``.
    """
    dir_path = Path(backup_dir)
    cache_key = str(dir_path.resolve()) if dir_path.exists() else str(dir_path)
    cached = _STATS_CACHE.get(cache_key)
    now = time.monotonic()

    if cached is not None and (now - cached[0]) < STATS_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        usage = _DISK_USAGE_FN(str(dir_path))
    except OSError:
        # Fall back to a stable root-volume measurement rather than 503'ing
        # the operator in pathological cases.
        usage = _DISK_USAGE_FN("/")

    count, last_at = _REFUSALS

    stats = BackupStatsResponse(
        total_bytes=int(usage.total),
        free_bytes=int(usage.free),
        refusal_count=count,
        last_refusal_at=last_at,
    )
    _STATS_CACHE[cache_key] = (now, stats)
    return stats


def is_backup_enabled() -> bool:
    """Return whether the backup subsystem is enabled.

    Reads from ``NFM_BACKUP_ENABLED`` once and memoises the result. Tests
    can override the cached value via ``backup_service._BACKUP_ENABLED``.
    """
    global _BACKUP_ENABLED
    if _BACKUP_ENABLED is None:
        env = os.environ.get("NFM_BACKUP_ENABLED", "true").strip().lower()
        _BACKUP_ENABLED = env not in {"0", "false", "no", "off"}
    return _BACKUP_ENABLED


# ---------------------------------------------------------------------------
# Refusal counter (durable across restarts via JSON sidecar)
# ---------------------------------------------------------------------------


def record_refusal() -> datetime:
    """Record one refusal and return the timestamp.

    The count and timestamp are persisted to the sidecar JSON file after
    every update so a process restart does not reset the operator-visible
    refusal state.
    """
    global _REFUSALS
    count, _existing = _REFUSALS
    when = datetime.now(UTC)
    _REFUSALS = (count + 1, when)
    _persist_refusals(_REFUSALS[0], _REFUSALS[1])
    return when


def snapshot_refusals() -> BackupRefusalsSnapshot:
    """Return an immutable snapshot of the live refusal counter."""
    count, last_at = _REFUSALS
    return BackupRefusalsSnapshot(refusal_count=count, last_refusal_at=last_at)


def _load_refusals_from_disk() -> None:
    """Re-hydrate ``_REFUSALS`` from the sidecar JSON file (if present).

    Tolerates missing, malformed, or unreadable files by resetting to
    ``(0, None)`` — never raises.
    """
    global _REFUSALS
    path = _REFUSAL_FILE
    if path is None or not Path(path).exists():
        return

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        logger.warning("Corrupt refusal sidecar at %s — resetting", path)
        _REFUSALS = (0, None)
        return

    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    ts_raw = data.get("last_refusal_at")
    last_at: datetime | None
    if isinstance(ts_raw, str) and ts_raw:
        try:
            last_at = datetime.fromisoformat(ts_raw)
        except ValueError:
            last_at = None
    else:
        last_at = None
    _REFUSALS = (max(count, 0), last_at)


def _persist_refusals(count: int, last_at: datetime | None) -> None:
    """Persist the refusal counter to the sidecar JSON file (best-effort)."""
    path = _REFUSAL_FILE
    if path is None:
        return
    payload = {
        "count": count,
        "last_refusal_at": last_at.isoformat() if last_at is not None else None,
    }
    try:
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        logger.warning("Cannot persist refusal sidecar at %s", path)


# Best-effort hydration on import so the counter survives process restarts.
try:
    _load_refusals_from_disk()
except Exception:  # pragma: no cover — defensive on import
    logger.exception("Unexpected refusal-counter load failure at import")
