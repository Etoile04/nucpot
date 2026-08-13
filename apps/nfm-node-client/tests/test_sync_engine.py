"""Tests for nfm_node_client.sync_engine — Cross-node sync engine."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nfm_node_client.conflict_resolver import (
    ConflictRecord,
    ConflictType,
    ResolutionStrategy,
)
from nfm_node_client.offline_queue import OfflineQueue, OperationType, PendingOperation
from nfm_node_client.sync_engine import (
    SyncEngine,
    SyncEngineResult,
    SyncPhase,
)
from nfm_node_client.vector_clock import VectorClock


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_NODE_ID = "resource-node-1"
_HUB_URL = "https://hub.example.test"


def _vc(node: str, counter: int, timestamp: float = 0.0) -> VectorClock:
    """Create a VectorClock with a single node entry."""
    return VectorClock(node_id=node, clocks={node: counter}, timestamp=timestamp)


def _remote_record(
    entity_id: str = "rec-1",
    timestamp: float = 20.0,
    clock_counter: int = 1,
) -> dict[str, Any]:
    """Create a remote record dict as returned by the hub."""
    return {
        "entity_id": entity_id,
        "data": f"remote-{entity_id}",
        "updated_at": timestamp,
        "vector_clock": {"node_id": "hub", "clocks": {f"hub": [clock_counter]}, "timestamp": timestamp},
    }


def _local_record(
    entity_id: str = "rec-1",
    timestamp: float = 10.0,
    clock_counter: int = 1,
) -> dict[str, Any]:
    """Create a local record dict."""
    return {
        "entity_id": entity_id,
        "data": f"local-{entity_id}",
        "updated_at": timestamp,
    }


def _make_queue(tmp_path: str) -> OfflineQueue:
    """Create an OfflineQueue backed by a temp SQLite DB."""
    return OfflineQueue(db_path=f"{tmp_path}/test_queue.db")


# ------------------------------------------------------------------
# Construction
# ------------------------------------------------------------------

class TestSyncEngineConstruction:
    """Tests for SyncEngine creation."""

    def test_create_with_queue(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)
        assert engine.node_id == _NODE_ID
        assert engine.hub_url == _HUB_URL
        assert engine.watermark == 0
        engine.close()
        queue.close()

    def test_create_with_watermark(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, watermark=100,
        )
        assert engine.watermark == 100
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Full sync (AC-1)
# ------------------------------------------------------------------

class TestFullSync:
    """Tests for full sync (pull all hub data to resource node)."""

    @pytest.mark.asyncio
    async def test_full_sync_pulls_all_records(self, tmp_path: str) -> None:
        """Full sync pulls all records from the hub and stores them."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)

        records = [
            _remote_record("rec-1", timestamp=10.0, clock_counter=1),
            _remote_record("rec-2", timestamp=20.0, clock_counter=2),
            _remote_record("rec-3", timestamp=30.0, clock_counter=3),
        ]

        async def mock_fetch_all() -> list[dict[str, Any]]:
            return records

        engine._fetch_all_records = mock_fetch_all  # type: ignore[assignment]
        result = await engine.full_sync()

        assert result.phase == SyncPhase.FULL
        assert result.pulled == 3
        assert result.pushed == 0
        assert result.conflicts == []
        # Watermark should be updated to 3 (last record's counter)
        assert engine.watermark > 0
        engine.close()
        queue.close()

    @pytest.mark.asyncio
    async def test_full_sync_empty_hub(self, tmp_path: str) -> None:
        """Full sync with no hub records is a no-op success."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)

        async def mock_fetch_all() -> list[dict[str, Any]]:
            return []

        engine._fetch_all_records = mock_fetch_all  # type: ignore[assignment]
        result = await engine.full_sync()

        assert result.phase == SyncPhase.FULL
        assert result.pulled == 0
        assert result.success
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Incremental sync (AC-2)
# ------------------------------------------------------------------

class TestIncrementalSync:
    """Tests for incremental sync (push + pull since watermark)."""

    @pytest.mark.asyncio
    async def test_incremental_push_pull(self, tmp_path: str) -> None:
        """Incremental sync pushes local changes and pulls remote changes."""
        queue = _make_queue(tmp_path)
        # Enqueue some pending operations
        queue.enqueue(PendingOperation(
            op_type=OperationType.UPDATE,
            entity_type="material",
            entity_id="rec-1",
            payload={"data": "local-update"},
        ))
        queue.enqueue(PendingOperation(
            op_type=OperationType.CREATE,
            entity_type="material",
            entity_id="rec-2",
            payload={"data": "new-local"},
        ))

        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, watermark=5,
        )

        remote_changes = [_remote_record("rec-3", timestamp=50.0, clock_counter=6)]

        async def mock_fetch_incremental(since: int) -> list[dict[str, Any]]:
            assert since == 5
            return remote_changes

        async def mock_push() -> int:
            return 2  # pushed 2 local operations

        engine._fetch_incremental_records = mock_fetch_incremental  # type: ignore[assignment]
        engine._push_local_changes = mock_push  # type: ignore[assignment]
        result = await engine.incremental_sync()

        assert result.phase == SyncPhase.INCREMENTAL
        assert result.pushed == 2
        assert result.pulled == 1
        assert result.success
        engine.close()
        queue.close()

    @pytest.mark.asyncio
    async def test_incremental_no_changes(self, tmp_path: str) -> None:
        """Incremental sync with nothing to push or pull."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, watermark=10,
        )

        async def mock_fetch_incremental(since: int) -> list[dict[str, Any]]:
            return []

        async def mock_push() -> int:
            return 0

        engine._fetch_incremental_records = mock_fetch_incremental  # type: ignore[assignment]
        engine._push_local_changes = mock_push  # type: ignore[assignment]
        result = await engine.incremental_sync()

        assert result.pushed == 0
        assert result.pulled == 0
        assert result.success
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Conflict detection during sync (AC-3)
# ------------------------------------------------------------------

