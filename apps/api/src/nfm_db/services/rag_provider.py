"""RAG provider abstraction with auto-fallback (NFM-1223).

Defines a `RAGProvider` protocol so that the KG pipeline can delegate
knowledge-graph operations to either the LightRAG sidecar or a
rules-based PostgreSQL full-text search fallback, with automatic
selection based on sidecar health.

Architecture::

    ┌─────────────┐    health check    ┌──────────────────────┐
    │  KG Pipeline │ ────────────────── │ RAGProviderSelector   │
    └──────┬───────┘                   │  ├─ LightRAGProvider  │
           │                            │  └─ RuleBasedFallback │
           ▼                            └──────────────────────┘
    RAGProvider (Protocol)
      ├─ query(query) -> RAGQueryResult
      ├─ ingest(text, source) -> None
      └─ health() -> bool
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.lightrag_client import (
    LightRAGClient,
    LightRAGClientError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RAGQueryResult:
    """Unified result from any RAG provider."""

    response: str
    references: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    fallback: bool = False


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class RAGProvider(ABC):
    """Abstract base for RAG providers.

    Each implementation wraps a different backend (LightRAG sidecar,
    PG full-text search, etc.) behind a uniform interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider identifier."""

    @abstractmethod
    async def query(self, *, query: str, **kwargs: Any) -> RAGQueryResult:
        """Execute a knowledge-graph query."""

    @abstractmethod
    async def ingest(self, *, text: str, source: str | None = None) -> str | None:
        """Ingest a document into the knowledge graph.

        Returns a provider-specific tracking ID (e.g. LightRAG ``track_id``)
        or ``None`` when the provider does not support tracking.
        """

    @abstractmethod
    async def health(self) -> bool:
        """Return True if the underlying service is healthy."""


# ---------------------------------------------------------------------------
# LightRAG provider
# ---------------------------------------------------------------------------


class LightRAGProvider(RAGProvider):
    """RAG provider that delegates to the LightRAG sidecar service."""

    def __init__(self, client: LightRAGClient | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            from nfm_db.services.lightrag_lifecycle import get_shared_lightrag_client

            shared = get_shared_lightrag_client()
            if shared is not None:
                self._client = shared
            else:
                self._client = LightRAGClient()

    @property
    def name(self) -> str:
        return "lightrag"

    async def query(self, *, query: str, **kwargs: Any) -> RAGQueryResult:
        result = await self._client.query(query=query)
        return RAGQueryResult(
            response=result.get("response", ""),
            references=result.get("references", []),
            entities=result.get("entities", []),
            relationships=result.get("relationships", []),
            provider=self.name,
        )

    async def ingest(self, *, text: str, source: str | None = None) -> str | None:
        """Ingest and return the LightRAG track_id (if any).

        Wraps :meth:`LightRAGClient.ingest` which returns a dict that
        may contain a ``track_id`` key.  Returns the string value or
        ``None`` when absent.
        """
        result = await self._client.ingest(text=text, file_source=source)
        return result.get("track_id") if isinstance(result, dict) else None

    async def health(self) -> bool:
        return await self._client.health_check()


# ---------------------------------------------------------------------------
# Rule-based fallback provider (PG full-text search)
# ---------------------------------------------------------------------------

_QUERY_TOKEN_RE = re.compile(r"\w+")


class RuleBasedFallbackProvider(RAGProvider):
    """RAG provider using PostgreSQL full-text search as a fallback.

    When the LightRAG sidecar is unavailable, this provider extracts
    keywords from the query and performs ``ts_rank``-based matching
    against existing database tables via a UNION of:

    * ``data_sources`` — searches ``title`` and ``abstract``
    * ``materials``   — searches ``name`` and ``description``
    * ``kg_nodes``    — searches ``label`` and ``aliases``
      (only ``status = 'active'`` nodes)
    """

    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    @property
    def name(self) -> str:
        return "rule-based-fallback"

    async def query(self, *, query: str, **kwargs: Any) -> RAGQueryResult:
        tokens = _QUERY_TOKEN_RE.findall(query)
        if not tokens:
            return RAGQueryResult(
                response="",
                provider=self.name,
                fallback=True,
            )

        limit = kwargs.get("limit", 5)
        tsquery_str = " & ".join(tokens[:10])

        sql = text(
            """
            SELECT source_type, source_id, snippet_text, rank
            FROM (
                SELECT 'data_source' AS source_type, id AS source_id,
                       COALESCE(title, '') || ' ' || COALESCE(abstract, '') AS snippet_text,
                       ts_rank(
                         to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(abstract, '')),
                         plainto_tsquery(:q)
                       ) AS rank
                FROM data_sources
                WHERE to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(abstract, ''))
                      @@ plainto_tsquery(:q)

                UNION ALL

                SELECT 'material' AS source_type, id AS source_id,
                       COALESCE(name, '') || ' ' || COALESCE(description, '') AS snippet_text,
                       ts_rank(
                         to_tsvector('english', COALESCE(name, '') || ' ' || COALESCE(description, '')),
                         plainto_tsquery(:q)
                       ) AS rank
                FROM materials
                WHERE to_tsvector('english', COALESCE(name, '') || ' ' || COALESCE(description, ''))
                      @@ plainto_tsquery(:q)

                UNION ALL

                SELECT 'kg_node' AS source_type, id AS source_id,
                       COALESCE(label, '') || ' ' || COALESCE(aliases, '') AS snippet_text,
                       ts_rank(
                         to_tsvector('english', COALESCE(label, '') || ' ' || COALESCE(aliases, '')),
                         plainto_tsquery(:q)
                       ) AS rank
                FROM kg_nodes
                WHERE status = 'active'
                  AND to_tsvector('english', COALESCE(label, '') || ' ' || COALESCE(aliases, ''))
                      @@ plainto_tsquery(:q)
            ) combined
            ORDER BY rank DESC
            LIMIT :limit
            """
        )
        result = await self._db.execute(
            sql,
            {"q": tsquery_str, "limit": limit},
        )
        rows = (await result.mappings()).all()

        references: list[dict[str, Any]] = []
        snippets: list[str] = []
        for row in rows:
            references.append(
                {
                    "source_type": row.get("source_type", ""),
                    "source_id": str(row.get("source_id", "")),
                    "score": float(row.get("rank", 0)),
                }
            )
            snippet = row.get("snippet_text", "")
            if snippet:
                snippets.append(snippet[:500])

        response = (
            f"Rule-based fallback: found {len(rows)} relevant results "
            f"for query '{query}'.\n\n" + "\n---\n".join(snippets)
            if snippets
            else f"No results found for query '{query}'."
        )

        return RAGQueryResult(
            response=response,
            references=references,
            provider=self.name,
            fallback=True,
        )

    async def ingest(self, *, text: str, source: str | None = None) -> str | None:
        """No-op for the fallback provider.  Always returns ``None``."""
        logger.debug(
            "RuleBasedFallbackProvider.ingest is a no-op (text=%d chars, source=%s)",
            len(text),
            source,
        )

    async def health(self) -> bool:
        """Healthy as long as the database is reachable."""
        try:
            await self._db.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("RuleBasedFallbackProvider health check failed", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# Provider selector — stateless health-check + try/except fallback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthStatus:
    """Immutable snapshot of the current RAG health state."""

    lightrag_healthy: bool
    active_provider: str


class RAGProviderSelector:
    """Auto-selects the best available RAG provider.

    Wraps a :class:`LightRAGProvider` and a
    :class:`RuleBasedFallbackProvider`.  Selection is **stateless** —
    every ``query()`` / ``ingest()`` call first attempts LightRAG and
    falls back to rule-based PG search on ``LightRAGClientError``.
    A ``check_health()`` probe is available for monitoring endpoints.

    Previous circuit-breaker state was per-request and never persisted,
    so it was removed in favour of this simpler pattern (NFM-1247).
    """

    def __init__(
        self,
        *,
        lightrag_client: LightRAGClient | None = None,
        db_session: AsyncSession,
    ) -> None:
        self._lightrag = LightRAGProvider(lightrag_client)
        self._fallback = RuleBasedFallbackProvider(db_session)

    async def check_health(self) -> HealthStatus:
        """Run a health check against the LightRAG sidecar.

        Returns a snapshot suitable for monitoring; does **not** influence
        provider selection (which is handled per-request via try/except).
        """
        lightrag_ok = await self._lightrag.health()
        return HealthStatus(
            lightrag_healthy=lightrag_ok,
            active_provider=self._lightrag.name if lightrag_ok else self._fallback.name,
        )

    @property
    def status(self) -> HealthStatus:
        """Return an optimistic status without performing a network check."""
        return HealthStatus(
            lightrag_healthy=True,
            active_provider=self._lightrag.name,
        )

    @property
    def active_provider(self) -> RAGProvider:
        """Return the primary (LightRAG) provider."""
        return self._lightrag

    async def query(self, *, query: str, **kwargs: Any) -> RAGQueryResult:
        """Query using LightRAG with automatic fallback on any failure.

        Broad ``except Exception`` (NFM-3368) ensures non-wrapped exceptions
        (JSONDecodeError, RuntimeError, asyncio.TimeoutError, etc.) also
        trigger the PG full-text fallback rather than propagating as 500.

        Raises HTTPException(503) only when BOTH providers fail. The full
        original exception detail is logged at WARNING level so operators
        can debug the outage; the user-facing detail is a clean message
        with no internal exception text.
        """
        try:
            return await self._lightrag.query(query=query, **kwargs)
        except Exception as lightrag_exc:
            # AC-1: full detail preserved server-side (status code, body snippet, etc.)
            logger.warning(
                "LightRAG query failed, falling back to rule-based: %s",
                lightrag_exc,
                exc_info=True,
            )
            try:
                return await self._fallback.query(query=query, **kwargs)
            except Exception as fallback_exc:
                logger.warning(
                    "Rule-based fallback query also failed: %s",
                    fallback_exc,
                    exc_info=True,
                )
                # AC-4: 503 when no results available due to service failure
                # AC-2: detail is clean user-facing text — no leaked exception
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Semantic search temporarily unavailable, "
                        "please use keyword search"
                    ),
                ) from fallback_exc

    async def ingest(self, *, text: str, source: str | None = None) -> str | None:
        """Ingest using LightRAG with automatic fallback on error.

        Returns the LightRAG ``track_id`` when the primary provider
        succeeds, or ``None`` on fallback.
        """
        try:
            return await self._lightrag.ingest(text=text, source=source)
        except LightRAGClientError:
            logger.warning("LightRAG ingest failed, falling back to rule-based")
            await self._fallback.ingest(text=text, source=source)
            return None
