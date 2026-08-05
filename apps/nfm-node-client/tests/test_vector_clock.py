"""Tests for nfm_node_client.vector_clock — Vector Clock implementation."""

from __future__ import annotations

import uuid

import pytest

from nfm_node_client.vector_clock import (
    ClockComparison,
    VectorClock,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_node_id(v: str = "1") -> str:
    """Create a stable node ID string for tests."""
    return f"node-{v}"


# ------------------------------------------------------------------
# Construction
# ------------------------------------------------------------------

class TestVectorClockConstruction:
    """Tests for VectorClock creation and basic properties."""

    def test_empty_clock(self) -> None:
        vc = VectorClock()
        assert vc.clocks == {}
        assert vc.timestamp == 0.0
        assert vc.node_id is None

    def test_clock_with_node_id(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(node_id=node)
        assert vc.node_id == node
        assert vc.clocks == {}

    def test_clock_from_dict(self) -> None:
        clocks = {_make_node_id("a"): 3, _make_node_id("b"): 5}
        vc = VectorClock(clocks=clocks, timestamp=100.0)
        assert vc.clocks == clocks
        assert vc.timestamp == 100.0

    def test_clock_immutable(self) -> None:
        """VectorClock is frozen — cannot mutate attributes."""
        vc = VectorClock()
        with pytest.raises(AttributeError):
            vc.clocks = {}  # type: ignore[misc]

    def test_clock_copy_with_increment(self) -> None:
        """increment returns a NEW VectorClock, leaving original unchanged."""
        node = _make_node_id("a")
        vc = VectorClock(node_id=node, timestamp=1.0)
        vc2 = vc.increment()
        assert vc.timestamp == 1.0
        assert vc2.timestamp > vc.timestamp
        assert vc2.clocks.get(node) == 1

    def test_clock_increment_with_explicit_time(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(node_id=node, timestamp=0.0)
        vc2 = vc.increment(timestamp=50.0)
        assert vc2.timestamp == 50.0
        assert vc2.clocks.get(node) == 1

    def test_increment_without_node_id(self) -> None:
        """Increment without node_id raises ValueError."""
        vc = VectorClock()
        with pytest.raises(ValueError, match="node_id is required"):
            vc.increment()

    def test_merge_empty_clocks(self) -> None:
        vc_a = VectorClock(node_id=_make_node_id("a"))
        vc_b = VectorClock(node_id=_make_node_id("b"))
        merged = vc_a.merge(vc_b)
        # Equal timestamps → self wins, so node_id comes from vc_a
        assert merged.node_id == _make_node_id("a")
        assert merged.timestamp == max(vc_a.timestamp, vc_b.timestamp)


# ------------------------------------------------------------------
# Comparison
# ------------------------------------------------------------------

class TestVectorClockComparison:
    """Tests for happens-before / concurrent / after comparisons."""

    def test_identical_clocks_are_after(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(node_id=node, clocks={node: 1}, timestamp=1.0)
        assert vc.compare(vc) == ClockComparison.AFTER

    def test_happens_before(self) -> None:
        """A happens-before B when A's counters are all <= B's and A < B."""
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(
            node_id=node_a,
            clocks={node_a: 1, node_b: 0},
            timestamp=1.0,
        )
        vc_b = VectorClock(
            node_id=node_b,
            clocks={node_a: 1, node_b: 1},
            timestamp=2.0,
        )
        assert vc_a.compare(vc_b) == ClockComparison.BEFORE
        assert vc_b.compare(vc_a) == ClockComparison.AFTER

    def test_concurrent_clocks(self) -> None:
        """A and B are concurrent when neither dominates the other."""
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(
            node_id=node_a,
            clocks={node_a: 2, node_b: 0},
            timestamp=1.0,
        )
        vc_b = VectorClock(
            node_id=node_b,
            clocks={node_a: 0, node_b: 1},
            timestamp=2.0,
        )
        assert vc_a.compare(vc_b) == ClockComparison.CONCURRENT
        assert vc_b.compare(vc_a) == ClockComparison.CONCURRENT

    def test_single_node_before(self) -> None:
        node = _make_node_id("a")
        vc1 = VectorClock(node_id=node, clocks={node: 1}, timestamp=1.0)
        vc2 = VectorClock(node_id=node, clocks={node: 3}, timestamp=3.0)
        assert vc1.compare(vc2) == ClockComparison.BEFORE
        assert vc2.compare(vc1) == ClockComparison.AFTER

    def test_empty_vs_nonempty(self) -> None:
        """Empty clock (no increments) is before any clock with counters."""
        node = _make_node_id("a")
        vc_empty = VectorClock(node_id=node)
        vc_nonempty = VectorClock(node_id=node, clocks={node: 1}, timestamp=1.0)
        assert vc_empty.compare(vc_nonempty) == ClockComparison.BEFORE


# ------------------------------------------------------------------
# Merge
# ------------------------------------------------------------------

class TestVectorClockMerge:
    """Tests for merge (taking element-wise maximum)."""

    def test_merge_takes_max(self) -> None:
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(clocks={node_a: 3, node_b: 1}, timestamp=10.0)
        vc_b = VectorClock(clocks={node_a: 1, node_b: 5}, timestamp=20.0)
        merged = vc_a.merge(vc_b)
        assert merged.clocks == {node_a: 3, node_b: 5}
        assert merged.timestamp == 20.0

    def test_merge_is_idempotent(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(clocks={node: 5}, timestamp=5.0)
        assert vc.merge(vc).clocks == {node: 5}

    def test_merge_with_new_node(self) -> None:
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(clocks={node_a: 2}, timestamp=5.0)
        vc_b = VectorClock(clocks={node_b: 3}, timestamp=10.0)
        merged = vc_a.merge(vc_b)
        assert merged.clocks == {node_a: 2, node_b: 3}

    def test_merge_preserves_node_id_from_higher_timestamp(self) -> None:
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(node_id=node_a, clocks={node_a: 1}, timestamp=5.0)
        vc_b = VectorClock(node_id=node_b, clocks={node_b: 1}, timestamp=10.0)
        merged = vc_a.merge(vc_b)
        assert merged.node_id == node_b

    def test_merge_empty_with_nonempty(self) -> None:
        node = _make_node_id("a")
        vc_empty = VectorClock()
        vc_full = VectorClock(node_id=node, clocks={node: 1}, timestamp=1.0)
        merged = vc_empty.merge(vc_full)
        assert merged.clocks == {node: 1}


# ------------------------------------------------------------------
# Serialization
# ------------------------------------------------------------------

class TestVectorClockSerialization:
    """Tests for to_dict / from_dict round-trip."""

    def test_round_trip(self) -> None:
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        original = VectorClock(
            node_id=node_a,
            clocks={node_a: 3, node_b: 1},
            timestamp=100.0,
        )
        restored = VectorClock.from_dict(original.to_dict())
        assert restored.node_id == original.node_id
        assert restored.clocks == original.clocks
        assert restored.timestamp == original.timestamp

    def test_from_dict_empty(self) -> None:
        restored = VectorClock.from_dict({})
        assert restored.clocks == {}
        assert restored.node_id is None
        assert restored.timestamp == 0.0

    def test_from_dict_with_uuid_node_id(self) -> None:
        """from_dict handles UUID string node_ids."""
        uid = str(uuid.uuid4())
        d = {"node_id": uid, "clocks": {uid: [2]}, "timestamp": 5.0}
        vc = VectorClock.from_dict(d)
        assert vc.clocks == {uid: 2}

    def test_to_dict_serializes_clocks(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(node_id=node, clocks={node: 3}, timestamp=10.0)
        d = vc.to_dict()
        assert d["node_id"] == node
        assert d["clocks"] == {node: [3]}
        assert d["timestamp"] == 10.0


# ------------------------------------------------------------------
# dominates
# ------------------------------------------------------------------

class TestVectorClockDominates:
    """Tests for the dominates (>=) check."""

    def test_dominates_equal(self) -> None:
        node = _make_node_id("a")
        vc = VectorClock(clocks={node: 1}, timestamp=1.0)
        assert vc.dominates(vc) is True

    def test_dominates_strict(self) -> None:
        node = _make_node_id("a")
        vc_low = VectorClock(clocks={node: 1}, timestamp=1.0)
        vc_high = VectorClock(clocks={node: 3}, timestamp=3.0)
        assert vc_high.dominates(vc_low) is True
        assert vc_low.dominates(vc_high) is False

    def test_does_not_dominate_concurrent(self) -> None:
        node_a = _make_node_id("a")
        node_b = _make_node_id("b")
        vc_a = VectorClock(clocks={node_a: 2, node_b: 0}, timestamp=1.0)
        vc_b = VectorClock(clocks={node_a: 0, node_b: 1}, timestamp=2.0)
        assert vc_a.dominates(vc_b) is False
        assert vc_b.dominates(vc_a) is False
