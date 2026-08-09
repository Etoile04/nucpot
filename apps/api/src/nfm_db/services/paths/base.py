"""Base protocol and result type for gap-fill path handlers (NFM-2645).

Defines the :class:`GapFillPath` protocol that all concrete handlers must
implement, and the frozen :class:`DispatchResult` dataclass returned by
``execute()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from nfm_db.models.data_collection_request import DataCollectionRequest

# Canonical path names and lifecycle statuses for the gap-dispatch router.
DISPATCH_PATHS: tuple[str, ...] = ("literature", "dft", "external_db")
DISPATCH_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed")


@dataclass(frozen=True)
class DispatchResult:
    """Immutable result returned by a fill-path handler's ``execute()`` method.

    Attributes:
        success: Whether the dispatch was accepted by the handler.
        path: Handler name that processed the request (e.g. ``"literature"``).
        reference: External reference (task ID, UUID, query ID) for tracking.
        error: Human-readable error message when *success* is ``False``.
        data_found: Whether actual data was found (``False`` for placeholders).
    """

    success: bool
    path: str
    reference: str | None = None
    error: str | None = None
    data_found: bool = False


@runtime_checkable
class GapFillPath(Protocol):
    """Protocol for gap-fill path handlers.

    Each concrete handler decides whether it can process a given request
    via ``can_handle()`` and performs the actual data-acquisition work in
    ``execute()``.
    """

    async def can_handle(self, request: DataCollectionRequest) -> bool:
        """Return ``True`` if this handler can process *request*."""
        ...

    async def execute(
        self,
        request: DataCollectionRequest,
    ) -> DispatchResult:
        """Execute the fill path for *request* and return a result."""
        ...
