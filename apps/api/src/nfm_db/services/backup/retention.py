"""Tiered GFS retention enforcement (NFM-3024 T2).

Given a directory of backup snapshots, applies a :class:`TieredRetention`
policy by:

1. Grouping files into ``hourly | daily | weekly`` buckets via the
   filename-suffix convention (``.hourly.``, ``.daily.``, ``.weekly.``).
2. Within each bucket, sorting oldest-first (by ``st_mtime``) and deleting
   the oldest entries until the bucket contains at most
   ``retention.<tier>.count`` files.

The function never *increases* the snapshot count — only prunes. Files
without a recognised suffix are left untouched so they can be handled by
the downstream capacity-cap enforcement (:class:`CapacityGuardrails`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nfm_db.config.backup import TieredRetention
from nfm_db.schemas.backup import BackupTier

logger = logging.getLogger(__name__)

_TIER_SUFFIX_TO_TIER: dict[str, BackupTier] = {
    ".hourly": BackupTier.HOURLY,
    ".daily": BackupTier.DAILY,
    ".weekly": BackupTier.WEEKLY,
}


@dataclass(frozen=True)
class RetentionResult:
    """Outcome of applying tiered retention to a backup directory."""

    pruned_paths: list[Path] = field(default_factory=list)
    pruned_count: int = 0
    kept_by_tier: dict[BackupTier, int] = field(default_factory=dict)


def _tier_from_filename(filename: str) -> Optional[BackupTier]:
    """Map a filename to its GFS tier using the dot-suffix convention."""
    lower = filename.lower()
    for suffix, tier in _TIER_SUFFIX_TO_TIER.items():
        if suffix in lower:
            return tier
    return None


def _list_snapshot_files(backup_dir: Path) -> list[Path]:
    """List snapshot files in *backup_dir* (regular files only)."""
    if not backup_dir.exists():
        return []
    return [p for p in backup_dir.iterdir() if p.is_file()]


def apply_tiered_retention(
    backup_dir: Path,
    retention: TieredRetention,
) -> RetentionResult:
    """Prune oldest snapshots in each tier beyond the configured ``count``.

    Parameters:
        backup_dir: Directory containing snapshot files.
        retention:  Tiered GFS policy (counts per tier).

    Returns:
        :class:`RetentionResult` describing what was pruned and the
        post-prune per-tier counts. The function is a no-op when the
        directory does not exist or contains no snapshot files.
    """
    files = _list_snapshot_files(backup_dir)
    if not files:
        return RetentionResult()

    # Group files by tier.
    tier_buckets: dict[BackupTier, list[Path]] = {t: [] for t in BackupTier}
    for path in files:
        tier = _tier_from_filename(path.name)
        if tier is not None:
            tier_buckets[tier].append(path)

    tier_caps = {
        BackupTier.HOURLY: retention.hourly.count,
        BackupTier.DAILY: retention.daily.count,
        BackupTier.WEEKLY: retention.weekly.count,
    }

    pruned: list[Path] = []
    kept_by_tier: dict[BackupTier, int] = {}

    for tier, bucket in tier_buckets.items():
        # Sort oldest-first so we keep the newest.
        bucket_sorted = sorted(bucket, key=lambda p: p.stat().st_mtime)
        cap = tier_caps[tier]
        if len(bucket_sorted) <= cap:
            kept_by_tier[tier] = len(bucket_sorted)
            continue
        excess = bucket_sorted[: len(bucket_sorted) - cap]
        kept = bucket_sorted[len(bucket_sorted) - cap :]
        for victim in excess:
            try:
                victim.unlink(missing_ok=True)
                pruned.append(victim)
            except OSError as exc:
                logger.warning(
                    "Retention prune failed for %s: %s", victim, exc
                )
        kept_by_tier[tier] = len(kept)
        logger.info(
            "Tier retention pruned %d %s snapshot(s), kept %d (cap=%d)",
            len(excess),
            tier.value,
            len(kept),
            cap,
        )

    # Files without a recognised tier suffix are not pruned here — they
    # are handled by the downstream capacity cap enforcement.
    return RetentionResult(
        pruned_paths=pruned,
        pruned_count=len(pruned),
        kept_by_tier=kept_by_tier,
    )