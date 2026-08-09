"""Strategy-pattern path handlers for the gap-dispatch router.

Re-exports the foundation types from :mod:`nfm_db.services.paths.base`
so consumers can ``from nfm_db.services.paths import GapFillPath,
DispatchResult``.
"""

from nfm_db.services.paths.base import DispatchResult, GapFillPath

__all__ = [
    "DispatchResult",
    "GapFillPath",
]
