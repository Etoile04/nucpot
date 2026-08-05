"""Network partition simulation for E2E tests.

Provides a :class:`PartitionSimulator` that wraps a :class:`SyncEngine`
and controls its I/O hooks to simulate:
  * Normal connectivity (records flow freely)
  * Network partition (remote fetches fail or return stale data)
  * Recovery (connectivity restored, sync resumes)

The simulator is designed to be injected into a :class:`SyncEngine` via
its pluggable ``_fetch_all_records`` / ``_fetch_incremental_records``
/ ``_push_local_changes`` hooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nfm_node_client.sync_engine import SyncEngine


_LOGGER = logging.getLogger("e2e.partition")


class ConnectivityState(str, Enum):
    """Simulated network state for a node."""

    CONNECTED = "connected"
    PARTITIONED = "partitioned"
    RECOVERING = "recovering"


@dataclass(frozen=True)
class PartitionConfig:
    """Configuration for a single node's network simulation.

    Parameters
    ----------
    node_id:
        Identifier of the node being simulated.
    state:
        Current connectivity state.
    error_code:
        HTTP error code to return when partitioned (default 503).
    allowed_remote_records:
        Records the partitioned node can still see (subset).
    drop_push:
        If True, pushes are silently dropped during partition.
    """

    node_id: str
    state: ConnectivityState = ConnectivityState.CONNECTED
    error_code: int = 503
    allowed_remote_records: list[dict[str, Any]] = field(default_factory=list)
    drop_push: bool = False


class PartitionSimulator:
    """Simulates network conditions for a SyncEngine.

    Wrap a SyncEngine and override its I/O hooks to control data flow.
    The ``config`` attribute can be mutated between operations to
    transition between connected / partitioned / recovering states.

    Usage::

        sim = PartitionSimulator(engine=engine, config=PartitionConfig("node-1"))
        sim.inject()

        # Normal operation
        result = await engine.full_sync()

        # Simulate partition
        sim.partition()
        result = await engine.incremental_sync()  # Will fail / return empty

        # Recover
        sim.reconnect()
        result = await engine.incremental_sync()  # Syncs normally
    """

    def __init__(
        self,
        *,
        engine: SyncEngine,
        config: PartitionConfig,
        remote_records: list[dict[str, Any]] | None = None,
    ) -> None:
        if not engine.node_id:
            raise ValueError("engine must have a node_id")
        if not config.node_id:
            raise ValueError("config.node_id is required")

        self._engine = engine
        self.config = config
        self._remote_records: list[dict[str, Any]] = remote_records or []
        self._push_log: list[dict[str, Any]] = []
        self._injected = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def engine(self) -> SyncEngine:
        """The wrapped sync engine."""
        return self._engine

    @property
    def push_log(self) -> list[dict[str, Any]]:
        """Log of operations pushed during simulation."""
        return list(self._push_log)

    @property
    def injected(self) -> bool:
        """Whether hooks have been injected into the engine."""
        return self._injected

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def inject(self) -> None:
        """Override the engine's I/O hooks with partition-aware versions.

        Idempotent: calling inject() twice does nothing.
        """
        if self._injected:
            return

        engine = self._engine

        async def partitioned_fetch_all() -> list[dict[str, Any]]:
            return self._simulated_fetch(self._remote_records)

        async def partitioned_fetch_incremental(since: int) -> list[dict[str, Any]]:
            filtered = [
                r
                for r in self._remote_records
                if self._extract_watermark(r) > since
            ]
            return self._simulated_fetch(filtered)

        async def partitioned_push() -> int:
            return self._simulated_push()

        engine._fetch_all_records = partitioned_fetch_all  # noqa: SLF001
        engine._fetch_incremental_records = partitioned_fetch_incremental  # noqa: SLF001
        engine._push_local_changes = partitioned_push  # noqa: SLF001
        self._injected = True
        _LOGGER.info(
            "partition simulator injected for node %s (state=%s)",
            self.config.node_id,
            self.config.state.value,
        )

    def eject(self) -> None:
        """Remove injected hooks and restore the original engine methods.

        Note: restores to empty stubs (the engine's default). The original
        methods are not preserved.
        """
        if not self._injected:
            return

        engine = self._engine

        async def empty_fetch_all() -> list[dict[str, Any]]:
            return []

        async def empty_fetch_incremental(_since: int) -> list[dict[str, Any]]:
            return []

        async def empty_push() -> int:
            return 0

        engine._fetch_all_records = empty_fetch_all  # noqa: SLF001
        engine._fetch_incremental_records = empty_fetch_incremental  # noqa: SLF001
        engine._push_local_changes = empty_push  # noqa: SLF001
        self._injected = False
        _LOGGER.info("partition simulator ejected for node %s", self.config.node_id)

    # ------------------------------------------------------------------
    # Remote data management
    # ------------------------------------------------------------------

    def set_remote_records(self, records: list[dict[str, Any]]) -> None:
        """Set the remote records available to the simulated hub."""
        self._remote_records = list(records)

    def add_remote_record(self, record: dict[str, Any]) -> None:
        """Add a single remote record."""
        self._remote_records = [*self._remote_records, record]

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def partition(self, *, error_code: int = 503, drop_push: bool = True) -> None:
        """Transition to PARTITIONED state."""
        self.config = PartitionConfig(
            node_id=self.config.node_id,
            state=ConnectivityState.PARTITIONED,
            error_code=error_code,
            allowed_remote_records=self.config.allowed_remote_records,
            drop_push=drop_push,
        )
        _LOGGER.info("node %s → PARTITIONED", self.config.node_id)

    def reconnect(self) -> None:
        """Transition to CONNECTED state."""
        self.config = PartitionConfig(
            node_id=self.config.node_id,
            state=ConnectivityState.CONNECTED,
        )
        _LOGGER.info("node %s → CONNECTED", self.config.node_id)

    # ------------------------------------------------------------------
    # Simulated I/O
    # ------------------------------------------------------------------

    def _simulated_fetch(
        self, records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply partition logic to a fetch operation."""
        if self.config.state == ConnectivityState.PARTITIONED:
            _LOGGER.debug("fetch blocked by partition for %s", self.config.node_id)
            return self.config.allowed_remote_records
        return records

    def _simulated_push(self) -> int:
        """Apply partition logic to a push operation."""
        from nfm_node_client.offline_queue import PendingOperation

        count = 0
        while True:
            op: PendingOperation | None = self._engine._queue.dequeue()
            if op is None:
                break

            if self.config.state == ConnectivityState.PARTITIONED and self.config.drop_push:
                self._push_log.append({
                    "node_id": self.config.node_id,
                    "status": "dropped",
                    "op_type": op.op_type.value,
                    "entity_id": op.entity_id,
                })
                count += 1
                continue

            self._push_log.append({
                "node_id": self.config.node_id,
                "status": "pushed",
                "op_type": op.op_type.value,
                "entity_id": op.entity_id,
            })

            existing = self._engine._local_clocks.get(op.entity_id)
            if existing is not None:
                self._engine._local_clocks[op.entity_id] = existing.increment()
            else:
                from nfm_node_client.vector_clock import VectorClock

                self._engine._local_clocks[op.entity_id] = VectorClock(
                    node_id=self._engine.node_id,
                ).increment()
            count += 1
        return count

    @staticmethod
    def _extract_watermark(record_data: dict[str, Any]) -> int:
        """Extract watermark counter from a remote record."""
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


def partition_island(
    *,
    node_ids: list[str],
    remote_records: dict[str, list[dict[str, Any]]],
) -> dict[str, PartitionConfig]:
    """Create configs for a partitioned island subset.

    Nodes in ``node_ids`` can see each other's records but nothing
    from nodes outside the island.

    Returns a dict mapping node_id → PartitionConfig.
    """
    island_records_by_node: dict[str, list[dict[str, Any]]] = {
        nid: [] for nid in node_ids
    }
    for nid in node_ids:
        for record in remote_records.get(nid, []):
            source_node = record.get("source_node", "")
            if source_node in node_ids:
                island_records_by_node[nid].append(record)

    return {
        nid: PartitionConfig(
            node_id=nid,
            state=ConnectivityState.PARTITIONED,
            allowed_remote_records=island_records_by_node.get(nid, []),
            drop_push=True,
        )
        for nid in node_ids
    }


__all__ = [
    "ConnectivityState",
    "PartitionConfig",
    "PartitionSimulator",
    "partition_island",
]
