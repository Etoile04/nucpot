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

from nfm_db.services.paths.base import (
    DISPATCH_PATHS,
    DISPATCH_STATUSES,
    DispatchResult,
    GapFillPath,
)

__all__ = [
    "DISPATCH_PATHS",
    "DISPATCH_STATUSES",
    "DispatchResult",
    "GapFillPath",
]

import logging

_logger = logging.getLogger(__name__)

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
    except ImportError as _exc:
        _logger.warning(
            "Skipping optional fill path %s (%s): %s",
            _handler_name,
            _module,
            _exc,
        )
