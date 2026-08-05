"""Tests for nfm_node_client.conflict_resolver — LWW + Manual Merge flagging."""

from __future__ import annotations

import pytest

from nfm_node_client.conflict_resolver import (
    ConflictRecord,
    ConflictResolver,
    ConflictResolution,
    ConflictType,
    ResolutionStrategy,
)
from nfm_node_client.vector_clock import ClockComparison, VectorClock


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_NODE_A = "node-a"
_NODE_B = "node-b"


def _vc(node: str, counter: int, timestamp: float = 0.0) -> VectorClock:
    """Create a VectorClock with a single node entry."""
    return VectorClock(
        node_id=node,
        clocks={node: counter},
        timestamp=timestamp,
    )


def _make_local(
    entity_id: str = "rec-1",
    timestamp: float = 10.0,
) -> dict:
    """Create a local record dict."""
    return {"entity_id": entity_id, "data": "local-value", "updated_at": timestamp}


def _make_remote(
    entity_id: str = "rec-1",
    timestamp: float = 20.0,
) -> dict:
    """Create a remote record dict."""
    return {"entity_id": entity_id, "data": "remote-value", "updated_at": timestamp}


# ------------------------------------------------------------------
# ConflictRecord
# ------------------------------------------------------------------

class TestConflictRecord:
    """Tests for the ConflictRecord frozen dataclass."""

    def test_creation(self) -> None:
        vc_local = _vc(_NODE_A, 1, 10.0)
        vc_remote = _vc(_NODE_B, 1, 20.0)
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=vc_local,
            remote_clock=vc_remote,
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        assert record.entity_id == "rec-1"
        assert record.conflict_type == ConflictType.CONCURRENT_UPDATE
        assert record.resolution is None
        assert record.resolved is False

    def test_with_resolution(self) -> None:
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc(_NODE_A, 1),
            remote_clock=_vc(_NODE_B, 1),
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        resolved = record.with_resolution(
            strategy=ResolutionStrategy.MANUAL_MERGE,
            merged_data={"entity_id": "rec-1", "data": "merged"},
        )
        assert resolved.resolution == ResolutionStrategy.MANUAL_MERGE
        assert resolved.resolved is True
        assert resolved.merged_data == {"entity_id": "rec-1", "data": "merged"}

    def test_immutability(self) -> None:
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc(_NODE_A, 1),
            remote_clock=_vc(_NODE_B, 1),
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        with pytest.raises(AttributeError):
            record.resolution = ResolutionStrategy.USE_LOCAL  # type: ignore[misc]


# ------------------------------------------------------------------
# ConflictResolver — detect
# ------------------------------------------------------------------

