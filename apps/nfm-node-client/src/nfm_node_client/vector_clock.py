"""Vector Clock implementation for causal ordering.

A vector clock tracks the causal history of mutations across nodes in
the 1+N architecture. Each node maintains its own counter; when
merging, the element-wise maximum is taken. Comparison determines
happens-before, concurrent, or after relationships.

Used by :class:`SyncEngine` for conflict detection (AC-3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClockComparison(str, Enum):
    """Result of comparing two vector clocks."""

    BEFORE = "before"        # self happens-before other
    AFTER = "after"          # self happens-after other
    CONCURRENT = "concurrent"  # neither dominates (potential conflict)


@dataclass(frozen=True)
class VectorClock:
    """Immutable vector clock for tracking causal ordering.

    Parameters
    ----------
    node_id:
        Identifier of the owning node (used for increment).
    clocks:
        Mapping of node_id → logical counter (monotonic integer).
    timestamp:
        Wall-clock timestamp of the last mutation (epoch seconds).
    """

    node_id: str | None = None
    clocks: dict[str, int] = field(default_factory=dict)
    timestamp: float = 0.0

    # ------------------------------------------------------------------
    # Mutation (returns new instance)
    # ------------------------------------------------------------------

    def increment(self, *, timestamp: float | None = None) -> VectorClock:
        """Return a new VectorClock with this node's counter incremented.

        Raises ValueError if node_id is not set.
        """
        if self.node_id is None:
            raise ValueError("node_id is required to increment a vector clock")

        effective_timestamp = timestamp if timestamp is not None else time.time()
        new_clocks = {**self.clocks, self.node_id: self.clocks.get(self.node_id, 0) + 1}
        return VectorClock(
            node_id=self.node_id,
            clocks=new_clocks,
            timestamp=effective_timestamp,
        )

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare(self, other: VectorClock) -> ClockComparison:
        """Compare this clock with another to determine causal ordering.

        Returns:
            BEFORE if all counters in self are <= other and self != other.
            AFTER if all counters in other are <= self and self != other.
            CONCURRENT if neither dominates.
        """
        all_keys = set(self.clocks) | set(other.clocks)

        self_dominates = True
        other_dominates = True

        for key in all_keys:
            self_val = self.clocks.get(key, 0)
            other_val = other.clocks.get(key, 0)
            if self_val < other_val:
                self_dominates = False
            if self_val > other_val:
                other_dominates = False

        if self_dominates and not other_dominates:
            return ClockComparison.AFTER
        if other_dominates and not self_dominates:
            return ClockComparison.BEFORE
        if self_dominates and other_dominates:
            return ClockComparison.AFTER
        return ClockComparison.CONCURRENT

    def dominates(self, other: VectorClock) -> bool:
        """Return True if this clock dominates or equals other."""
        comparison = self.compare(other)
        return comparison in (ClockComparison.AFTER,)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: VectorClock) -> VectorClock:
        """Return a new VectorClock with element-wise maximum counters.

        The node_id and timestamp come from whichever clock has the
        higher wall-clock timestamp (or None / 0.0 if both are empty).
        """
        all_keys = set(self.clocks) | set(other.clocks)
        merged_clocks = {
            key: max(self.clocks.get(key, 0), other.clocks.get(key, 0))
            for key in all_keys
        }

        higher = other if other.timestamp > self.timestamp else self
        return VectorClock(
            node_id=higher.node_id,
            clocks=merged_clocks,
            timestamp=max(self.timestamp, other.timestamp),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict.

        Clock counter values are wrapped in lists for JSON array encoding,
        which ensures they deserialize as integers (not strings).
        """
        serialized_clocks = {k: [v] for k, v in self.clocks.items()}
        return {
            "node_id": self.node_id,
            "clocks": serialized_clocks,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VectorClock:
        """Deserialize from a dict produced by :meth:`to_dict`.

        Handles list-wrapped counter values for JSON interop.
        """
        raw_clocks = data.get("clocks", {})
        clocks: dict[str, int] = {}
        for key, value in raw_clocks.items():
            if isinstance(value, list) and len(value) > 0:
                clocks[str(key)] = int(value[0])
            else:
                clocks[str(key)] = int(value)

        return cls(
            node_id=data.get("node_id"),
            clocks=clocks,
            timestamp=float(data.get("timestamp", 0.0)),
        )


__all__ = [
    "ClockComparison",
    "VectorClock",
]
