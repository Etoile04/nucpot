"""Conflict resolution strategies for multi-source property values (NFM-839 B3.2).

Determines the winning value when multiple sources report different values for
the same material/property pair. Strategies: newest, confidence, consensus, manual.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nfm_db.models.conflict import ResolutionStrategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Candidate:
    """Immutable wrapper for a single value candidate."""

    entry: dict[str, Any]

    @property
    def value(self) -> float:
        return float(self.entry["value"])

    @property
    def source_id(self) -> str:
        return str(self.entry["source_id"])

    @property
    def confidence(self) -> float:
        return float(self.entry["confidence"])

    @property
    def extracted_at(self) -> datetime:
        raw = self.entry.get("extracted_at")
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            return datetime.fromisoformat(raw)
        raise ValueError(
            f"extracted_at must be a datetime or ISO string, got {type(raw).__name__}"
        )


# Re-export frozen Candidate so callers can inspect the shape if needed.
Candidate = _Candidate


def _as_candidates(values: list[dict[str, Any]]) -> list[_Candidate]:
    """Convert raw dicts into validated Candidate instances."""
    return [_Candidate(entry=v) for v in values]


def _build_result(
    winner: _Candidate,
    strategy: str,
    reason: str,
) -> dict[str, Any]:
    """Build the resolved-value dict expected by FusionPipeline."""
    return {
        "value": winner.value,
        "resolution_reason": reason,
        "source_id": winner.source_id,
        "confidence": winner.confidence,
        "strategy": strategy,
    }


class ConflictResolver:
    """Resolves property value conflicts using a configurable strategy.

    Usage::

        resolver = ConflictResolver()
        result = resolver.resolve(candidates, strategy=ResolutionStrategy.CONFIDENCE)
    """

    # -- public API -----------------------------------------------------------

    def resolve(
        self,
        values: list[dict[str, Any]],
        *,
        strategy: ResolutionStrategy,
    ) -> dict[str, Any]:
        """Apply *strategy* to pick the winning value.

        Args:
            values: Each dict must contain ``value``, ``source_id``,
                ``confidence``, and ``extracted_at``.
            strategy: One of the :class:`ResolutionStrategy` members.

        Returns:
            A dict with at least ``resolved_value`` and ``resolution_reason``.

        Raises:
            ValueError: If *strategy* is ``MANUAL`` or values list is empty.
        """
        if not values:
            raise ValueError("Cannot resolve an empty candidate list")

        candidates = _as_candidates(values)

        handler = {
            ResolutionStrategy.NEWEST: self._resolve_newest,
            ResolutionStrategy.CONFIDENCE: self._resolve_confidence,
            ResolutionStrategy.CONSENSUS: self._resolve_consensus,
        }

        if strategy == ResolutionStrategy.MANUAL:
            raise ValueError(
                "Manual strategy requires human review; cannot auto-resolve"
            )

        resolver_fn = handler[strategy]
        result = resolver_fn(candidates)

        logger.info(
            "Resolved conflict via %s strategy: value=%s source=%s",
            strategy.value,
            result["value"],
            result.get("source_id"),
        )
        return result

    # -- strategy implementations ----------------------------------------------

    def _resolve_newest(
        self,
        candidates: list[_Candidate],
    ) -> dict[str, Any]:
        """Pick the candidate with the most recent ``extracted_at``."""
        winner = max(candidates, key=lambda c: c.extracted_at)
        return _build_result(
            winner,
            ResolutionStrategy.NEWEST.value,
            f"Newest extraction at {winner.extracted_at.isoformat()}",
        )

    def _resolve_confidence(
        self,
        candidates: list[_Candidate],
    ) -> dict[str, Any]:
        """Pick the candidate with the highest ``confidence`` score."""
        winner = max(candidates, key=lambda c: c.confidence)
        return _build_result(
            winner,
            ResolutionStrategy.CONFIDENCE.value,
            f"Highest confidence ({winner.confidence:.3f})",
        )

    def _resolve_consensus(
        self,
        candidates: list[_Candidate],
    ) -> dict[str, Any]:
        """Pick the most common value (mode). Tie-break on confidence."""
        value_counts: Counter[float] = Counter(c.value for c in candidates)
        max_freq = max(value_counts.values())

        # All values that share the highest frequency
        modes = {v for v, count in value_counts.items() if count == max_freq}

        if len(modes) == 1:
            winner = next(c for c in candidates if c.value == modes.pop())
            return _build_result(
                winner,
                ResolutionStrategy.CONSENSUS.value,
                f"Consensus (mode, freq={max_freq})",
            )

        # Tie — fall back to confidence among the tied values
        tied = [c for c in candidates if c.value in modes]
        winner = max(tied, key=lambda c: c.confidence)
        return _build_result(
            winner,
            ResolutionStrategy.CONSENSUS.value,
            f"Consensus tie broken by confidence ({winner.confidence:.3f})",
        )
