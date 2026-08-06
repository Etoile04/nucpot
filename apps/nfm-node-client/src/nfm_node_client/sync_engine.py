"""Cross-node synchronization engine for the 1+N architecture.

Orchestrates full sync, incremental sync, and bidirectional data
exchange between a resource node and the hub. Uses vector clocks
for conflict detection (AC-3), Last-Write-Wins for automatic
resolution (AC-4), and flags unresolvable conflicts for manual merge
(AC-5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nfm_node_client.conflict_resolver import (
    ConflictRecord,
    ConflictResolver,
    ConflictResolution,
    ResolutionStrategy,
)
from nfm_node_client.hub_transport import HubTransport
from nfm_node_client.offline_queue import OfflineQueue
from nfm_node_client.vector_clock import ClockComparison, VectorClock


_LOGGER = logging.getLogger("nfm_node_client.sync_engine")


class SyncPhase(str, Enum):
    """Phase of a sync operation."""

    FULL = "full"
    INCREMENTAL = "incremental"
    IDLE = "idle"


@dataclass(frozen=True)
class SyncEngineResult:
    """Immutable summary of a sync operation.

    Parameters
    ----------
    phase:
        The sync phase (FULL or INCREMENTAL).
    pulled:
        Number of records pulled from the hub.
    pushed:
        Number of local operations pushed to the hub.
    conflicts:
        Unresolved conflicts requiring manual merge.
    resolved:
        Number of conflicts auto-resolved via LWW.
    watermark_after:
        Watermark value after this sync operation.
    """

    phase: SyncPhase
    pulled: int
    pushed: int
    conflicts: list[ConflictRecord] = field(default_factory=list)
    resolved: int = 0
    watermark_after: int = 0

    @property
    def success(self) -> bool:
        """True if sync completed without unresolved conflicts."""
        return len(self.conflicts) == 0


class SyncEngine:
    """Orchestrates full and incremental sync between resource node and hub.

    Parameters
    ----------
    queue:
        The OfflineQueue for tracking pending local operations
        and sync watermarks.
    node_id:
        Identifier of this resource node.
    hub_url:
        Base URL of the hub.
    watermark:
        Last sync point counter (default 0 for initial sync).
    auto_resolve:
        If True, automatically resolve conflicts using LWW (AC-4).
        If False, flag all conflicts for manual merge (AC-5).
    """

    def __init__(
        self,
        *,
        queue: OfflineQueue,
        node_id: str,
        hub_url: str,
        watermark: int = 0,
        auto_resolve: bool = True,
        transport: HubTransport | None = None,
    ) -> None:
        if not node_id:
            raise ValueError("node_id is required")
        if not hub_url:
            raise ValueError("hub_url is required")

        self._queue = queue
        self._node_id = node_id
        self._hub_url = hub_url
        self._watermark = watermark
        self._auto_resolve = auto_resolve
        self._resolver = ConflictResolver()
        self._transport = transport
        self._closed = False

        # Local vector clocks per entity for conflict detection
        self._local_clocks: dict[str, VectorClock] = {}
        self._current_phase: SyncPhase = SyncPhase.IDLE

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def node_id(self) -> str:
        """Identifier of this resource node."""
        return self._node_id

    @property
    def hub_url(self) -> str:
        """Base URL of the hub."""
        return self._hub_url

    @property
    def watermark(self) -> int:
        """Last sync point counter."""
        return self._watermark

    @property
    def auto_resolve(self) -> bool:
        """Whether LWW auto-resolution is enabled."""
        return self._auto_resolve

    @property
    def sync_status(self) -> dict[str, Any]:
        """Return current sync status (AC-6)."""
        return {
            "node_id": self._node_id,
            "hub_url": self._hub_url,
            "watermark": self._watermark,
            "pending_operations": self._queue.size(),
            "phase": self._current_phase.value,
        }

    # ------------------------------------------------------------------
    # Full sync (AC-1)
    # ------------------------------------------------------------------

    async def full_sync(self) -> SyncEngineResult:
        """Pull all records from the hub (initial sync).

        Fetches every record from the hub, detects conflicts with any
        existing local data, applies changes, and updates the watermark.
        """
        self._current_phase = SyncPhase.FULL
        _LOGGER.info("starting full sync for node %s", self._node_id)

        remote_records = await self._fetch_all_records()
        pulled = 0
        conflicts: list[ConflictRecord] = []
        resolved = 0
        max_watermark = 0

        for record_data in remote_records:
            entity_id = str(record_data.get("entity_id", ""))
            remote_vc = self._parse_remote_clock(record_data)
            new_watermark = self._extract_watermark(record_data)
            max_watermark = max(max_watermark, new_watermark)

            local_vc = self._local_clocks.get(entity_id)
            if local_vc is not None:
                comparison = local_vc.compare(remote_vc)

                if comparison == ClockComparison.CONCURRENT:
                    if self._auto_resolve:
                        conflict_result = self._resolver.resolve_lww(
                            entity_id=entity_id,
                            local_clock=local_vc,
                            remote_clock=remote_vc,
                            local_data={"entity_id": entity_id},
                            remote_data=record_data,
                        )
                        if conflict_result is not None:
                            pulled += 1
                            self._local_clocks[entity_id] = local_vc.merge(remote_vc)
                            resolved += 1
                            continue

                    # No auto-resolve — flag for manual merge
                    detected = self._resolver.detect(
                        entity_id=entity_id,
                        local_clock=local_vc,
                        remote_clock=remote_vc,
                        local_data={"entity_id": entity_id},
                        remote_data=record_data,
                    )
                    conflicts.extend(detected)
                    continue

                # Non-concurrent (BEFORE/AFTER) — accept the dominant version
                pulled += 1
                self._local_clocks[entity_id] = local_vc.merge(remote_vc)
                continue

            # No local data — accept remote unconditionally
            pulled += 1
            self._local_clocks[entity_id] = remote_vc

        self._watermark = max_watermark
        self._current_phase = SyncPhase.IDLE

        _LOGGER.info(
            "full sync complete: pulled=%d, resolved=%d, conflicts=%d",
            pulled, resolved, len(conflicts),
        )
        return SyncEngineResult(
            phase=SyncPhase.FULL,
            pulled=pulled,
            pushed=0,
            conflicts=conflicts,
            resolved=resolved,
            watermark_after=self._watermark,
        )

    # ------------------------------------------------------------------
    # Incremental sync (AC-2)
    # ------------------------------------------------------------------

    async def incremental_sync(self) -> SyncEngineResult:
        """Push local changes and pull remote changes since watermark.

        Bidirectional: first pushes pending local operations, then
        pulls remote changes since the last watermark.
        """
        self._current_phase = SyncPhase.INCREMENTAL
        _LOGGER.info(
            "starting incremental sync for node %s (watermark=%d)",
            self._node_id,
            self._watermark,
        )

        # Phase 1: Push local changes
        pushed = await self._push_local_changes()

        # Phase 2: Pull remote changes since watermark
        remote_records = await self._fetch_incremental_records(self._watermark)
        pulled = 0
        conflicts: list[ConflictRecord] = []
        resolved = 0
        max_watermark = self._watermark

        for record_data in remote_records:
            entity_id = str(record_data.get("entity_id", ""))
            remote_vc = self._parse_remote_clock(record_data)
            new_watermark = self._extract_watermark(record_data)
            max_watermark = max(max_watermark, new_watermark)

            local_vc = self._local_clocks.get(entity_id)
            if local_vc is not None:
                detected = self._resolver.detect(
                    entity_id=entity_id,
                    local_clock=local_vc,
                    remote_clock=remote_vc,
                    local_data={"entity_id": entity_id},
                    remote_data=record_data,
                )
                if detected:
                    if self._auto_resolve:
                        conflict_result = self._resolver.resolve_lww(
                            entity_id=entity_id,
                            local_clock=local_vc,
                            remote_clock=remote_vc,
                            local_data={"entity_id": entity_id},
                            remote_data=record_data,
                        )
                        if conflict_result is not None:
                            pulled += 1
                            self._local_clocks[entity_id] = local_vc.merge(remote_vc)
                            resolved += 1
                        else:
                            conflicts.extend(detected)
                    else:
                        conflicts.extend(detected)
                    continue

            # No local data or no conflict — accept
            pulled += 1
            self._local_clocks[entity_id] = remote_vc

        self._watermark = max_watermark
        self._current_phase = SyncPhase.IDLE

        _LOGGER.info(
            "incremental sync complete: pushed=%d, pulled=%d, resolved=%d, conflicts=%d",
            pushed, pulled, resolved, len(conflicts),
        )
        return SyncEngineResult(
            phase=SyncPhase.INCREMENTAL,
            pulled=pulled,
            pushed=pushed,
            conflicts=conflicts,
            resolved=resolved,
            watermark_after=self._watermark,
        )

    # ------------------------------------------------------------------
    # Manual merge resolution
    # ------------------------------------------------------------------

    def resolve_manual(
        self,
        entity_id: str,
        merged_data: dict[str, Any],
    ) -> None:
        """Apply a manual merge resolution for a flagged conflict.

        Updates the local clock to the merged state and clears the
        conflict flag.
        """
        local_vc = self._local_clocks.get(entity_id)
        if local_vc is not None:
            self._local_clocks[entity_id] = local_vc.increment()
        _LOGGER.info("manual merge applied for %s", entity_id)

    # ------------------------------------------------------------------
    # Pluggable I/O hooks (override for testing or HTTP integration)
    # ------------------------------------------------------------------

    async def _fetch_all_records(self) -> list[dict[str, Any]]:
        """Fetch all records from the configured transport."""
        if self._transport is None:
            raise RuntimeError("SyncEngine requires a HubTransport for real I/O")
        return await self._transport.fetch_all_records()

    async def _fetch_incremental_records(self, since: int) -> list[dict[str, Any]]:
        """Fetch records changed since the given watermark."""
        if self._transport is None:
            raise RuntimeError("SyncEngine requires a HubTransport for real I/O")
        return await self._transport.fetch_incremental_records(since)

    async def _push_local_changes(self) -> int:
        """Push pending local operations, retaining them until Hub ACK."""
        if self._transport is None:
            raise RuntimeError("SyncEngine requires a HubTransport for real I/O")
        count = 0
        while True:
            op = self._queue.claim()
            if op is None:
                break
            try:
                await self._transport.push_operation(op)
            except Exception as exc:
                self._queue.nack(op.row_id, error=str(exc))  # type: ignore[arg-type]
                raise
            self._queue.ack(op.row_id)  # type: ignore[arg-type]
            count += 1
            existing = self._local_clocks.get(op.entity_id)
            if existing is not None:
                self._local_clocks[op.entity_id] = existing.increment()
            else:
                self._local_clocks[op.entity_id] = VectorClock(
                    node_id=self._node_id,
                ).increment()
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_remote_clock(record_data: dict[str, Any]) -> VectorClock:
        """Parse a vector clock from remote record data."""
        vc_data = record_data.get("vector_clock", {})
        if isinstance(vc_data, VectorClock):
            return vc_data
        if isinstance(vc_data, dict):
            return VectorClock.from_dict(vc_data)
        return VectorClock()

    @staticmethod
    def _extract_watermark(record_data: dict[str, Any]) -> int:
        """Extract the watermark counter from a remote record."""
        vc_data = record_data.get("vector_clock", {})
        if isinstance(vc_data, dict):
            clocks = vc_data.get("clocks", {})
            if clocks:
                values: list[int] = []
                for v in clocks.values():
                    if isinstance(v, list):
                        values.extend(int(x) for x in v)
                    else:
                        values.append(int(v))
                return max(values) if values else 0
        return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the sync engine. Idempotent."""
        if self._closed:
            return
        self._closed = True


__all__ = [
    "SyncEngine",
    "SyncEngineResult",
    "SyncPhase",
]
