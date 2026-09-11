"""Managed httpx.AsyncClient lifecycle for LightRAG (NFM-1245).

Provides a module-level singleton that ensures exactly one ``httpx.AsyncClient``
is shared across all LightRAG consumers (API endpoints, fire-and-forget tasks,
RAG provider) and is properly closed on application shutdown.

Usage from FastAPI lifespan::

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield  # get_shared_lightrag_client() is called lazily
        await close_lightrag_client()  # <-- close on shutdown

Usage from consumers::

    from nfm_db.services.lightrag_lifecycle import get_shared_lightrag_client

    client = get_shared_lightrag_client()
    if client is not None:
        await client.health_check()

Design notes:
    - The client is lazily created on first ``get_shared_lightrag_client()``
      call (not at import time) to avoid side effects during testing.
    - When LightRAG is not configured, ``get_shared_lightrag_client()``
      returns ``None`` and ``close_lightrag_client()`` is a no-op.
    - The singleton is stored at module level and cleared on ``close()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nfm_db.services.lightrag_client import (
    LightRAGClient,
    is_lightrag_configured,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Module-level singleton — None means "not yet created" or "closed".
_shared_client: LightRAGClient | None = None


def _client_bound_to_open_loop(client: LightRAGClient | None) -> bool:
    """Return True iff ``client`` is bound to an open event loop.

    NFM-4719 / NFM-4717 / NFM-4083 family: ``httpx.AsyncClient`` binds to
    the loop that constructed it.  In a Celery worker each task runs in a
    throwaway ``asyncio.run`` loop; the singleton survives across tasks
    (module-level) so the SECOND task hits a closed-loop client and every
    POST raises ``RuntimeError: Event loop is closed``.  Detect this and
    force a rebuild instead of letting the silent-failure swallow the
    exception downstream.
    """
    if client is None:
        return False
    if not hasattr(client, "_loop"):
        return True  # backward-compat: legacy clients without the marker
    loop = client._loop
    if loop is None:
        return True  # built off-loop; first in-loop caller rebinds
    return not loop.is_closed()


def get_shared_lightrag_client() -> LightRAGClient | None:
    """Return the shared ``LightRAGClient`` singleton.

    On first call (or after ``close_lightrag_client``), lazily creates
    a new client from environment variables / application settings.

    NFM-4719: when the existing singleton is bound to a closed event
    loop (Celery worker re-entry path), reset it so the caller gets a
    fresh client bound to the current loop.  Without this, every POST
    on the SECOND task in a worker process would raise
    ``RuntimeError: Event loop is closed`` — silently swallowed by the
    outer ``except Exception`` in ``ingest_kg_to_lightrag`` and
    misreported as "LightRAG inline ingest done" while no VDB rows
    landed.

    Returns:
        A ``LightRAGClient`` instance, or ``None`` if LightRAG is
        not configured (``NFM_LIGHTRAG_HOST`` not set).
    """
    global _shared_client

    if not is_lightrag_configured():
        if _shared_client is not None:
            # Mis-config change at runtime — drop the stale client.
            _shared_client = None
        return None

    if _shared_client is not None and not _client_bound_to_open_loop(_shared_client):
        logger.debug(
            "Shared LightRAG client bound to closed loop (%s); recreating",
            _shared_client.base_url,
        )
        _shared_client = None

    if _shared_client is not None:
        return _shared_client

    _shared_client = LightRAGClient()
    logger.debug("Created shared LightRAG client: %s", _shared_client.base_url)
    return _shared_client


async def close_lightrag_client() -> None:
    """Close and reset the shared ``LightRAGClient`` singleton.

    Safe to call multiple times — if no client exists or the client
    has already been closed, this is a no-op.
    """
    global _shared_client

    client = _shared_client
    _shared_client = None

    if client is not None:
        try:
            await client.close()
            logger.debug("Closed shared LightRAG client")
        except Exception:
            logger.warning("Failed to close shared LightRAG client", exc_info=True)


def reset_lightrag_client() -> None:
    """Reset the singleton without closing.

    Used in tests to force re-creation of the client on the next
    ``get_shared_lightrag_client()`` call.
    """
    global _shared_client
    _shared_client = None
