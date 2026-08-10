"""Base protocol and result type for gap fill path handlers (NFM-2649).

The :class:`GapFillPath` protocol defines the contract that each
concrete path handler must implement.  The dispatch router
(:class:`nfm_db.services.gap_dispatch_router.GapDispatchRouter`) selects
the matching handler by ``source_preference`` and calls ``execute``.

Each handler receives a :class:`~nfm_db.models.DataCollectionRequest`
and returns a :class:`DispatchResult` describing whether the request
was satisfied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nfm_db.models.data_collection_request import DataCollectionRequest

logger = logging.getLogger(__name__)


# Canonical path names and lifecycle statuses for the gap-dispatch router.
# These constants are shared across gap_dispatch_service, gap_dispatch_router,
# and downstream consumers so the router agrees with the handlers and the
# data-collection request models on what values are valid.
DISPATCH_PATHS: tuple[str, ...] = ("literature", "dft", "external_db")
DISPATCH_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed")


__all__ = [
    "DISPATCH_PATHS",
    "DISPATCH_STATUSES",
    "DispatchResult",
    "GapFillPath",
]


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a single ``execute`` invocation by a fill path.

    Attributes:
        success: ``True`` when the path produced a usable result
            (placeholder, stub, or real data).
        path: The path name that produced this result
            (``"literature"``, ``"dft"``, or ``"external_db"``).
        reference: Opaque reference id (DataSource UUID, DFT
            calculation UUID, or ``"<source>:<query_id>"``).
        error: Human-readable error message, or ``None`` on success.
        data_found: ``True`` when real data was returned (e.g. external
            DB hit).  ``False`` for placeholder/stub outputs.
        metadata: Additional structured information about the result.
    """

    success: bool
    path: str
    reference: str | None = None
    error: str | None = None
    data_found: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GapFillPath(Protocol):
    """Protocol for a single gap-fill path handler.

    Implementations decide whether they can satisfy a given
    ``source_preference`` and produce the corresponding fill artifact
    (a DataSource placeholder, a DFT calculation stub, or external DB
    query results).
    """

    def can_handle(self, source_preference: str) -> bool:
        """Return ``True`` when this handler can serve the preference.

        Args:
            source_preference: One of ``"literature"``, ``"dft"``,
                ``"external_db"``, or ``"any"``.
        """
        ...

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Perform the fill path and return a :class:`DispatchResult`.

        Args:
            request: The DataCollectionRequest to satisfy.

        Returns:
            A :class:`DispatchResult` describing the outcome.
        """
        ...