class TestSyncConflictDetection:
    """Tests for conflict detection during sync operations."""

    @pytest.mark.asyncio
    async def test_conflict_detected_during_incremental(self, tmp_path: str) -> None:
        """Incremental sync detects concurrent modifications."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, auto_resolve=False,
        )

        # Remote has a record that conflicts with local
        local_vc = _vc(_NODE_ID, 2, 10.0)
        remote_record = {
            "entity_id": "rec-1",
            "data": "remote-value",
            "updated_at": 20.0,
            "vector_clock": _vc("hub", 1, 20.0).to_dict(),
        }
        # Register local clock in engine's tracking
        engine._local_clocks = {"rec-1": local_vc}  # type: ignore[assignment]

        async def mock_fetch_incremental(since: int) -> list[dict[str, Any]]:
            return [remote_record]

        async def mock_push() -> int:
            return 0

        engine._fetch_incremental_records = mock_fetch_incremental  # type: ignore[assignment]
        engine._push_local_changes = mock_push  # type: ignore[assignment]
        result = await engine.incremental_sync()

        assert result.pulled == 0  # Conflict prevents auto-apply
        assert len(result.conflicts) == 1
        assert result.conflicts[0].entity_id == "rec-1"
        assert result.conflicts[0].conflict_type == ConflictType.CONCURRENT_UPDATE
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Last-Write-Wins during sync (AC-4)
# ------------------------------------------------------------------

class TestSyncLWWResolution:
    """Tests for automatic LWW resolution during sync."""

    @pytest.mark.asyncio
    async def test_lww_applied_for_resolvable_conflicts(self, tmp_path: str) -> None:
        """Concurrent conflicts are auto-resolved via LWW when timestamps differ."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, auto_resolve=True,
        )

        local_vc = _vc(_NODE_ID, 2, 10.0)
        remote_record = {
            "entity_id": "rec-1",
            "data": "remote-newer",
            "updated_at": 30.0,
            "vector_clock": _vc("hub", 1, 30.0).to_dict(),
        }
        engine._local_clocks = {"rec-1": local_vc}  # type: ignore[assignment]

        async def mock_fetch_incremental(since: int) -> list[dict[str, Any]]:
            return [remote_record]

        async def mock_push() -> int:
            return 0

        engine._fetch_incremental_records = mock_fetch_incremental  # type: ignore[assignment]
        engine._push_local_changes = mock_push  # type: ignore[assignment]
        result = await engine.incremental_sync()

        # Auto-resolved: remote wins (newer timestamp)
        assert len(result.conflicts) == 0
        assert result.pulled == 1
        assert result.resolved > 0
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Manual merge flagging (AC-5)
# ------------------------------------------------------------------

class TestSyncManualMerge:
    """Tests for manual merge flagging in sync results."""

    @pytest.mark.asyncio
    async def test_unresolvable_conflicts_flagged(self, tmp_path: str) -> None:
        """Conflicts with equal timestamps are flagged for manual merge."""
        queue = _make_queue(tmp_path)
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, auto_resolve=False,
        )

        local_vc = _vc(_NODE_ID, 2, 20.0)
        remote_record = {
            "entity_id": "rec-1",
            "data": "remote-value",
            "updated_at": 20.0,  # Same timestamp as local
            "vector_clock": _vc("hub", 1, 20.0).to_dict(),
        }
        engine._local_clocks = {"rec-1": local_vc}  # type: ignore[assignment]

        async def mock_fetch_incremental(since: int) -> list[dict[str, Any]]:
            return [remote_record]

        async def mock_push() -> int:
            return 0

        engine._fetch_incremental_records = mock_fetch_incremental  # type: ignore[assignment]
        engine._push_local_changes = mock_push  # type: ignore[assignment]
        result = await engine.incremental_sync()

        assert len(result.conflicts) == 1
        assert result.conflicts[0].resolved is False
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# Sync status (AC-6)
# ------------------------------------------------------------------

