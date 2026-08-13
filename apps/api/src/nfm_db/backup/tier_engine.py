"""Pure tier-classification engine for backup files (NFM-3024-T1).

The engine consumes an iterable of backup file records (each a dict with at
least a ``created_at`` :class:`datetime.datetime`) and assigns each one to a
retention tier based on its age relative to its peers.

Tier order (newest-first):

1. ``Tier.HOURLY``  — the freshest ``retention.hourly.count`` files.
2. ``Tier.DAILY``   — the next ``retention.daily.count`` files.
3. ``Tier.WEEKLY``  — the next ``retention.weekly.count`` files.
4. ``Tier.PRUNABLE``— anything beyond the weekly count (oldest first).

The function is pure: it does not mutate its inputs and it has no I/O.
The companion loader (:mod:`nfm_db.config.backup_loader`) is the only
side-effecting piece.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from nfm_db.backup.schema import RetentionConfig


class Tier(str, Enum):
    """Retention tier a backup file belongs to."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    PRUNABLE = "prunable"


@dataclass(frozen=True)
class TierAssignment:
    """Result of classifying a single backup file.

    ``record`` is the original dict the caller passed in — the engine never
    mutates or copies it. ``tier`` is the assigned retention tier.
    """

    record: dict[str, Any]
    tier: Tier


def _created_at_key(record: dict[str, Any]) -> datetime:
    """Extract the ``created_at`` field from a backup file record.

    Records are dicts of the form ``{"path": ..., "created_at": datetime, ...}``.
    Anything else raises :class:`TypeError` so callers learn early if they
    hand us the wrong shape.
    """
    created_at = record.get("created_at")
    if not isinstance(created_at, datetime):
        raise TypeError(
            "Backup file record missing 'created_at' datetime field; got "
            f"{type(created_at).__name__}"
        )
    return created_at


def sort_by_age_desc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new list sorted newest-first by ``created_at``.

    The input list is not mutated (verified by
    ``test_sort_by_age_desc_does_not_mutate_input``).
    """
    return sorted(records, key=_created_at_key, reverse=True)


def classify_tier(
    records: list[dict[str, Any]],
    config: RetentionConfig,
) -> list[TierAssignment]:
    """Assign each backup file to a retention tier.

    Sorting is performed internally — callers do not need to pre-sort. The
    result is parallel to ``records`` (sorted) and never mutates the input.

    Edge cases handled:

    - Empty input → empty output.
    - Fewer files than ``hourly.count`` → all HOURLY.
    - Files beyond ``hourly + daily + weekly`` → PRUNABLE (oldest first).
    """
    sorted_records = sort_by_age_desc(records)

    hourly_cutoff = config.hourly.count
    daily_cutoff = hourly_cutoff + config.daily.count
    weekly_cutoff = daily_cutoff + config.weekly.count

    results: list[TierAssignment] = []
    for index, record in enumerate(sorted_records):
        if index < hourly_cutoff:
            tier = Tier.HOURLY
        elif index < daily_cutoff:
            tier = Tier.DAILY
        elif index < weekly_cutoff:
            tier = Tier.WEEKLY
        else:
            tier = Tier.PRUNABLE
        results.append(TierAssignment(record=record, tier=tier))

    return results


__all__ = ["Tier", "TierAssignment", "classify_tier", "sort_by_age_desc"]
