"""Immutable snapshot data model.

Each snapshot represents a single .sql.gz backup file with metadata
for tier classification and retention management.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from backup.tier import Tier


@dataclass(frozen=True)
class Snapshot:
    """A database backup snapshot.

    Attributes:
        snapshot_id: Unique identifier for the snapshot.
        timestamp: When the snapshot was created (UTC).
        tier: Current GFS retention tier.
        size_bytes: Size of the snapshot file in bytes.
        path: Filesystem path to the .sql.gz file.
    """

    snapshot_id: str
    timestamp: datetime
    tier: Tier
    size_bytes: int
    path: Path

    def age(self, now: datetime) -> timedelta:
        """Return the age of this snapshot relative to *now*."""
        return now - self.timestamp

    def with_tier(self, new_tier: Tier) -> Snapshot:
        """Return a copy of this snapshot with a different tier."""
        return replace(self, tier=new_tier)

    def __lt__(self, other: object) -> bool:
        """Order by timestamp (newer = smaller)."""
        if not isinstance(other, Snapshot):
            return NotImplemented
        return self.timestamp > other.timestamp
