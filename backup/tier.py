"""Tier enumeration and classification logic for GFS retention.

A snapshot's tier is determined by its age:
- Hourly:  age < 24h
- Daily:   24h <= age < 7d
- Weekly:  age >= 7d
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum


class Tier(StrEnum):
    """GFS retention tier."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


# Tier age boundaries
_HOURLY_THRESHOLD = timedelta(hours=24)
_DAILY_THRESHOLD = timedelta(days=7)


def classify_tier(snapshot_timestamp: datetime, now: datetime) -> Tier:
    """Classify a snapshot into a GFS tier based on its age.

    Args:
        snapshot_timestamp: When the snapshot was created.
        now: The reference time (typically ``datetime.utcnow()``).

    Returns:
        The appropriate ``Tier`` for the snapshot.
    """
    age = now - snapshot_timestamp
    if age < _HOURLY_THRESHOLD:
        return Tier.HOURLY
    if age < _DAILY_THRESHOLD:
        return Tier.DAILY
    return Tier.WEEKLY
