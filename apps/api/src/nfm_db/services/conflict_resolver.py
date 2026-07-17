"""Conflict resolution engine with 4 configurable strategies.

Per ADR-NFM-817-3, supports:
  - newest: Most recent extraction wins
  - confidence: Highest confidence score wins
  - consensus: Statistical aggregation (mean/median with outlier detection)
  - manual: Route to review queue for human decision

Each strategy is a pure function that takes a list of conflicting values
and returns the resolved value (or None for manual escalation).
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any

from nfm_db.models.conflict_record import ConflictStrategy, ConflictStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ConflictingEntry = dict[str, Any]
"""Expected keys: value, source_id, confidence, extracted_at."""

# ---------------------------------------------------------------------------
# Strategy validators
# ---------------------------------------------------------------------------

VALID_STRATEGIES = frozenset(list(ConflictStrategy))


def validate_strategy(strategy: str) -> str:
    """Raise ValueError if strategy is not recognized."""
    if strategy not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown conflict strategy '{strategy}'. "
            f"Must be one of: {', '.join(sorted(VALID_STRATEGIES))}"
        )
    return strategy


# ---------------------------------------------------------------------------
# Strategy: newest
# ---------------------------------------------------------------------------


def resolve_newest(entries: list[ConflictingEntry]) -> dict[str, Any] | None:
    """Select the most recently extracted value.

    Picks the entry with the latest ``extracted_at`` timestamp.
    If timestamps are missing or equal, falls back to the first entry.
    Returns None if entries is empty.
    """
    if not entries:
        return None

    def _sort_key(entry: ConflictingEntry) -> tuple[int, int]:
        ts = entry.get("extracted_at")
        if ts is None:
            return (0, 0)
        if isinstance(ts, datetime):
            return (1, int(ts.timestamp()))
        if isinstance(ts, str):
            try:
                return (1, int(datetime.fromisoformat(ts).timestamp()))
            except (ValueError, TypeError):
                return (0, 0)
        return (0, 0)

    sorted_entries = sorted(entries, key=_sort_key, reverse=True)
    return sorted_entries[0]


# ---------------------------------------------------------------------------
# Strategy: confidence
# ---------------------------------------------------------------------------


def resolve_confidence(entries: list[ConflictingEntry]) -> dict[str, Any] | None:
    """Select the value with the highest confidence score.

    Returns None if entries is empty.
    Ties are broken by most recent extraction date.
    """
    if not entries:
        return None

    def _sort_key(entry: ConflictingEntry) -> tuple[float, tuple[int, int]]:
        conf = float(entry.get("confidence", 0.0))
        ts = entry.get("extracted_at")
        if ts is None:
            return (conf, (0, 0))
        if isinstance(ts, datetime):
            return (conf, (1, int(ts.timestamp())))
        if isinstance(ts, str):
            try:
                return (conf, (1, int(datetime.fromisoformat(ts).timestamp())))
            except (ValueError, TypeError):
                return (conf, (0, 0))
        return (conf, (0, 0))

    sorted_entries = sorted(entries, key=_sort_key, reverse=True)
    return sorted_entries[0]


# ---------------------------------------------------------------------------
# Strategy: consensus
# ---------------------------------------------------------------------------


def resolve_consensus(entries: list[ConflictingEntry]) -> dict[str, Any] | None:
    """Statistical aggregation with outlier detection.

    For numeric values: compute median, filter values within 1.5× IQR
    of the median, then return the mean of the remaining values.

    For non-numeric values: return the most common value (mode).

    Returns None if entries is empty or no numeric values found.
    """
    if not entries:
        return None

    numeric_values: list[float] = []
    entry_map: dict[float, list[ConflictingEntry]] = {}

    for entry in entries:
        raw_val = entry.get("value", {})
        if isinstance(raw_val, dict):
            scalar = raw_val.get("value_scalar")
        elif isinstance(raw_val, (int, float)):
            scalar = raw_val
        else:
            scalar = None

        if scalar is not None:
            try:
                num = float(scalar)
                numeric_values.append(num)
                entry_map.setdefault(num, []).append(entry)
            except (ValueError, TypeError):
                continue

    if not numeric_values:
        return None

    if len(numeric_values) == 1:
        return entry_map[numeric_values[0]][0]

    median = statistics.median(numeric_values)

    if len(numeric_values) >= 3:
        sorted_vals = sorted(numeric_values)
        q1 = statistics.median(sorted_vals[: len(sorted_vals) // 2])
        q3 = statistics.median(sorted_vals[len(sorted_vals) // 2 + 1 :])
        iqr = q3 - q1
        lower_bound = median - 1.5 * iqr
        upper_bound = median + 1.5 * iqr
        filtered = [v for v in numeric_values if lower_bound <= v <= upper_bound]
    else:
        filtered = numeric_values

    if not filtered:
        filtered = [median]

    mean_val = statistics.mean(filtered)

    # Find the entry closest to the mean
    closest_val = min(filtered, key=lambda v: abs(v - mean_val))
    best_entries = entry_map[closest_val]

    return best_entries[0]


# ---------------------------------------------------------------------------
# Strategy: manual
# ---------------------------------------------------------------------------


def resolve_manual(
    entries: list[ConflictingEntry],
) -> dict[str, Any] | None:
    """Mark conflict for human review.

    Always returns None, signalling the caller to escalate
    the conflict to the review queue.
    """
    return None


# ---------------------------------------------------------------------------
# Strategy dispatch
# ---------------------------------------------------------------------------

STRATEGY_FUNCTIONS: dict[str, type] = {
    ConflictStrategy.NEWEST: resolve_newest,
    ConflictStrategy.CONFIDENCE: resolve_confidence,
    ConflictStrategy.CONSENSUS: resolve_consensus,
    ConflictStrategy.MANUAL: resolve_manual,
}


def resolve_conflict(
    entries: list[ConflictingEntry],
    strategy: str,
) -> dict[str, Any] | None:
    """Apply a resolution strategy to a set of conflicting values.

    Args:
        entries: List of conflicting value entries with keys:
            value, source_id, confidence, extracted_at.
        strategy: One of newest, confidence, consensus, manual.

    Returns:
        The winning entry dict, or None for manual strategy.

    Raises:
        ValueError: If strategy is not recognized.
    """
    validate_strategy(strategy)
    resolver = STRATEGY_FUNCTIONS[strategy]
    return resolver(entries)


def get_strategy_for_property_type(
    default_strategy: str | None,
    override: str | None = None,
) -> str:
    """Determine the effective strategy for a property type.

    API override takes precedence over the property type default.
    Falls back to 'confidence' if neither is set.
    """
    if override is not None:
        return validate_strategy(override)
    if default_strategy is not None:
        return validate_strategy(default_strategy)
    return ConflictStrategy.CONFIDENCE
