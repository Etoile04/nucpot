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
    from nfm_db.config import Settings

logger = logging.getLogger(__name__)

# Module-level singleton — None means "not yet created" or "closed".
_shared_client: LightRAGClient | None = None


def get_shared_lightrag_client() -> LightRAGClient | None:
    """Return the shared ``LightRAGClient`` singleton.

    On first call (or after ``close_lightrag_client``), lazily creates
    a new client from environment variables / application settings.

    Returns:
        A ``LightRAGClient`` instance, or ``None`` if LightRAG is
        not configured (``NFM_LIGHTRAG_HOST`` not set).
    """
    global _shared_client

    if _shared_client is not None:
        return _shared_client

    if not is_lightrag_configured():
        return None

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
