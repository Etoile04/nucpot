"""Backup capacity guardrails (NFM-3051 / NFM-3024-C).

Implements two capacity guardrails:

1. ``backup.maxTotalBytes`` (default 12 GiB) — pruner deletes oldest
   snapshots first when the cap is breached.
2. ``backup.minFreeBytes`` (default 20 GiB) — backup job **refuses to
   write** a new snapshot if writing it would drop free space below the
   floor.

Both checks MUST run AFTER any pruner pass (prune, then re-measure,
then accept or refuse).

Produces state that NFM-3024-D surfaces via
``GET /api/admin/backups/stats``:
``{ totalBytes, freeBytes, refusalCount, lastRefusalAt }``
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from typing import Protocol


# ---------------------------------------------------------------------------
# Snapshot tier enum (GFS retention: hourly -> daily -> weekly)
# ---------------------------------------------------------------------------


class SnapshotTier(enum.IntEnum):
    """Grandfather-Father-Son backup retention tiers.

    Lower value = lower retention priority = pruned first.
    """

    HOURLY = 0
    DAILY = 1
    WEEKLY = 2


# ---------------------------------------------------------------------------
# Write result -- observable refusal state (NFM-3024-D surfaces this)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """Result of a ``check_write_feasibility`` call.

    Immutable -- create a new instance for each check.
    """

    refused: bool
    refusal_count: int
    last_refusal_at: float | None = None

    @property
    def stats_dict(self) -> dict:
        """Shape consumed by the NFM-3024-D stats endpoint."""
        return {
            "refused": self.refused,
            "refusalCount": self.refusal_count,
            "lastRefusalAt": self.last_refusal_at,
        }


# ---------------------------------------------------------------------------
# Snapshot store protocol -- abstracts filesystem operations
# ---------------------------------------------------------------------------


class SnapshotStore(Protocol):
    """Interface for snapshot filesystem operations.

    Implementations may use real disk (production) or in-memory
    structures (tests).  The pruner and guard depend only on this
    protocol.
    """

    def list_snapshots(self) -> list[dict]:
        """Return all snapshots sorted by creation time (oldest first)."""
        ...

    def delete_snapshot(self, path: str) -> int:
        """Delete a snapshot by path. Returns freed bytes."""
        ...

    def total_snapshot_bytes(self) -> int:
        """Total bytes consumed by all snapshots."""
        ...

    def free_bytes(self) -> int:
        """Free bytes on the target disk / volume."""
        ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapacityConfig:
    """Capacity guardrail configuration.

    Immutable -- create a new instance to change values.
    """

    max_total_bytes: int = 12 * 1024 ** 3  # 12 GiB
    min_free_bytes: int = 20 * 1024 ** 3   # 20 GiB


# ---------------------------------------------------------------------------
# Tier precedence: lower tiers are pruned before higher tiers
# ---------------------------------------------------------------------------

_TIER_PRUNE_ORDER: list[SnapshotTier] = [
    SnapshotTier.HOURLY,
    SnapshotTier.DAILY,
    SnapshotTier.WEEKLY,
]


# ---------------------------------------------------------------------------
# Backup pruner -- deletes oldest-first to stay under maxTotalBytes
# ---------------------------------------------------------------------------


class BackupPruner:
    """Prunes backup snapshots to satisfy the ``maxTotalBytes`` cap.

    Deletion order:
    1. Within each tier, oldest first (by ``created_at``).
    2. Across tiers, hourly before daily before weekly.
    3. The newest snapshot in each tier is NEVER deleted (RPO).

    Returns the list of deleted paths (empty if under cap).
    """

    def __init__(self, store: SnapshotStore, config: CapacityConfig) -> None:
        self._store = store
        self._config = config

    def prune(self) -> list[str]:
        """Prune snapshots until total bytes is under ``max_total_bytes``.

        RPO-safe: deletes non-newest candidates per tier. If the cap
        cannot be met without deleting the newest snapshot in a tier,
        the violation is reported but RPO is preserved.
        2. If still over cap, delete newest per tier (RPO breach —
           last resort to satisfy the cap).

        Returns:
            List of deleted snapshot paths in deletion order.
        """
        total = self._store.total_snapshot_bytes()
        if total <= self._config.max_total_bytes:
            return []

        deleted: list[str] = []

        # Pass 1: RPO-safe — delete non-newest candidates per tier
        deleted.extend(self._prune_pass(protect_newest=True))
        return deleted

    def _prune_pass(self, *, protect_newest: bool) -> list[str]:
        """Single pruning pass across tiers.

        Args:
            protect_newest: If True, skip the newest snapshot in each
                tier (RPO-safe).  If False, all snapshots are candidates.
        """
        total = self._store.total_snapshot_bytes()
        if total <= self._config.max_total_bytes:
            return []

        deleted: list[str] = []
        snapshots = self._store.list_snapshots()

        # Group by tier
        tier_map: dict[SnapshotTier, list[dict]] = {
            tier: [] for tier in _TIER_PRUNE_ORDER
        }
        for snap in snapshots:
            tier = snap["tier"]
            if tier in tier_map:
                tier_map[tier].append(snap)

        for tier in _TIER_PRUNE_ORDER:
            tier_snaps = sorted(
                tier_map[tier], key=lambda s: s["created_at"]
            )

            if not tier_snaps:
                continue

            candidates = (
                tier_snaps[:-1] if protect_newest else tier_snaps
            )

            for snap in candidates:
                if total <= self._config.max_total_bytes:
                    break
                freed = self._store.delete_snapshot(snap["path"])
                total -= freed
                deleted.append(snap["path"])

        return deleted


# ---------------------------------------------------------------------------
# Capacity guard -- checks minFreeBytes before accepting a write
# ---------------------------------------------------------------------------


class CapacityGuard:
    """Checks whether a new backup write is feasible given capacity
    constraints and refusal state.

    Thread-safe via a single-flight lock: concurrent
    ``check_write_feasibility`` calls serialize through a single
    check at a time.
    """

    def __init__(
        self,
        store: SnapshotStore,
        config: CapacityConfig,
    ) -> None:
        self._store = store
        self._config = config
        self._refusal_count: int = 0
        self._last_refusal_at: float | None = None
        self._lock = threading.Lock()

    @property
    def refusal_count(self) -> int:
        return self._refusal_count

    @property
    def last_refusal_at(self) -> float | None:
        return self._last_refusal_at

    def stats_dict(self) -> dict:
        """Full stats shape for NFM-3024-D endpoint.

        ``{ totalBytes, freeBytes, refusalCount, lastRefusalAt }``
        """
        return {
            "totalBytes": self._store.total_snapshot_bytes(),
            "freeBytes": self._store.free_bytes(),
            "refusalCount": self._refusal_count,
            "lastRefusalAt": self._last_refusal_at,
        }

    def check_write_feasibility(self, projected_bytes: int) -> WriteResult:
        """Check if writing ``projected_bytes`` would breach
        ``minFreeBytes``.

        Pre-write check:
        ``(currentFreeBytes - projectedSnapshotBytes) >= minFreeBytes``

        If false -> ``refused=True``, increment ``refusalCount``,
        set ``lastRefusalAt``, do NOT write.

        This method is serialized via a single-flight lock.
        """
        with self._lock:
            free = self._store.free_bytes()
            would_remain = free - projected_bytes

            if would_remain < self._config.min_free_bytes:
                self._refusal_count += 1
                self._last_refusal_at = time.time()
                return WriteResult(
                    refused=True,
                    refusal_count=self._refusal_count,
                    last_refusal_at=self._last_refusal_at,
                )

            return WriteResult(
                refused=False,
                refusal_count=self._refusal_count,
                last_refusal_at=self._last_refusal_at,
            )
