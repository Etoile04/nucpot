"""Backup retention tier classification engine (NFM-3036).

Given a list of backup files and a :class:`RetentionConfig`, classifies each
file into one of the GFS tiers (``hourly``, ``daily``, ``weekly``) or
marks it ``expired`` if the file pool exceeds the configured slot budget.

Algorithm (deterministic, newest-first):

1. Sort ``backup_files`` by ``modified_at`` descending (newest first).
2. The first ``hourly.count`` files → :attr:`Tier.HOURLY`.
3. The next ``daily.count`` files → :attr:`Tier.DAILY`.
4. The next ``weekly.count`` files → :attr:`Tier.WEEKLY`.
5. Any remaining files → :attr:`Tier.EXPIRED`.

This implements the simpler "N newest are hourly, next M daily, next K
weekly" rule from NFM-3024-T1. Edge cases (zero tier counts, fewer
files than a tier's slot budget) short-circuit naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from nfm_db.backup.retention_config import (
    RetentionConfig,
    Tier,
    TierConfig,
)

__all__ = [
    "BackupFile",
    "ClassifiedFile",
    "Tier",
    "classify_tier",
    "TierConfig",
    "RetentionConfig",
]


@dataclass(frozen=True)
class BackupFile:
    """Metadata about a single backup file on disk.

    Attributes:
        path: Absolute or relative path to the backup artifact.
        size_bytes: On-disk size in bytes.
        modified_at: UTC timestamp of when the file was last written.
    """

    path: str
    size_bytes: int
    modified_at: datetime

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError(
                f"size_bytes must be >= 0 (got {self.size_bytes})"
            )


@dataclass(frozen=True)
class ClassifiedFile:
    """A :class:`BackupFile` annotated with its retention tier.

    Attributes:
        file: The original backup file metadata.
        tier: The retention tier assigned by classification.
    """

    file: BackupFile
    tier: Tier


def classify_tier(
    backup_files: list[BackupFile],
    retention_config: RetentionConfig,
) -> list[ClassifiedFile]:
    """Classify each backup file into a retention tier.

    Files are sorted newest-first by ``modified_at`` and assigned
    sequentially to the configured tier slot budgets. Files that
    exceed the total slot budget are marked :attr:`Tier.EXPIRED`.

    The input list is not mutated; a fresh list is returned in the
    same order as the sorted input (newest-first).

    Args:
        backup_files: Backup file metadata, in any order.
        retention_config: The GFS retention schedule (hourly/daily/weekly
            slot counts).

    Returns:
        A list of :class:`ClassifiedFile` entries, ordered newest-first,
        of equal length to *backup_files*.

    Examples:
        With default counts (24 hourly + 7 daily + 4 weekly = 35 slots),
        35 files classify as ``[hourly×24, daily×7, weekly×4]``.
        A 40th file would be marked ``expired``.
    """
    if not backup_files:
        return []

    # Sort newest-first by modified_at. ``reverse=True`` ensures
    # the first entry is the freshest backup. Stable sort preserves
    # input order for ties (e.g. multiple backups in the same second).
    sorted_files = sorted(
        backup_files,
        key=lambda f: f.modified_at,
        reverse=True,
    )

    hourly_budget = retention_config.hourly.count
    daily_budget = retention_config.daily.count
    weekly_budget = retention_config.weekly.count

    hourly_end = hourly_budget
    daily_end = hourly_end + daily_budget
    weekly_end = daily_end + weekly_budget

    classified: list[ClassifiedFile] = []
    for index, backup in enumerate(sorted_files):
        if index < hourly_end:
            tier = Tier.HOURLY
        elif index < daily_end:
            tier = Tier.DAILY
        elif index < weekly_end:
            tier = Tier.WEEKLY
        else:
            tier = Tier.EXPIRED
        classified.append(ClassifiedFile(file=backup, tier=tier))

    return classified