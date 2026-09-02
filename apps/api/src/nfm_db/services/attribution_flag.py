"""Attribution-flag configuration for NFM-4134 §5 / NFM-4159 backend.

This module resolves three runtime knobs that the §5.2 locked contract
depends on but which are NOT hardcoded in SQL:

1. ``NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS`` — comma-separated
   UUID list of the 4 canonical ``data_sources.id`` values that
   absorbed measurements during migration 070's collapse.  Until CTO
   publishes them, this MUST default to ``""`` (empty tuple) so the
   predicate is a safe no-op and every measurement returns
   ``status: "intact"``.

2. ``NFM_RECAST_RESTORED_DATASET_IDS`` — comma-separated UUID list of
   the 10 datasets restored from ``datasets_backup_070`` (NFM-4136,
   now ``done``).  Until CEO publishes them, this defaults to ``""``.
   Each matching dataset surfaces ``attribution.status: "placeholder"``
   on ``GET /api/v1/datasets/{id}``.

3. ``NFM_ATTRIBUTION_LOST_AT`` — the immutable timestamp the §5.2
   contract pins for ``lostAt``.  Defaults to ``2026-09-02`` (the
   migration 070 collapse date).  Override only for forensic
   reconstruction; not part of the contract.

Env-var choice (NFM-4159 AC) — read once at module load, memoised.
This mirrors the precedent in ``adr009_flag.py`` and avoids dragging
in a new settings-table pattern.  CTO/CEO deploy the canonicals as
config changes, with zero code churn.

Tests can reset the cache via ``reset_attribution_flag_cache()`` — the
fixture in ``tests/test_attribution_flag.py`` does this between every
test to isolate env mutations.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

# ---------------------------------------------------------------------------
# Constants — exported so tests + migration can reference the canonical names.
# ---------------------------------------------------------------------------

ATTRIBUTION_LOST_CANONICAL_ENV: str = "NFM_ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"
RECAST_RESTORED_DATASET_IDS_ENV: str = "NFM_RECAST_RESTORED_DATASET_IDS"
ATTRIBUTION_LOST_AT_ENV: str = "NFM_ATTRIBUTION_LOST_AT"

# Locked §5.2 value.  Override only for forensic reconstruction.
DEFAULT_ATTRIBUTION_LOST_AT: str = "2026-09-02"

# Audit-trail name (matches the spec's "feature flag" framing).
ATTRIBUTION_FEATURE_FLAG_NAME: str = "ATTRIBUTION_LOST_CANONICAL_DATA_SOURCE_IDS"


# ---------------------------------------------------------------------------
# Memoised state — private; reset via ``reset_attribution_flag_cache``.
# ---------------------------------------------------------------------------


_CanonicalIdsCache = tuple[uuid.UUID, ...]
_DatasetIdsCache = tuple[uuid.UUID, ...]

_canonical_ids_cache: _CanonicalIdsCache | None = None
_dataset_ids_cache: _DatasetIdsCache | None = None
_lost_at_cache: date | None = None


def reset_attribution_flag_cache() -> None:
    """Drop the memoised cache. Tests call this between cases."""
    global _canonical_ids_cache, _dataset_ids_cache, _lost_at_cache
    _canonical_ids_cache = None
    _dataset_ids_cache = None
    _lost_at_cache = None


# ---------------------------------------------------------------------------
# Parsing — straight, no frills.
# ---------------------------------------------------------------------------


def _parse_uuid_list(raw: str, env_name: str) -> tuple[uuid.UUID, ...]:
    """Parse ``"u1,u2,..."`` → ``(UUID(u1), UUID(u2), ...)``.

    Whitespace around commas is tolerated; empty/whitespace-only strings
    return an empty tuple.  Invalid UUIDs raise ``ValueError`` so the
    config bug surfaces at boot rather than at first request.
    """
    parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p]
    result: list[uuid.UUID] = []
    for p in parts:
        try:
            result.append(uuid.UUID(p))
        except (ValueError, AttributeError) as exc:  # pragma: no cover - defensive
            raise ValueError(
                f"{env_name}: invalid UUID '{p}': {exc}"
            ) from exc
    return tuple(result)


def _parse_lost_at(raw: str) -> date:
    """Parse ``"YYYY-MM-DD"`` → ``date``. ISO 8601 only."""
    return date.fromisoformat(raw)


# ---------------------------------------------------------------------------
# Public API — read once at boot, memoised on first call.
# ---------------------------------------------------------------------------


def get_lost_canonical_data_source_ids() -> tuple[uuid.UUID, ...]:
    """Return the 4 canonical ``data_sources.id`` values that absorbed losses.

    Empty tuple = safe no-op (§5.1 filter becomes a no-op, every row
    intact).  This is the default until CTO publishes the IDs.

    The cache survives subsequent ``os.environ`` mutations in the same
    process — call :func:`reset_attribution_flag_cache` to force a re-read.
    """
    global _canonical_ids_cache
    if _canonical_ids_cache is None:
        raw = os.environ.get(ATTRIBUTION_LOST_CANONICAL_ENV, "")
        _canonical_ids_cache = _parse_uuid_list(raw, ATTRIBUTION_LOST_CANONICAL_ENV)
    return _canonical_ids_cache


def get_recast_restored_dataset_ids() -> tuple[uuid.UUID, ...]:
    """Return the 10 dataset IDs restored from ``datasets_backup_070``.

    Empty tuple = no dataset is flagged ``placeholder``.  This is the
    default until CEO publishes the IDs.
    """
    global _dataset_ids_cache
    if _dataset_ids_cache is None:
        raw = os.environ.get(RECAST_RESTORED_DATASET_IDS_ENV, "")
        _dataset_ids_cache = _parse_uuid_list(raw, RECAST_RESTORED_DATASET_IDS_ENV)
    return _dataset_ids_cache


def get_attribution_lost_at() -> date:
    """Return the §5.2 ``lostAt`` timestamp (default ``2026-09-02``)."""
    global _lost_at_cache
    if _lost_at_cache is None:
        raw = os.environ.get(ATTRIBUTION_LOST_AT_ENV, DEFAULT_ATTRIBUTION_LOST_AT)
        _lost_at_cache = _parse_lost_at(raw)
    return _lost_at_cache


def attribution_feature_flag_name() -> str:
    """Audit-trail name (matches §5.2's "feature flag" framing)."""
    return ATTRIBUTION_FEATURE_FLAG_NAME


__all__ = [
    "ATTRIBUTION_FEATURE_FLAG_NAME",
    "ATTRIBUTION_LOST_AT_ENV",
    "ATTRIBUTION_LOST_CANONICAL_ENV",
    "DEFAULT_ATTRIBUTION_LOST_AT",
    "RECAST_RESTORED_DATASET_IDS_ENV",
    "attribution_feature_flag_name",
    "get_attribution_lost_at",
    "get_lost_canonical_data_source_ids",
    "get_recast_restored_dataset_ids",
    "reset_attribution_flag_cache",
]
