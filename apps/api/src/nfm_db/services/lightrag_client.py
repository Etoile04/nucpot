"""LightRAG sidecar client protocol and stub (NFM-1061).

Defines the interface that the LightRAG proxy endpoints use to communicate
with the sidecar service.  A ``StubLightRAGClient`` is provided for
testing and initial development before the real sidecar is available.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from nfm_db.schemas.lightrag import (
    GraphQueryResponse,
    HealthResponse,
    QueryMode,
    QueryResponse,
)


@runtime_checkable
class LightRAGClientProtocol(Protocol):
    """Interface for LightRAG sidecar clients."""

    async def ingest(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> uuid.UUID: ...

    async def query(
        self,
        query: str,
        mode: QueryMode = QueryMode.HYBRID,
    ) -> QueryResponse: ...

    async def graph_query(self, query: str) -> GraphQueryResponse: ...

    async def health(self) -> HealthResponse: ...


class StubLightRAGClient:
    """No-op client that always raises ConnectionError.

    Used as the default client so the proxy endpoints return 503 until
    a real sidecar is configured.
    """

    async def ingest(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        source_id: str | None = None,
    ) -> uuid.UUID:
        raise ConnectionError("LightRAG sidecar not configured")

    async def query(
        self,
        query: str,
        mode: QueryMode = QueryMode.HYBRID,
    ) -> QueryResponse:
        raise ConnectionError("LightRAG sidecar not configured")

    async def graph_query(self, query: str) -> GraphQueryResponse:
        raise ConnectionError("LightRAG sidecar not configured")

    async def health(self) -> HealthResponse:
        raise ConnectionError("LightRAG sidecar not configured")