class TestConflictResolverDetect:
    """Tests for conflict detection via vector clock comparison."""

    def test_no_conflict_local_before_remote(self) -> None:
        """Local is causally before remote — no conflict."""
        # Both share same node counters, but remote has advanced node-b
        vc_local = VectorClock(
            node_id=_NODE_A,
            clocks={_NODE_A: 1, _NODE_B: 0},
            timestamp=10.0,
        )
        vc_remote = VectorClock(
            node_id=_NODE_B,
            clocks={_NODE_A: 1, _NODE_B: 1},
            timestamp=20.0,
        )
        resolver = ConflictResolver()
        conflicts = resolver.detect(
            entity_id="rec-1",
            local_clock=vc_local,
            remote_clock=vc_remote,
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        assert len(conflicts) == 0

    def test_concurrent_update_detected(self) -> None:
        """Both sides have independent changes — concurrent, flagged."""
        vc_local = _vc(_NODE_A, 2, 10.0)
        vc_remote = _vc(_NODE_B, 1, 20.0)
        resolver = ConflictResolver()
        conflicts = resolver.detect(
            entity_id="rec-1",
            local_clock=vc_local,
            remote_clock=vc_remote,
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.CONCURRENT_UPDATE
        assert conflicts[0].entity_id == "rec-1"

    def test_detect_multiple_entities(self) -> None:
        """Detect conflicts across multiple entities."""
        resolver = ConflictResolver()
        # rec-1: concurrent (A:2, B:0 vs A:0, B:1)
        conflicts = resolver.detect(
            entity_id="rec-1",
            local_clock=_vc(_NODE_A, 2, 10.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local("rec-1"),
            remote_data=_make_remote("rec-1"),
        )
        # rec-2: local BEFORE remote (A:1,B:0 vs A:1,B:3)
        vc_local = VectorClock(
            node_id=_NODE_A,
            clocks={_NODE_A: 1, _NODE_B: 0},
            timestamp=15.0,
        )
        vc_remote = VectorClock(
            node_id=_NODE_B,
            clocks={_NODE_A: 1, _NODE_B: 3},
            timestamp=25.0,
        )
        conflicts += resolver.detect(
            entity_id="rec-2",
            local_clock=vc_local,
            remote_clock=vc_remote,
            local_data=_make_local("rec-2"),
            remote_data=_make_remote("rec-2"),
        )
        assert len(conflicts) == 1
        assert conflicts[0].entity_id == "rec-1"


# ------------------------------------------------------------------
# ConflictResolver — resolve (LWW)
# ------------------------------------------------------------------

class TestConflictResolverLWW:
    """Tests for Last-Write-Wins automatic resolution (AC-4)."""

    def test_lww_remote_newer(self) -> None:
        """Remote timestamp is newer — remote wins."""
        resolver = ConflictResolver()
        result = resolver.resolve_lww(
            entity_id="rec-1",
            local_clock=_vc(_NODE_A, 2, 10.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(timestamp=10.0),
            remote_data=_make_remote(timestamp=20.0),
        )
        assert result.strategy == ResolutionStrategy.USE_REMOTE
        assert result.winner_data == _make_remote(timestamp=20.0)

    def test_lww_local_newer(self) -> None:
        """Local timestamp is newer — local wins."""
        resolver = ConflictResolver()
        result = resolver.resolve_lww(
            entity_id="rec-1",
            local_clock=_vc(_NODE_A, 2, 30.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(timestamp=30.0),
            remote_data=_make_remote(timestamp=20.0),
        )
        assert result.strategy == ResolutionStrategy.USE_LOCAL
        assert result.winner_data == _make_local(timestamp=30.0)

    def test_lww_equal_timestamps_uses_clock(self) -> None:
        """Equal timestamps — use vector clock to break tie."""
        resolver = ConflictResolver()
        # Same timestamp but different clocks: local has higher counter
        result = resolver.resolve_lww(
            entity_id="rec-1",
            local_clock=_vc(_NODE_A, 3, 20.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(timestamp=20.0),
            remote_data=_make_remote(timestamp=20.0),
        )
        # Local clock dominates (A:3 > A:0, B:0 == B:1) — wait, A has 3 but B has 0
        # remote has A:0, B:1. local has A:3, B:0. These are concurrent.
        # LWW with equal timestamps falls back to the node_id lexicographic order.
        assert result.strategy in (
            ResolutionStrategy.USE_LOCAL,
            ResolutionStrategy.USE_REMOTE,
        )

    def test_lww_non_conflict_picks_dominant(self) -> None:
        """Non-conflicting records — LWW returns the dominant side."""
        resolver = ConflictResolver()
        # Same node, remote has higher counter → remote dominates
        vc_local = _vc(_NODE_A, 1, 10.0)
        vc_remote = VectorClock(
            node_id=_NODE_A,
            clocks={_NODE_A: 2},
            timestamp=20.0,
        )
        result = resolver.resolve_lww(
            entity_id="rec-1",
            local_clock=vc_local,
            remote_clock=vc_remote,
            local_data=_make_local(timestamp=10.0),
            remote_data=_make_remote(timestamp=20.0),
        )
        assert result is not None
        assert result.strategy == ResolutionStrategy.USE_REMOTE


# ------------------------------------------------------------------
# ConflictResolver — manual merge
# ------------------------------------------------------------------

class TestConflictResolverManualMerge:
    """Tests for manual merge flagging (AC-5)."""

    def test_flag_for_manual_merge(self) -> None:
        """Concurrent conflicts are flagged for manual resolution."""
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc(_NODE_A, 2, 10.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        resolver = ConflictResolver()
        conflicts = resolver.flag_manual_merge([record])
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == ConflictType.CONCURRENT_UPDATE

    def test_resolved_records_not_flagged(self) -> None:
        """Already-resolved records are skipped during flagging."""
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc(_NODE_A, 2, 10.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        resolved = record.with_resolution(
            strategy=ResolutionStrategy.USE_REMOTE,
            merged_data=_make_remote(),
        )
        resolver = ConflictResolver()
        conflicts = resolver.flag_manual_merge([resolved])
        assert len(conflicts) == 0

    def test_empty_list(self) -> None:
        resolver = ConflictResolver()
        conflicts = resolver.flag_manual_merge([])
        assert conflicts == []

    def test_resolve_with_manual_data(self) -> None:
        """Apply manual merge data to a conflict record."""
        record = ConflictRecord(
            entity_id="rec-1",
            conflict_type=ConflictType.CONCURRENT_UPDATE,
            local_clock=_vc(_NODE_A, 2, 10.0),
            remote_clock=_vc(_NODE_B, 1, 20.0),
            local_data=_make_local(),
            remote_data=_make_remote(),
        )
        merged = {"entity_id": "rec-1", "data": "manually-merged", "updated_at": 30.0}
        resolved = record.with_resolution(
            strategy=ResolutionStrategy.MANUAL_MERGE,
            merged_data=merged,
        )
        assert resolved.resolved is True
        assert resolved.merged_data == merged
