"""Async HTTP client for the LightRAG sidecar service (NFM-862).

Wraps the LightRAG REST API (default port 9621):
  POST /documents/text  — ingest text documents
  POST /query           — semantic / graph queries
  GET  /health          — service health check

Configuration via environment variables:
  NFM_LIGHTRAG_HOST  - LightRAG server host (default: "localhost")
  NFM_LIGHTRAG_PORT  - LightRAG server port (default: 9621)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9621

# ---------------------------------------------------------------------------
# Timeouts (NFM-2565)
# ---------------------------------------------------------------------------
# Read and write paths have very different latency budgets, so they no longer
# share a single constant.
#
#   query  — serves a *synchronous user request* (api/v1/kg.py semantic search).
#            The caller already degrades to Postgres full-text search on
#            LightRAGClientError, so a short ceiling costs nothing but the
#            LightRAG answer; a long one costs the user a blank screen. The
#            previous shared 60s meant a stalled sidecar blocked the browser for
#            a full minute before the (fast, working) fallback ran — the real
#            cause of the long-standing "LightRAG query timeout" reports.
#
#   ingest — runs in a fire-and-forget background task
#            (kg_lightrag_sync.fire_ingest_to_lightrag). Nobody is waiting, and
#            entity extraction over a large document legitimately takes minutes.
#            Cutting this to seconds would turn working ingests into failures.
_DEFAULT_QUERY_TIMEOUT = 8.0
_DEFAULT_INGEST_TIMEOUT = 300.0

# Retained for backward compatibility: callers that pass ``timeout=`` explicitly
# still override both paths, and the transport-level default keeps the old
# value for any request that specifies neither.
_DEFAULT_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Env-var overrides (NFM-3404 / NFM-3425 — ADR §2.1 single source of truth)
# ---------------------------------------------------------------------------
# ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` binds the read/write/pool ceiling for the
# query path (defaulting to ``_DEFAULT_QUERY_TIMEOUT`` below); the connect
# ceiling was previously hardcoded at 5 s and now reads from
# ``NFM_LIGHTRAG_QUERY_CONNECT_S``. Both fall back to the constants above
# when the env vars are unset, preserving pre-NFM-3404 behaviour for
# existing callers and tests.
_ENV_QUERY_TIMEOUT_S = "NFM_LIGHTRAG_QUERY_TIMEOUT_S"
_ENV_QUERY_CONNECT_S = "NFM_LIGHTRAG_QUERY_CONNECT_S"
_DEFAULT_QUERY_CONNECT_S = 5.0


def _read_env_float(name: str, default: float) -> float:
    """Read a float from ``os.environ[name]``, falling back to ``default``.

    Empty / whitespace strings, ``None``, and non-numeric values all fall
    through to ``default``. This matches the contract documented in
    ADR-NFM-3404 §2.1: env vars are the source of truth, but the module
    constants are the safe fallback for callers that explicitly clear the
    environment.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "LightRAG env override %s=%r is not a float; using default %s",
            name,
            raw,
            default,
        )
        return default


def _resolve_query_timeout(
    explicit_query: float | None,
    explicit_legacy: float | None,
) -> float:
    """Resolution chain for the query read budget.

    Explicit ``query_timeout=`` wins over the legacy ``timeout=`` kwarg,
    which wins over the ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` env var, which wins
    over the module constant ``_DEFAULT_QUERY_TIMEOUT``. Each step is
    optional; an unset env var or empty string falls straight through to
    the next level (see ``_read_env_float``).
    """
    if explicit_query is not None:
        return explicit_query
    if explicit_legacy is not None:
        return explicit_legacy
    return _read_env_float(_ENV_QUERY_TIMEOUT_S, _DEFAULT_QUERY_TIMEOUT)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class LightRAGClientError(Exception):
    """Raised when a LightRAG API call fails."""


# ---------------------------------------------------------------------------
# Module-level config helper
# ---------------------------------------------------------------------------


