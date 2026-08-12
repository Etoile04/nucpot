"""Backup configuration models — NFM-3014.

Provides Pydantic models for tiered backup retention and capacity
guardrails.  Designed to replace the flat ``retentionDays`` integer with
a Granular-Full-Schedule (GFS) tiered retention object while maintaining
backward compatibility during migration.

Env var prefix: ``NFM_BACKUP_`` (wired through the parent Settings class).

Defaults
--------
- retention.hourly: intervalMinutes=60, count=24
- retention.daily:  intervalMinutes=1440, count=7
- retention.weekly: intervalMinutes=10080, count=4
- maxTotalBytes: 12 GiB (12_884_901_888)
- minFreeBytes:  20 GiB (21_474_836_480)
- refuseOnFloorBreach: True
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class RetentionTier(BaseModel):
    """A single retention tier with interval and snapshot count.

    Attributes:
        interval_minutes: Minutes between snapshots in this tier.
        count: Maximum number of snapshots to keep in this tier.
    """

    interval_minutes: int = Field(
        gt=0, description="Minutes between snapshots"
    )
    count: int = Field(
        gt=0, description="Max snapshots to keep"
    )


class TieredRetention(BaseModel):
    """Granular-Full-Schedule retention with three tiers.

    Attributes:
        hourly: Hourly snapshots (default: 60 min, 24 copies).
        daily: Daily snapshots (default: 1440 min, 7 copies).
        weekly: Weekly snapshots (default: 10080 min, 4 copies).
    """

    hourly: RetentionTier = Field(
        default_factory=lambda: RetentionTier(interval_minutes=60, count=24),
        description="Hourly retention tier",
    )
    daily: RetentionTier = Field(
        default_factory=lambda: RetentionTier(interval_minutes=1440, count=7),
        description="Daily retention tier",
    )
    weekly: RetentionTier = Field(
        default_factory=lambda: RetentionTier(interval_minutes=10080, count=4),
        description="Weekly retention tier",
    )


class BackupConfig(BaseModel):
    """Backup configuration with tiered retention and capacity guardrails.

    Attributes:
        retention: Tiered GFS retention object (None = not configured yet).
        retention_days: Legacy flat retention in days (backward compat).
        max_total_bytes: Maximum total backup storage in bytes (12 GiB).
        min_free_bytes: Minimum free disk space before floor breach (20 GiB).
        refuse_on_floor_breach: Reject new backups when floor is breached.
    """

    retention: Optional[TieredRetention] = Field(
        default=None,
        description="Tiered GFS retention (None = use legacy fallback)",
    )
    retention_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="Legacy flat retention in days (deprecated)",
    )
    max_total_bytes: int = Field(
        default=12_884_901_888,
        ge=0,
        description="Max total backup storage in bytes (default 12 GiB)",
    )
    min_free_bytes: int = Field(
        default=21_474_836_480,
        ge=0,
        description="Min free disk space before floor breach (default 20 GiB)",
    )
    refuse_on_floor_breach: bool = Field(
        default=True,
        description="Reject new backups when free space < minFreeBytes",
    )


def check_retention_deprecation(config: BackupConfig) -> None:
    """Emit a deprecation warning if legacy retentionDays is in use.

    Fires a ``[DEPRECATION]`` warning when ``retention_days`` is set but
    the new ``retention`` tiered object is absent, signaling the operator
    has not yet migrated to the tiered schema.

    Args:
        config: The resolved backup configuration.
    """
    if config.retention_days is not None and config.retention is None:
        logger.warning(
            "[DEPRECATION] backup.retentionDays is deprecated and will be "
            "removed in a future release. Migrate to "
            "backup.retention.{hourly,daily,weekly}."
        )
