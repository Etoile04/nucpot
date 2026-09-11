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
    """Unified result from any RAG provider.

    NFM-4539 RAG-B AC-7: ``was_cached`` propagates the LightRAG sidecar's
    cache-hit hint through the provider chain so the access_log can
    distinguish Tier-1 (cached) vs Tier-2 (fresh) latency on the AC-8
    dashboard.  Only the primary LightRAG provider populates it; the
    rule-based fallback never consults the LLM cache, so it stays False.

    NFM-4734 §3 / AC-2: ``fallback_reason`` is the first-class
    machine-readable code that lets the API route project the reason
    onto the ``FallbackInfo.reason`` field without inspecting
    ``original_error``.  ``"none"`` is the steady state; ``"semantic_timeout"``
    surfaces a LightRAGClientError (including the read-timeout path);
    ``"semantic_empty"`` would be set by an API-layer policy that
    escalates an empty-but-successful semantic hit to the ILIKE rescue
    path (not currently used at the selector layer — see api/v1/lightrag.py).
    ``original_error`` carries the verbatim upstream exception text so
    the access_log row and the UI badge can correlate on it.
    """

    response: str
    references: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    fallback: bool = False
    was_cached: bool = False
    fallback_reason: str = "none"
    original_error: str | None = None


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
        # NFM-4539 RAG-B: forward ``mode`` / ``include_references`` to the
        # sidecar so the route's per-request knobs still apply when we
        # route through ``RAGProviderSelector``.  ``LightRAGClient.query``
        # accepts these as explicit kwargs (see services/lightrag_client.py
        # line 334); the previous ``**kwargs``-only signature silently
        # dropped them on the floor.  ``limit`` is reserved for the
        # rule-based fallback's ts_rank cap — it is not part of the
        # LightRAG wire protocol so we strip it before the call.
        client_kwargs: dict[str, Any] = {
            k: v for k, v in kwargs.items() if k in ("mode", "include_references")
        }
        result = await self._client.query(
            query=query,
            **client_kwargs,
        )
        # NFM-4539 RAG-B AC-7: ``was_cached`` is grounded in the sidecar's
        # own ``cached`` / ``cache_hit`` hint (NFM-4522: ``dict.get`` does
        # not coerce JSON null, hence the ``or`` chain).  The fallback
        # provider never returns cache hits so its default stays False.
        was_cached = bool(result.get("cached") or result.get("cache_hit"))
        return RAGQueryResult(
            response=result.get("response", ""),
            references=result.get("references", []),
            entities=result.get("entities", []),
            relationships=result.get("relationships", []),
            provider=self.name,
            was_cached=was_cached,
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
        # NFM-4593: ``AsyncResult.mappings()`` is a SYNC method in SQLAlchemy 2.0
        # — it returns a ``MappingResult`` iterator wrapper directly, NOT a
        # coroutine.  Awaiting it (the previous code) raised
        # ``TypeError: object MappingResult can't be used in 'await' expression``
        # on every prod query that fell through to the rule-based rescue path.
        # The bug predates NFM-4539 (commit 6d3fcafa2, NFM-1244) but only surfaced
        # in production after PR #1285 routed ``/lightrag/query`` through
        # ``RAGProviderSelector`` — pre-#1285 the route's
        # ``except LightRAGClientError`` branch swallowed errors and returned a
        # degraded answer without ever invoking ``RuleBasedFallbackProvider.query``.
        rows = result.mappings().all()

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
        """Query using LightRAG with automatic fallback on error.

        NFM-4734 §3 / AC-2: when the LightRAG sidecar raises
        :class:`LightRAGClientError` (incl. read-timeout) the selector
        surfaces the original error on the result and stamps
        ``fallback_reason='semantic_timeout'``.  The API route projects
        both onto the response envelope without re-parsing strings.
        """
        try:
            return await self._lightrag.query(query=query, **kwargs)
        except LightRAGClientError as exc:
            logger.warning(
                "LightRAG query failed (%s), falling back to rule-based",
                exc,
            )
            fallback = await self._fallback.query(query=query, **kwargs)
            # Project the reason + original_error onto the rule-based
            # result so the API route can stamp them on FallbackInfo
            # without re-inspecting ``original_error``.  We rebuild the
            # frozen dataclass rather than mutating because RAGQueryResult
            # is ``frozen=True``.
            return RAGQueryResult(
                response=fallback.response,
                references=fallback.references,
                entities=fallback.entities,
                relationships=fallback.relationships,
                provider=fallback.provider,
                fallback=fallback.fallback,
                was_cached=fallback.was_cached,
                fallback_reason="semantic_timeout",
                original_error=str(exc),
            )

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
