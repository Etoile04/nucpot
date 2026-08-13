"""Tiered backup pruner — GFS retention with capacity cap (NFM-3024-T2).

Two-phase pruning algorithm:

1. **Tier-aware phase** — classify all backup files via
   :func:`~nfm_db.backup.tier_engine.classify_tier` and mark every
   ``Tier.PRUNABLE`` file for deletion.  This respects tier boundaries:
   files within their configured counts (hourly, daily, weekly) are never
   touched in this phase.

2. **Cap-enforcement phase** — if total bytes of *remaining* files still
   exceed ``maxTotalBytes``, delete the oldest files first *regardless of
   tier* until the total is at or below the cap.

The function is pure: it does not mutate its inputs, performs no I/O,
and returns an immutable :class:`PrunePlan` that the caller can inspect
before executing deletions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nfm_db.backup.schema import BackupConfig
from nfm_db.backup.tier_engine import Tier, classify_tier


@dataclass(frozen=True)
class PrunePlan:
    """Immutable result of a prune computation.

    Attributes:
        tier_violations:  Files deleted because they are beyond the
                           configured tier counts (PRUNABLE tier).
        cap_violations:   Files deleted because total bytes exceeded
                           ``maxTotalBytes`` (oldest-first across all tiers).
        bytes_to_free:    Total bytes that would be freed by applying the plan.
    """

    tier_violations: tuple[dict[str, Any], ...]
    cap_violations: tuple[dict[str, Any], ...]
    bytes_to_free: int


def _size_bytes(record: dict[str, Any]) -> int:
    """Extract ``size_bytes`` from a backup record. Returns 0 if missing."""
    return int(record.get("size_bytes", 0))


def _total_bytes(records: list[dict[str, Any]]) -> int:
    """Sum ``size_bytes`` across all records."""
    return sum(_size_bytes(r) for r in records)


def compute_prune_plan(
    records: list[dict[str, Any]],
    config: BackupConfig,
) -> PrunePlan:
    """Compute which backup files to delete, respecting GFS tiers first then
    the ``maxTotalBytes`` capacity cap.

    Phase 1 — Tier-aware:
        Classify all files.  Every ``Tier.PRUNABLE`` file is marked for
        deletion.  Files within hourly/daily/weekly counts are safe.

    Phase 2 — Cap enforcement:
        If ``maxTotalBytes`` is set and the remaining total exceeds it,
        delete the oldest files first regardless of tier until the total
        is at or below the cap.

    The input ``records`` list is never mutated.
    """
    if not records or config.retention is None:
        return PrunePlan(tier_violations=(), cap_violations=(), bytes_to_free=0)

    # --- Phase 1: tier-aware classification ---
    assignments = classify_tier(records, config.retention)

    tier_deletable: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []

    for assignment in assignments:
        if assignment.tier is Tier.PRUNABLE:
            tier_deletable.append(assignment.record)
        else:
            remaining.append(assignment.record)

    # --- Phase 2: cap enforcement on remaining files ---
    cap_deletable: list[dict[str, Any]] = []

    if config.max_total_bytes is not None:
        # Sort remaining oldest-first for cap enforcement
        remaining_sorted = sorted(
            remaining, key=lambda r: r.get("created_at", datetime.min)
        )

        running_total = _total_bytes(remaining)

        for record in remaining_sorted:
            if running_total <= config.max_total_bytes:
                break
            size = _size_bytes(record)
            running_total -= size
            cap_deletable.append(record)

    total_freed = _total_bytes(tier_deletable) + _total_bytes(cap_deletable)

    return PrunePlan(
        tier_violations=tuple(tier_deletable),
        cap_violations=tuple(cap_deletable),
        bytes_to_free=total_freed,
    )


__all__ = ["PrunePlan", "compute_prune_plan"]
