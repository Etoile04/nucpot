"""Backup configuration schema with tiered GFS retention.

NFM-3066 / NFM-3024-A1 — primary deliverable for the tiered GFS retention
feature. Adds the canonical ``retention`` object (hourly/daily/weekly) while
keeping ``retentionDays`` working as a deprecated alias for one release cycle.

Scope: config layer only. Downstream consumers (NFM-3050 3-tier GFS scheduler,
NFM-3016 capacity guardrails) consume the materialised ``BackupConfig.retention``
field. No scheduler / storage logic is wired up here.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Once-per-process deprecation flag
# ---------------------------------------------------------------------------
# Module-level state is intentional: the deprecation log MUST fire at most
# once per process start (NFM-3066 AC3). Tests use ``reset_retention_days_warned``
# to start from a clean slate.
_retention_days_warned: bool = False


def reset_retention_days_warned() -> None:
    """Clear the once-per-process deprecation log guard.

    Exposed for tests; production callers should NOT invoke this. Resetting
    the guard in production would allow the WARN to fire repeatedly across
    config reloads, defeating the AC3 contract.
    """
    global _retention_days_warned
    _retention_days_warned = False


# ---------------------------------------------------------------------------
# Per-tier retention spec
# ---------------------------------------------------------------------------


class TierSpec(BaseModel):
    """A single retention tier: ``intervalMinutes`` (cadence) + ``count`` (how many to keep)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    intervalMinutes: PositiveInt = Field(
        ...,
        description="Cadence in minutes at which this tier produces a backup.",
    )
    count: PositiveInt = Field(
        ...,
        description="Number of backups to retain in this tier.",
    )


# ---------------------------------------------------------------------------
# Top-level retention object
# ---------------------------------------------------------------------------


class RetentionConfig(BaseModel):
    """Three-tier GFS retention block: hourly, daily, weekly.

    All three tiers are required. Per-tier cadence and count are independent;
    the only constraints are the ``PositiveInt`` validators on each field.
    """

    model_config = ConfigDict(extra="forbid")

    hourly: TierSpec
    daily: TierSpec
    weekly: TierSpec


# ---------------------------------------------------------------------------
# Top-level backup config
# ---------------------------------------------------------------------------


# Canonical tier names — exposed as Literal so downstream code can switch on them.
TierName = Literal["hourly", "daily", "weekly"]

# Canonical tier cadences (minutes). Used when deriving the legacy ``retentionDays``
# flat mode into a 3-tier retention block.
_LEGACY_DAILY_INTERVAL_MINUTES: int = 24 * 60  # 1440
_LEGACY_HOURLY_INTERVAL_MINUTES: int = 60
_LEGACY_WEEKLY_INTERVAL_MINUTES: int = 7 * 24 * 60  # 10080


class _MetricsConfig(BaseModel):
    """Backup-metrics emission controls."""

    model_config = ConfigDict(extra="forbid")

    pushOnRefusal: bool = False


class BackupConfig(BaseModel):
    """Top-level backup config object (loaded from ``config.json.backup``).

    Canonical shape::

        {
            "enabled": true,
            "intervalMinutes": 60,
            "dir": "<instance data>/backups",
            "retention": {
                "hourly": {"intervalMinutes": 60,    "count": 24},
                "daily":  {"intervalMinutes": 1440,  "count": 7},
                "weekly": {"intervalMinutes": 10080, "count": 4}
            },
            "maxTotalBytes": 12884901888,
            "minFreeBytes":  21474836480,
            "refuseOnFloorBreach": true,
            "metrics": {"pushOnRefusal": true}
        }

    Backward compatibility: ``retentionDays`` (deprecated) is accepted. When
    supplied without a sibling ``retention`` block, a single WARN is emitted
    at startup and a derived ``retention`` block is materialised (legacy flat
    mode implemented as ``retentionDays`` daily tiers + minimal hourly/
    weekly tiers so downstream NFM-3050 consumers do not fork).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    intervalMinutes: PositiveInt = Field(..., description="Base backup cadence in minutes.")
    dir: str = Field(..., min_length=1)
    retention: RetentionConfig | None = None
    retentionDays: PositiveInt | None = Field(
        default=None,
        description=(
            "DEPRECATED: migrate to the ``retention`` object "
            "(hourly/daily/weekly tiers)."
        ),
    )
    maxTotalBytes: int = Field(..., ge=0)
    minFreeBytes: int = Field(..., ge=0)
    refuseOnFloorBreach: bool = True
    metrics: _MetricsConfig = Field(default_factory=_MetricsConfig)

    @model_validator(mode="after")
    def _resolve_retention_from_legacy_alias(self) -> BackupConfig:
        """Derive ``retention`` from ``retentionDays`` when only the alias is set.

        Contract (NFM-3066):
          - ``retention`` present (canonical) → no WARN, ``retentionDays`` ignored.
          - ``retention`` absent, ``retentionDays`` present → emit one WARN,
            then materialise a derived ``retention`` block (legacy flat mode
            implemented as ``retentionDays`` daily tiers + minimal hourly/
            weekly tiers so downstream NFM-3050 consumers do not fork).
          - Neither present → ValidationError ("must provide either").
        """
        global _retention_days_warned

        if self.retention is not None:
            # Canonical path: explicit retention block wins. The deprecated
            # alias is silently ignored so already-migrated deployments that
            # still carry a stale ``retentionDays`` field don't double-warn.
            return self

        if self.retentionDays is None:
            raise ValueError(
                "backup config requires either 'retention' (canonical hourly/"
                "daily/weekly tiers) or the legacy 'retentionDays' alias."
            )

        # Legacy path: emit exactly one WARN across the process lifetime.
        if not _retention_days_warned:
            logger.warning(
                "config.retentionDays is deprecated; migrate to "
                "backup.retention (hourly|daily|weekly)",
            )
            _retention_days_warned = True

        # Derive a 3-tier retention block so NFM-3050 (3-tier GFS scheduler)
        # can consume the same ``BackupConfig.retention`` shape regardless of
        # whether the operator is on the canonical or legacy config.
        days = self.retentionDays
        object.__setattr__(
            self,
            "retention",
            RetentionConfig(
                hourly=TierSpec(
                    intervalMinutes=_LEGACY_HOURLY_INTERVAL_MINUTES,
                    # Keep 24 hourly slots; the legacy 7-day window is fully
                    # covered by the daily tier.
                    count=24,
                ),
                daily=TierSpec(
                    intervalMinutes=_LEGACY_DAILY_INTERVAL_MINUTES,
                    count=days,
                ),
                weekly=TierSpec(
                    intervalMinutes=_LEGACY_WEEKLY_INTERVAL_MINUTES,
                    # Single weekly slot is enough to bridge the legacy flat
                    # window; downstream policy can grow this when NFM-3050
                    # ships the real GFS scheduler.
                    count=max(1, days // 7),
                ),
            ),
        )
        return self


__all__ = [
    "BackupConfig",
    "RetentionConfig",
    "TierName",
    "TierSpec",
    "reset_retention_days_warned",
]
