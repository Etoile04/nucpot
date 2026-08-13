"""Tiered pruner for GFS retention.

Enforces per-tier count limits (hourly: 24, daily: 7, weekly: 4).
Deletes oldest-first within each tier, but never deletes the sole
representative of a time window.

Window grouping:
- Hourly: no sub-buckets — flat oldest-first trim within the tier.
- Daily: snapshots grouped by calendar day; one protected per day.
- Weekly: snapshots grouped by calendar week; one protected per week.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from backup.snapshot import Snapshot
from backup.tier import Tier


@dataclass(frozen=True)
class PruneDecision:
    """A decision about whether to keep or delete a snapshot.

    Attributes:
        snapshot: The snapshot in question.
        action: "keep" or "delete".
        reason: Human-readable justification.
    """

    snapshot: Snapshot
    action: str
    reason: str


def _day_bucket(ts: datetime) -> str:
    """Group snapshots by calendar day (YYYY-MM-DD)."""
    return ts.strftime("%Y-%m-%d")


def _week_bucket(ts: datetime) -> str:
    """Group snapshots by ISO week (YYYY-WNN)."""
    return ts.strftime("%Y-W%W")


class TieredPruner:
    """Prunes snapshots to stay within tier-specific count limits.

    Args:
        max_hourly: Maximum hourly snapshots to retain.
        max_daily: Maximum daily snapshots to retain.
        max_weekly: Maximum weekly snapshots to retain.
    """

    def __init__(
        self,
        max_hourly: int = 24,
        max_daily: int = 7,
        max_weekly: int = 4,
    ) -> None:
        self.max_hourly = max_hourly
        self.max_daily = max_daily
        self.max_weekly = max_weekly

    def _limit_for_tier(self, tier: Tier) -> int:
        limits = {
            Tier.HOURLY: self.max_hourly,
            Tier.DAILY: self.max_daily,
            Tier.WEEKLY: self.max_weekly,
        }
        return limits[tier]

    def _bucket_key(self, ts: datetime, tier: Tier) -> str | None:
        """Return the time-window bucket key for a tier.

        Hourly tier has no sub-buckets (flat list).
        Daily buckets by calendar day.
        Weekly buckets by calendar week.
        """
        if tier == Tier.HOURLY:
            return None  # no sub-bucketing
        if tier == Tier.WEEKLY:
            return _week_bucket(ts)
        return _day_bucket(ts)

    def _prune_tier(
        self,
        tier_snaps: list[Snapshot],
        tier: Tier,
    ) -> list[PruneDecision]:
        """Prune a single tier, respecting window representative protection."""
        limit = self._limit_for_tier(tier)
        decisions: list[PruneDecision] = []

        if len(tier_snaps) <= limit:
            return [
                PruneDecision(s, "keep", "within tier limit")
                for s in tier_snaps
            ]

        # Hourly: flat oldest-first, no window protection
        if self._bucket_key(tier_snaps[0].timestamp, tier) is None:
            excess = len(tier_snaps) - limit
            # tier_snaps are sorted newest-first (reverse timestamp);
            # oldest are at the tail, so delete from there.
            hourly_delete_ids: set[str] = set()
            for snap in tier_snaps[-excess:]:
                hourly_delete_ids.add(snap.snapshot_id)
            for snap in tier_snaps:
                if snap.snapshot_id in hourly_delete_ids:
                    decisions.append(
                        PruneDecision(
                            snap, "delete", "exceeds tier limit, oldest first"
                        )
                    )
                else:
                    decisions.append(
                        PruneDecision(snap, "keep", "within tier limit")
                    )
            return decisions

        # Daily/Weekly: group by time window, protect sole representatives
        bucket_fn = _week_bucket if tier == Tier.WEEKLY else _day_bucket
        windows: dict[str, list[Snapshot]] = defaultdict(list)
        for snap in tier_snaps:
            windows[bucket_fn(snap.timestamp)].append(snap)

        sorted_windows = sorted(
            windows.items(), key=lambda kv: min(kv[1]).timestamp
        )

        protected: set[str] = set()
        deletable: list[Snapshot] = []

        for _key, snaps_in_window in sorted_windows:
            if len(snaps_in_window) == 1:
                protected.add(snaps_in_window[0].snapshot_id)
            else:
                # Keep newest in window, mark rest as deletable
                sorted_snaps = sorted(
                    snaps_in_window,
                    key=lambda s: s.timestamp,
                    reverse=True,
                )
                for snap in sorted_snaps[:-1]:
                    deletable.append(snap)

        excess = len(tier_snaps) - limit
        to_delete_ids: set[str] = set()
        for snap in deletable:
            if snap.snapshot_id not in protected and len(to_delete_ids) < excess:
                to_delete_ids.add(snap.snapshot_id)

        # Fallback: if all windows are singletons and deletable didn't
        # cover the full excess, delete from oldest windows first.
        if len(to_delete_ids) < excess:
            remaining_excess = excess - len(to_delete_ids)
            oldest_first = [
                snap
                for _key, snaps_in_window in sorted_windows
                for snap in snaps_in_window
                if snap.snapshot_id not in to_delete_ids
            ]
            for snap in oldest_first[:remaining_excess]:
                to_delete_ids.add(snap.snapshot_id)

        for snap in tier_snaps:
            if snap.snapshot_id in to_delete_ids:
                decisions.append(
                    PruneDecision(
                        snap, "delete", "exceeds tier limit, oldest first"
                    )
                )
            else:
                decisions.append(
                    PruneDecision(snap, "keep", "within tier limit")
                )

        return decisions

    def prune(
        self,
        snapshots: list[Snapshot],
        now: datetime,
    ) -> list[PruneDecision]:
        """Evaluate all snapshots and return keep/delete decisions.

        Processes each tier independently, enforcing count limits.

        Args:
            snapshots: All snapshots to evaluate.
            now: The reference time for age calculation.

        Returns:
            A list of ``PruneDecision`` for every input snapshot.
        """
        by_tier: dict[Tier, list[Snapshot]] = defaultdict(list)
        for snap in snapshots:
            by_tier[snap.tier].append(snap)

        all_decisions: list[PruneDecision] = []
        for tier in (Tier.HOURLY, Tier.DAILY, Tier.WEEKLY):
            tier_snaps = sorted(
                by_tier[tier], key=lambda s: s.timestamp, reverse=True
            )
            all_decisions.extend(self._prune_tier(tier_snaps, tier))

        return all_decisions
