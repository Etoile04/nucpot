"""Conflict resolution with Last-Write-Wins and manual merge flagging.

Provides conflict detection via vector clock comparison (AC-3),
automatic resolution using Last-Write-Wins (AC-4), and manual merge
flagging for unresolvable conflicts (AC-5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nfm_node_client.vector_clock import ClockComparison, VectorClock


_LOGGER = logging.getLogger("nfm_node_client.conflict_resolver")


class ConflictType(str, Enum):
    """Type of sync conflict."""

    CONCURRENT_UPDATE = "concurrent_update"
    DELETED_MODIFIED = "deleted_modified"


class ResolutionStrategy(str, Enum):
    """How a conflict was (or should be) resolved."""

    USE_LOCAL = "use_local"
    USE_REMOTE = "use_remote"
    MANUAL_MERGE = "manual_merge"


@dataclass(frozen=True)
class ConflictRecord:
    """Immutable record of a detected conflict between local and remote data.

    Parameters
    ----------
    entity_id:
        Identifier of the conflicting entity.
    conflict_type:
        Category of conflict.
    local_clock:
        Vector clock of the local version.
    remote_clock:
        Vector clock of the remote version.
    local_data:
        Local entity data at time of conflict.
    remote_data:
        Remote entity data at time of conflict.
    resolution:
        Strategy used (or None if unresolved).
    merged_data:
        Result of manual merge (or None).
    """

    entity_id: str
    conflict_type: ConflictType
    local_clock: VectorClock
    remote_clock: VectorClock
    local_data: dict[str, Any]
    remote_data: dict[str, Any]
    resolution: ResolutionStrategy | None = None
    merged_data: dict[str, Any] | None = None

    @property
    def resolved(self) -> bool:
        """Whether this conflict has been resolved."""
        return self.resolution is not None

    def with_resolution(
        self,
        *,
        strategy: ResolutionStrategy,
        merged_data: dict[str, Any] | None = None,
    ) -> ConflictRecord:
        """Return a new ConflictRecord with the given resolution applied."""
        return ConflictRecord(
            entity_id=self.entity_id,
            conflict_type=self.conflict_type,
            local_clock=self.local_clock,
            remote_clock=self.remote_clock,
            local_data=self.local_data,
            remote_data=self.remote_data,
            resolution=strategy,
            merged_data=merged_data,
        )


@dataclass(frozen=True)
class ConflictResolution:
    """Result of an automatic LWW resolution."""

    entity_id: str
    strategy: ResolutionStrategy
    winner_data: dict[str, Any]
    local_clock: VectorClock
    remote_clock: VectorClock


class ConflictResolver:
    """Detects and resolves conflicts between local and remote records.

    Uses vector clock comparison for causal analysis and wall-clock
    timestamps for Last-Write-Wins resolution.
    """

    # ------------------------------------------------------------------
    # Detect conflicts (AC-3)
    # ------------------------------------------------------------------

    def detect(
        self,
        *,
        entity_id: str,
        local_clock: VectorClock,
        remote_clock: VectorClock,
        local_data: dict[str, Any],
        remote_data: dict[str, Any],
    ) -> list[ConflictRecord]:
        """Detect conflicts between a local and remote version.

        Returns a list of ConflictRecord (empty if no conflict).
        Only CONCURRENT clocks produce conflicts; BEFORE / AFTER
        relationships are handled automatically (remote or local wins).
        """
        comparison = local_clock.compare(remote_clock)

        if comparison != ClockComparison.CONCURRENT:
            return []

        conflict = ConflictRecord(
            entity_id=entity_id,
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=local_clock,
            remote_clock=remote_clock,
            local_data=local_data,
            remote_data=remote_data,
        )
        _LOGGER.info(
            "conflict detected for %s: concurrent modification",
            entity_id,
        )
        return [conflict]

    # ------------------------------------------------------------------
    # Last-Write-Wins resolution (AC-4)
    # ------------------------------------------------------------------

    def resolve_lww(
        self,
        *,
        entity_id: str,
        local_clock: VectorClock,
        remote_clock: VectorClock,
        local_data: dict[str, Any],
        remote_data: dict[str, Any],
    ) -> ConflictResolution | None:
        """Resolve a conflict using Last-Write-Wins.

        Returns None if the records are not in conflict (one dominates
        the other). Otherwise returns a ConflictResolution with the
        winning data based on timestamps.

        Timestamp tie-breaking: falls back to node_id comparison.
        """
        comparison = local_clock.compare(remote_clock)

        # No conflict — one side dominates
        if comparison == ClockComparison.BEFORE:
            return ConflictResolution(
                entity_id=entity_id,
                strategy=ResolutionStrategy.USE_REMOTE,
                winner_data=remote_data,
                local_clock=local_clock,
                remote_clock=remote_clock,
            )
        if comparison == ClockComparison.AFTER:
            return ConflictResolution(
                entity_id=entity_id,
                strategy=ResolutionStrategy.USE_LOCAL,
                winner_data=local_data,
                local_clock=local_clock,
                remote_clock=remote_clock,
            )

        # CONCURRENT — use timestamps for LWW
        local_ts = float(local_data.get("updated_at", local_clock.timestamp))
        remote_ts = float(remote_data.get("updated_at", remote_clock.timestamp))

        if remote_ts > local_ts:
            return ConflictResolution(
                entity_id=entity_id,
                strategy=ResolutionStrategy.USE_REMOTE,
                winner_data=remote_data,
                local_clock=local_clock,
                remote_clock=remote_clock,
            )
        if local_ts > remote_ts:
            return ConflictResolution(
                entity_id=entity_id,
                strategy=ResolutionStrategy.USE_LOCAL,
                winner_data=local_data,
                local_clock=local_clock,
                remote_clock=remote_clock,
            )

        # Equal timestamps — deterministic tie-break by node_id
        local_nid = local_clock.node_id or ""
        remote_nid = remote_clock.node_id or ""
        if local_nid >= remote_nid:
            strategy = ResolutionStrategy.USE_LOCAL
            winner = local_data
        else:
            strategy = ResolutionStrategy.USE_REMOTE
            winner = remote_data

        return ConflictResolution(
            entity_id=entity_id,
            strategy=strategy,
            winner_data=winner,
            local_clock=local_clock,
            remote_clock=remote_clock,
        )

    # ------------------------------------------------------------------
    # Manual merge flagging (AC-5)
    # ------------------------------------------------------------------

    def flag_manual_merge(
        self, conflicts: list[ConflictRecord],
    ) -> list[ConflictRecord]:
        """Filter to unresolved conflicts requiring manual merge.

        Already-resolved records are excluded.
        """
        unresolved = [c for c in conflicts if not c.resolved]
        if unresolved:
            _LOGGER.info(
                "%d conflict(s) flagged for manual merge: %s",
                len(unresolved),
                [c.entity_id for c in unresolved],
            )
        return unresolved


__all__ = [
    "ConflictRecord",
    "ConflictResolution",
    "ConflictResolver",
    "ConflictType",
    "ResolutionStrategy",
]
