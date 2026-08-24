"""ADR-009 §4.3-c feature flag (NFM-3586).

Gate for the daily 06:00 UTC reconcile routine. Default OFF per ADR-009 §6
(rollout: both §4.1 and §4.3 ship OFF and are promoted together).

Environment variable: ``NFM_ADR_009_RECONCILIATION_HOOK_ENABLED``. The flag is
memoised at module load so production code does not pay an ``os.environ``
lookup on every reconcile invocation; tests can clear the cache via
``adr009_flag._FLAG_CACHE = None`` and re-import.

Truthy values: ``true``, ``1``, ``yes``, ``on`` (case-insensitive, with
surrounding whitespace tolerated). Anything else, including unset, is OFF.
"""

from __future__ import annotations

import os

_FLAG_ENV: str = "NFM_ADR_009_RECONCILIATION_HOOK_ENABLED"
_FLAG_NAME: str = "ADR_009_RECONCILIATION_HOOK_ENABLED"
_TRUTHY: frozenset[str] = frozenset({"true", "1", "yes", "on"})

# Memoised lookup. Tests can clear this to re-read the environment.
_FLAG_CACHE: bool | None = None


def is_reconcile_routine_enabled() -> bool:
    """Return ``True`` iff the ADR-009 reconcile routine is enabled."""
    global _FLAG_CACHE
    if _FLAG_CACHE is None:
        raw = os.environ.get(_FLAG_ENV, "").strip().lower()
        _FLAG_CACHE = raw in _TRUTHY
    return _FLAG_CACHE


def feature_flag_name() -> str:
    """Return the canonical feature-flag name (matches §4.1's audit field)."""
    return _FLAG_NAME


__all__ = [
    "feature_flag_name",
    "is_reconcile_routine_enabled",
]
