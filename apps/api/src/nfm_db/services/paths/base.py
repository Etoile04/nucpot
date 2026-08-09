"""Strategy pattern foundation for the gap-fill path dispatcher (NFM-2648).

Defines two building blocks used by the gap-dispatch router and the
individual path implementations:

- :class:`DispatchResult` — a frozen dataclass describing the outcome
  of a single path's ``execute`` call.  Immutable so results can be
  safely passed across coroutine boundaries and stored as dictionary
  keys / set members.
- :class:`GapFillPath` — a ``runtime_checkable`` ``Protocol`` that
  individual collection paths (literature, DFT, external DB) implement.
  The router calls ``can_handle`` to pick a path and ``execute`` to
  run it.

Note: this module deliberately uses PEP 604 ``str | None`` rather than
the ``typing``-module legacy alias per the NFM-2648 acceptance criteria.

There is an unrelated ``DispatchResult`` defined in
:mod:`nfm_db.services.gap_dispatch_service` with a different schema
(``request_id``, ``path_taken``, ``status``, ``detail``, ``metadata``).
That class describes an outer dispatch outcome and continues to be
returned by :class:`GapDispatchService`.  Migrating ``GapDispatchService``
to the new ``DispatchResult`` is a separate follow-up issue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DispatchResult",
    "GapFillPath",
]


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a single path's ``execute`` invocation.

    Attributes:
        success: Whether the path completed without raising.
        path: Stable identifier of the path that produced this result
            (e.g. ``"literature"``, ``"dft"``, ``"external_db"``).
        reference: External reference (DOI, calculation id, source id)
            for whatever the path produced, or ``None`` if no data was
            found.
        error: Human-readable error message when ``success`` is False,
            otherwise ``None``.
        data_found: Whether the path actually produced data — a path
            may succeed (``success=True``) without finding any data.
    """

    success: bool
    path: str
    reference: str | None
    error: str | None
    data_found: bool


@runtime_checkable
class GapFillPath(Protocol):
    """Async strategy interface for gap-fill collection paths.

    Concrete paths (literature, DFT, external DB) implement this
    protocol so the dispatcher can route ``DataCollectionRequest``
    objects to whichever path claims them.
    """

    async def can_handle(self, request: Any) -> bool:
        """Return ``True`` if this path can handle ``request``."""
        ...

    async def execute(self, request: Any) -> DispatchResult:
        """Run this path against ``request`` and return the outcome."""
        ...
