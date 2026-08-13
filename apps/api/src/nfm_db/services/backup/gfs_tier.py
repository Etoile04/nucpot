"""GFS (Grandfather-Father-Son) tier classification engine.

NFM-3050 / NFM-3024-B

Pure-function tier assignment: given a list of backup snapshots with their
mtimes and a tier configuration, returns new snapshot objects with the ``tier``
field populated.  Deterministic and idempotent — the same (now, mtimes, config)
always produces the same output.

Algorithm
---------
1. Sort snapshots newest-first (descending mtime).
2. Walk the list, tracking slot occupancy per tier.
3. For each snapshot, assign the highest-priority tier whose age window
   still has capacity:
   - ``hourly`` if age < hourly_max_age and hourly slots remain
   - ``daily``  if age < daily_max_age  and daily slots remain
   - ``weekly`` if age < weekly_max_age and weekly slots remain
   - ``prune``  otherwise (candidate for deletion, handled by NFM-3024-C)

The max_age for each tier equals ``interval_minutes * count``, which is the
total time span that tier covers.  Newer snapshots always get priority
within their tier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

from nfm_db.services.backup.models import BackupSnapshot


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GFSTierConfig:
    """Per-tier configuration for the GFS retention schedule.

    Attributes:
        hourly_interval_minutes: Minutes between hourly snapshots.
        hourly_count:            Max number of hourly snapshots to retain.
        daily_interval_minutes:  Minutes between daily snapshots.
        daily_count:             Max number of daily snapshots to retain.
        weekly_interval_minutes: Minutes between weekly snapshots.
        weekly_count:            Max number of weekly snapshots to retain.
    """

    hourly_interval_minutes: int
    hourly_count: int
    daily_interval_minutes: int
    daily_count: int
    weekly_interval_minutes: int
    weekly_count: int

    def __post_init__(self) -> None:
        if self.hourly_interval_minutes <= 0:
            raise ValueError("hourly_interval_minutes must be positive")
        if self.hourly_count <= 0:
            raise ValueError("hourly_count must be positive")
        if self.daily_interval_minutes <= 0:
            raise ValueError("daily_interval_minutes must be positive")
        if self.daily_count <= 0:
            raise ValueError("daily_count must be positive")
        if self.weekly_interval_minutes <= 0:
            raise ValueError("weekly_interval_minutes must be positive")
        if self.weekly_count <= 0:
            raise ValueError("weekly_count must be positive")

    @property
    def hourly_max_age_hours(self) -> float:
        """Maximum age (in hours) for the hourly tier window."""
        return (self.hourly_interval_minutes * self.hourly_count) / 60.0

    @property
    def daily_max_age_hours(self) -> float:
        """Maximum age (in hours) for the daily tier window."""
        return (self.daily_interval_minutes * self.daily_count) / 60.0

    @property
    def weekly_max_age_hours(self) -> float:
        """Maximum age (in hours) for the weekly tier window."""
        return (self.weekly_interval_minutes * self.weekly_count) / 60.0


def default_gfs_config() -> GFSTierConfig:
    """Return the default GFS config matching the NFM-3024 spec table.

    ====== ===== ====== ========
    Hourly 1h    24    7.2 GiB
    Daily  1d    7     2.1 GiB
    Weekly 1w    4     1.2 GiB
    """
    return GFSTierConfig(
        hourly_interval_minutes=60,
        hourly_count=24,
        daily_interval_minutes=1440,  # 24h
        daily_count=7,
        weekly_interval_minutes=10080,  # 7d
        weekly_count=4,
    )


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def classify_tiers(
    snapshots: list[BackupSnapshot],
    *,
    now: float | None = None,
    config: GFSTierConfig | None = None,
) -> list[BackupSnapshot]:
    """Assign GFS tier labels to a list of backup snapshots.

    Returns **new** ``BackupSnapshot`` objects (input is never mutated).
    The result list has the same length and ordering as the input.

    Args:
        snapshots: List of backup snapshots (``tier`` field is ignored).
        now:       Reference timestamp (Unix epoch). Defaults to
                   ``time.time()``.
        config:    Tier configuration. Defaults to ``default_gfs_config()``.

    Returns:
        List of new ``BackupSnapshot`` objects with ``tier`` populated.
    """
    if not snapshots:
        return []

    if now is None:
        now = time.time()
    if config is None:
        config = default_gfs_config()

    # Sort newest-first (descending mtime). Ties broken by filename
    # for deterministic ordering.
    sorted_snaps = sorted(
        snapshots,
        key=lambda s: (s.mtime, s.filename),
        reverse=True,
    )

    hourly_slots = config.hourly_count
    daily_slots = config.daily_count
    weekly_slots = config.weekly_count

    hourly_max_age = config.hourly_max_age_hours * 3600.0
    daily_max_age = config.daily_max_age_hours * 3600.0
    weekly_max_age = config.weekly_max_age_hours * 3600.0

    result: list[BackupSnapshot] = []
    for snap in sorted_snaps:
        age = now - snap.mtime

        if age < hourly_max_age and hourly_slots > 0:
            tier = "hourly"
            hourly_slots -= 1
        elif age < daily_max_age and daily_slots > 0:
            tier = "daily"
            daily_slots -= 1
        elif age < weekly_max_age and weekly_slots > 0:
            tier = "weekly"
            weekly_slots -= 1
        else:
            tier = "prune"

        result.append(replace(snap, tier=tier))

    # Restore original input ordering (by filename, then mtime).
    original_order = {
        (s.filename, s.mtime): i for i, s in enumerate(snapshots)
    }
    result.sort(key=lambda s: original_order.get((s.filename, s.mtime), 0))

    return result


# ---------------------------------------------------------------------------
# Retention plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetentionPlan:
    """Outcome of the retention engine: which snapshots to keep vs. delete.

    Attributes:
        keep:  Snapshots assigned to hourly/daily/weekly tiers.
        prune: Snapshots marked for deletion.
    """

    keep: list[BackupSnapshot]
    prune: list[BackupSnapshot]


def compute_retention_plan(
    snapshots: list[BackupSnapshot],
    *,
    now: float | None = None,
    config: GFSTierConfig | None = None,
) -> RetentionPlan:
    """Classify tiers and split into keep/prune lists.

    Convenience wrapper over ``classify_tiers`` that produces a
    ``RetentionPlan`` suitable for the backup scheduler's prune step
    (NFM-3024-C).
    """
    classified = classify_tiers(snapshots, now=now, config=config)

    keep = [s for s in classified if s.tier != "prune"]
    prune = [s for s in classified if s.tier == "prune"]

    return RetentionPlan(keep=keep, prune=prune)