class TestSyncStatus:
    """Tests for sync status reporting."""

    def test_status_returns_accurate_progress(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        queue.enqueue(PendingOperation(
            op_type=OperationType.CREATE, entity_type="material", entity_id="rec-1",
        ))
        engine = SyncEngine(
            queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL, watermark=10,
        )
        status = engine.sync_status

        assert status["node_id"] == _NODE_ID
        assert status["hub_url"] == _HUB_URL
        assert status["watermark"] == 10
        assert status["pending_operations"] == 1
        assert status["phase"] == "idle"
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# SyncEngineResult
# ------------------------------------------------------------------

class TestSyncEngineResult:
    """Tests for the frozen SyncEngineResult dataclass."""

    def test_success_property(self) -> None:
        result = SyncEngineResult(
            phase=SyncPhase.FULL,
            pulled=5,
            pushed=0,
            conflicts=[],
            resolved=0,
            watermark_after=5,
        )
        assert result.success is True

    def test_not_success_with_conflicts(self) -> None:
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc("a", 1),
            remote_clock=_vc("b", 1),
            local_data={},
            remote_data={},
        )
        result = SyncEngineResult(
            phase=SyncPhase.INCREMENTAL,
            pulled=0,
            pushed=3,
            conflicts=[record],
            resolved=0,
            watermark_after=5,
        )
        assert result.success is False

    def test_success_with_auto_resolved(self) -> None:
        result = SyncEngineResult(
            phase=SyncPhase.INCREMENTAL,
            pulled=2,
            pushed=1,
            conflicts=[],
            resolved=1,
            watermark_after=10,
        )
        assert result.success is True


# ------------------------------------------------------------------
# Construction validation
# ------------------------------------------------------------------

class TestSyncEngineValidation:
    """Tests for SyncEngine input validation."""

    def test_missing_node_id(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        with pytest.raises(ValueError, match="node_id is required"):
            SyncEngine(queue=queue, node_id="", hub_url=_HUB_URL)
        queue.close()

    def test_missing_hub_url(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        with pytest.raises(ValueError, match="hub_url is required"):
            SyncEngine(queue=queue, node_id=_NODE_ID, hub_url="")
        queue.close()


# ------------------------------------------------------------------
# resolve_manual
# ------------------------------------------------------------------

class TestResolveManual:
    """Tests for manual merge resolution."""

    def test_resolve_manual_increments_clock(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)
        engine._local_clocks["rec-1"] = _vc(_NODE_ID, 2, 10.0)
        engine.resolve_manual("rec-1", {"data": "merged"})
        updated = engine._local_clocks["rec-1"]
        assert updated.clocks.get(_NODE_ID) == 3
        engine.close()
        queue.close()

    def test_resolve_manual_new_entity(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)
        # No local clock for this entity — should not error
        engine.resolve_manual("rec-new", {"data": "merged"})
        engine.close()
        queue.close()


# ------------------------------------------------------------------
# _parse_remote_clock / _extract_watermark
# ------------------------------------------------------------------

class TestInternalHelpers:
    """Tests for internal helper methods."""

    def test_parse_remote_clock_from_dict(self) -> None:
        vc_data = _vc("hub", 3, 50.0).to_dict()
        record = {"vector_clock": vc_data}
        result = SyncEngine._parse_remote_clock(record)
        assert result.node_id == "hub"
        assert result.clocks == {"hub": 3}

    def test_parse_remote_clock_missing(self) -> None:
        result = SyncEngine._parse_remote_clock({})
        assert result.clocks == {}

    def test_parse_remote_clock_vectorclock_obj(self) -> None:
        vc = _vc("hub", 1)
        result = SyncEngine._parse_remote_clock({"vector_clock": vc})
        assert result == vc

    def test_extract_watermark(self) -> None:
        record = {
            "vector_clock": {"node_id": "hub", "clocks": {"hub": [5]}, "timestamp": 10.0},
        }
        assert SyncEngine._extract_watermark(record) == 5

    def test_extract_watermark_multiple_nodes(self) -> None:
        record = {
            "vector_clock": {"clocks": {"hub": [3], "node-a": [7]}, "timestamp": 10.0},
        }
        assert SyncEngine._extract_watermark(record) == 7

    def test_extract_watermark_missing(self) -> None:
        assert SyncEngine._extract_watermark({}) == 0


# ------------------------------------------------------------------
# close idempotent
# ------------------------------------------------------------------

class TestLifecycle:
    """Tests for lifecycle methods."""

    def test_close_idempotent(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)
        engine.close()
        engine.close()  # Should not raise
        queue.close()

    def test_sync_status_after_close(self, tmp_path: str) -> None:
        queue = _make_queue(tmp_path)
        engine = SyncEngine(queue=queue, node_id=_NODE_ID, hub_url=_HUB_URL)
        engine.close()
        # sync_status still works after close
        status = engine.sync_status
        assert status["phase"] == "idle"
        queue.close()