def is_lightrag_configured() -> bool:
    """Check if LightRAG host is configured in environment."""
    return bool(os.environ.get("NFM_LIGHTRAG_HOST"))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LightRAGClient:
    """Async HTTP client for the LightRAG sidecar service.

    Usage::

        client = LightRAGClient()  # reads NFM_LIGHTRAG_HOST/PORT from env
        healthy = await client.health_check()
        result = await client.ingest(text="...", file_source="doc.pdf")
        answer = await client.query(query="What is UO2?")
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
        query_timeout: float | None = None,
        ingest_timeout: float | None = None,
    ) -> None:
        """Construct a client.

        Args:
            host: LightRAG host; falls back to ``NFM_LIGHTRAG_HOST``.
            port: LightRAG port; falls back to ``NFM_LIGHTRAG_PORT``.
            timeout: Legacy single-value override. When given it applies to
                **both** query and ingest, preserving the pre-NFM-2565
                behaviour for existing callers and tests.
            query_timeout: Per-request ceiling for :meth:`query` and
                :meth:`health_check`. Defaults to 8s.
            ingest_timeout: Per-request ceiling for :meth:`ingest`.
                Defaults to 300s.
        """
        self.host = host or os.environ.get("NFM_LIGHTRAG_HOST", _DEFAULT_HOST)
        self.port = port or int(os.environ.get("NFM_LIGHTRAG_PORT", str(_DEFAULT_PORT)))

        # An explicit ``timeout=`` collapses both paths onto that value.
        # NFM-3404 §2.1: when neither kwarg is passed, the query budget also
        # honours ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` (falls back to the module
        # constant ``_DEFAULT_QUERY_TIMEOUT`` if unset).
        self.query_timeout = _resolve_query_timeout(query_timeout, timeout)
        self.ingest_timeout = (
            ingest_timeout
            if ingest_timeout is not None
            else timeout
            if timeout is not None
            else _DEFAULT_INGEST_TIMEOUT
        )
        # ``self.timeout`` stays the transport-level default so attribute reads
        # in existing code keep working.
        self.timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT

        self._base_url = f"http://{self.host}:{self.port}"
        # NFM-3367 / NFM-3404: split the transport-level timeout so a stalled
        # TCP handshake cannot blow past the per-request query budget.
        #
        # ``read``/``write``/``pool`` are slaved to ``self.query_timeout`` —
        # not the legacy ``self.timeout`` — because the query budget is the
        # *binding* ceiling for a synchronous user request. When a caller
        # passes the legacy ``timeout=`` kwarg, ``self.query_timeout`` has
        # already absorbed that value (see ``__init__``), so the legacy
        # path keeps working too.
        #
        # ``connect`` is bounded so the TCP handshake itself cannot consume
        # the whole query budget; it honours ``NFM_LIGHTRAG_QUERY_CONNECT_S``
        # (ADR §2.1) and falls back to ``_DEFAULT_QUERY_CONNECT_S`` (5 s).
        self._connect_timeout = _read_env_float(
            _ENV_QUERY_CONNECT_S, _DEFAULT_QUERY_CONNECT_S
        )
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=self._connect_timeout,
                read=self.query_timeout,
                write=self.query_timeout,
                pool=self.query_timeout,
            ),
        )

    @property
    def base_url(self) -> str:
        """The base URL for the LightRAG service."""
        return self._base_url

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if the LightRAG service is healthy.

        Returns True if the service responds with HTTP 200, False otherwise.
        Connection errors and non-200 responses both return False.
        """
        try:
            response = await self._http_client.get(
                "/health",
                timeout=self.query_timeout,
            )
            return response.status_code == 200
        except httpx.HTTPError:
            logger.warning(
                "LightRAG health check failed: host=%s, port=%d",
                self.host,
                self.port,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    async def ingest(
        self,
        *,
        text: str,
        file_source: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a text document into the LightRAG knowledge graph.

        Args:
            text: Document text content to ingest.
            file_source: Optional source identifier.

        Returns:
            Parsed JSON response from LightRAG (includes track_id).

        Raises:
            LightRAGClientError: On server errors or connection failures.
        """
        payload: dict[str, Any] = {"text": text}
        if file_source is not None:
            payload["file_source"] = file_source

        try:
            response = await self._http_client.post(
                "/documents/text",
                json=payload,
                timeout=self.ingest_timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG ingest failed: HTTP {exc.response.status_code} - {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(f"LightRAG ingest failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        *,
        query: str,
        mode: str = "mix",
        include_references: bool = False,
    ) -> dict[str, Any]:
        """Query the LightRAG knowledge graph.

        Args:
            query: Natural language query.
            mode: Query mode (local, global, hybrid, mix, naive).
            include_references: Whether to include source references.

        Returns:
            Parsed JSON response with answer and optional references.

        Raises:
            LightRAGClientError: On server errors or connection failures.
        """
        payload: dict[str, Any] = {
            "query": query,
            "mode": mode,
            "include_references": include_references,
        }

        try:
            response = await self._http_client.post(
                "/query",
                json=payload,
                timeout=self.query_timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG query failed: HTTP {exc.response.status_code} - {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(f"LightRAG query failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> LightRAGClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
