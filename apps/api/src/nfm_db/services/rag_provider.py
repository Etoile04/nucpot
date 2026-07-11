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
    async def ingest(self, *, text: str, source: str | None = None) -> None:
        """Ingest a document into the knowledge graph."""

    @abstractmethod
    async def health(self) -> bool:
        """Return True if the underlying service is healthy."""


# ---------------------------------------------------------------------------
# LightRAG provider
# ---------------------------------------------------------------------------


class LightRAGProvider(RAGProvider):
    """RAG provider that delegates to the LightRAG sidecar service."""

    def __init__(self, client: LightRAGClient | None = None) -> None:
        self._client = client or LightRAGClient()

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

    async def ingest(self, *, text: str, source: str | None = None) -> None:
        await self._client.ingest(text=text, file_source=source)

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
            references.append({
                "source_type": row.get("source_type", ""),
                "source_id": str(row.get("source_id", "")),
                "score": float(row.get("rank", 0)),
            })
            snippet = row.get("snippet_text", "")
            if snippet:
                snippets.append(snippet[:500])

        response = (
            f"Rule-based fallback: found {len(rows)} relevant results "
            f"for query '{query}'.\n\n"
            + "\n---\n".join(snippets)
            if snippets
            else f"No results found for query '{query}'."
        )

        return RAGQueryResult(
            response=response,
            references=references,
            provider=self.name,
            fallback=True,
        )

    async def ingest(self, *, text: str, source: str | None = None) -> None:
        """No-op for the fallback provider."""
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
# Provider selector with circuit-breaker semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthStatus:
    """Immutable snapshot of the current RAG health state."""

    lightrag_healthy: bool
    active_provider: str
    consecutive_failures: int
    failure_threshold: int


class RAGProviderSelector:
    """Auto-selects the best available RAG provider.

    Wraps a :class:`LightRAGProvider` and a
    :class:`RuleBasedFallbackProvider`.  When LightRAG passes health
    checks, it is used; once ``failure_threshold`` consecutive failures
    occur, the fallback takes over until LightRAG recovers.
    """

    def __init__(
        self,
        *,
        lightrag_client: LightRAGClient | None = None,
        db_session: AsyncSession,
        failure_threshold: int = 3,
    ) -> None:
        self._lightrag = LightRAGProvider(lightrag_client)
        self._fallback = RuleBasedFallbackProvider(db_session)
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._use_fallback = False

    async def check_health(self) -> HealthStatus:
        """Run a health check and update internal circuit-breaker state."""
        lightrag_ok = await self._lightrag.health()

        if lightrag_ok:
            self._consecutive_failures = 0
            self._use_fallback = False
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._use_fallback = True
                logger.warning(
                    "LightRAG unhealthy %d/%d consecutive failures — "
                    "switching to fallback provider",
                    self._consecutive_failures,
                    self._failure_threshold,
                )

        active = self._fallback.name if self._use_fallback else self._lightrag.name
        return HealthStatus(
            lightrag_healthy=lightrag_ok,
            active_provider=active,
            consecutive_failures=self._consecutive_failures,
            failure_threshold=self._failure_threshold,
        )

    @property
    def status(self) -> HealthStatus:
        """Return last-known health status without performing a check."""
        active = self._fallback.name if self._use_fallback else self._lightrag.name
        return HealthStatus(
            lightrag_healthy=self._consecutive_failures == 0,
            active_provider=active,
            consecutive_failures=self._consecutive_failures,
            failure_threshold=self._failure_threshold,
        )

    @property
    def active_provider(self) -> RAGProvider:
        """Return the currently active provider."""
        if self._use_fallback:
            return self._fallback
        return self._lightrag

    async def query(self, *, query: str, **kwargs: Any) -> RAGQueryResult:
        """Query using the active provider with automatic fallback."""
        await self.check_health()
        provider = self.active_provider
        try:
            return await provider.query(query=query, **kwargs)
        except LightRAGClientError:
            if not self._use_fallback:
                logger.warning("LightRAG query failed, falling back to rule-based")
                self._use_fallback = True
                return await self._fallback.query(query=query, **kwargs)
            raise

    async def ingest(self, *, text: str, source: str | None = None) -> None:
        """Ingest using the active provider with automatic fallback."""
        await self.check_health()
        provider = self.active_provider
        try:
            await provider.ingest(text=text, source=source)
        except LightRAGClientError:
            if not self._use_fallback:
                logger.warning("LightRAG ingest failed, falling back to rule-based")
                self._use_fallback = True
                await self._fallback.ingest(text=text, source=source)
            raise
