"""Tier promotion logic for GFS retention.

When snapshots age past their current tier window, the best candidate
(closest to the day boundary — midnight UTC) is promoted to the next tier:
- Hourly -> Daily at 24h
- Daily -> Weekly at 7d

Snapshots crossing a tier boundary are grouped by calendar day. Within
each group, only the snapshot closest to midnight UTC is promoted; all
others remain in the current tier for separate pruning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from backup.snapshot import Snapshot
from backup.tier import _DAILY_THRESHOLD, _HOURLY_THRESHOLD, Tier


@dataclass(frozen=True)
class PromotionResult:
    """Outcome of a promotion pass.

    Attributes:
        promoted: Snapshots that were promoted to a higher tier.
        remaining: Snapshots that were not promoted.
    """

    promoted: tuple[Snapshot, ...] = ()
    remaining: tuple[Snapshot, ...] = ()


def _distance_to_midnight(ts: datetime) -> timedelta:
    """How far *ts* is from the nearest midnight UTC.

    Returns a timedelta in the range [0h, 12h].
    """
    since_midnight = timedelta(
        hours=ts.hour,
        minutes=ts.minute,
        seconds=ts.second,
        microseconds=ts.microsecond,
    )
    until_midnight = timedelta(days=1) - since_midnight
    return min(since_midnight, until_midnight)


def _target_tier(current_tier: Tier) -> Tier:
    """Return the next tier up from *current_tier*."""
    if current_tier == Tier.HOURLY:
        return Tier.DAILY
    return Tier.WEEKLY


def promote_snapshots(
    snapshots: list[Snapshot],
    now: datetime,
) -> PromotionResult:
    """Promote snapshots that have aged past their tier boundary.

    Snapshots crossing a tier boundary are grouped by calendar day.
    Within each group the snapshot closest to midnight UTC is promoted;
    all others in the group stay in ``remaining`` (the caller handles
    pruning separately).

    Args:
        snapshots: All snapshots to evaluate.
        now: The reference time for age calculation.

    Returns:
        A ``PromotionResult`` with promoted and remaining snapshots.
    """
    promoted: list[Snapshot] = []
    remaining: list[Snapshot] = []

    crossing: list[Snapshot] = []

    for snap in snapshots:
        age = now - snap.timestamp
        crosses = (
            (snap.tier == Tier.HOURLY and age >= _HOURLY_THRESHOLD)
            or (snap.tier == Tier.DAILY and age >= _DAILY_THRESHOLD)
        )
        if crosses:
            crossing.append(snap)
        else:
            remaining.append(snap)

    # Group crossing snapshots by calendar day.
    day_groups: dict[str, list[Snapshot]] = defaultdict(list)
    for snap in crossing:
        day_key = snap.timestamp.strftime("%Y-%m-%d")
        day_groups[day_key].append(snap)

    # For each day group, promote the one closest to midnight.
    for _day_key, group in day_groups.items():
        best = min(group, key=lambda s: _distance_to_midnight(s.timestamp))
        promoted.append(best.with_tier(_target_tier(best.tier)))
        for snap in group:
            if snap.snapshot_id != best.snapshot_id:
                remaining.append(snap)

    return PromotionResult(
        promoted=tuple(promoted),
        remaining=tuple(remaining),
    )
