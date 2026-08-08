"""Gap-fill path handlers (NFM-2645).

Concrete implementations of the :class:`GapFillPath` protocol that dispatch
a :class:`~nfm_db.models.data_collection_request.DataCollectionRequest` to the
appropriate data-acquisition strategy: literature search, DFT calculation, or
external database query.

Public API::

    from nfm_db.services.paths import (
        DispatchResult,
        GapFillPath,
        LiteratureFillPath,
        DFTFillPath,
        ExternalDBFillPath,
    )
"""

from __future__ import annotations

from nfm_db.services.paths.base import DispatchResult, GapFillPath

__all__ = [
    "DispatchResult",
    "GapFillPath",
]

# Concrete fill paths — imported lazily so downstream consumers can import
# the protocol without pulling in heavy dependencies (httpx, etc.).
for _handler_name, _module in [
    ("LiteratureFillPath", "nfm_db.services.paths.literature_fill"),
    ("DFTFillPath", "nfm_db.services.paths.dft_fill"),
    ("ExternalDBFillPath", "nfm_db.services.paths.external_db_fill"),
]:
    try:
        _mod = __import__(_module, fromlist=[_handler_name])
        globals()[_handler_name] = getattr(_mod, _handler_name)
        __all__.append(_handler_name)
    except ImportError:
        pass
