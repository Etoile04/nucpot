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
# Exception
# ---------------------------------------------------------------------------


#: Cap on the upstream ``response_body`` text attached to a
#: :class:`LightRAGClientError`. Bodies longer than this are truncated
#: with an explicit ``...[truncated]`` marker so the exception payload
#: stays bounded even when an upstream 500-page returns megabytes of
#: HTML or stack traces.
_MAX_RESPONSE_BODY_CHARS = 2000


def _truncate_response_body(body: str | None) -> str | None:
    """Cap an upstream response body at :data:`_MAX_RESPONSE_BODY_CHARS`.

    Returns ``None`` for ``None`` input (no body to attach). For an
    over-long body, returns the first ``_MAX_RESPONSE_BODY_CHARS``
    characters followed by an explicit ``...[truncated]`` marker so the
    operator can tell the body was clipped without losing the prefix
    that usually contains the actionable error.
    """
    if body is None:
        return None
    if len(body) <= _MAX_RESPONSE_BODY_CHARS:
        return body
    return body[:_MAX_RESPONSE_BODY_CHARS] + "...[truncated]"


class LightRAGClientError(Exception):
    """Raised when a LightRAG API call fails.

    Carries enriched diagnostic fields (NFM-3407) so the operator can
    cross-reference a blank/truncated user-facing error back to the
    real underlying cause in the server log:

    * ``original_type``     — ``type(exc).__name__`` of the originating
                              httpx / network exception, used to
                              distinguish ``ConnectError``,
                              ``ReadTimeout``, ``HTTPStatusError``, etc.
    * ``original_message``  — ``str(exc)`` of the originating exception,
                              preserved verbatim.
    * ``response_body``     — bounded upstream HTTP response body
                              (≤2000 chars + ``...[truncated]`` marker).
    * ``status_code``       — HTTP status code when the originating
                              exception carried one (``HTTPStatusError``).
    * ``request_id``        — UUID4 correlation key assigned by the API
                              layer; surfaced in both the log line and
                              the response payload.

    All five fields are **keyword-only** with ``None`` defaults so the
    original single-positional construction
    ``LightRAGClientError("msg")`` keeps working for existing callers
    and tests.
    """

    def __init__(
        self,
        message: str,
        *,
        original_type: str | None = None,
        original_message: str | None = None,
        response_body: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.original_type = original_type
        self.original_message = original_message
        self.response_body = response_body
        self.status_code = status_code
        self.request_id = request_id


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
        self.query_timeout = (
            query_timeout
            if query_timeout is not None
            else timeout
            if timeout is not None
            else _DEFAULT_QUERY_TIMEOUT
        )
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
        # NFM-3367: split the transport-level timeout so a stalled TCP
        # handshake cannot blow past the per-request query budget.
        #
        # ``read``/``write``/``pool`` are slaved to ``self.query_timeout`` —
        # not the legacy ``self.timeout`` — because the query budget is the
        # *binding* ceiling for a synchronous user request. When a caller
        # passes the legacy ``timeout=`` kwarg, ``self.query_timeout`` has
        # already absorbed that value (see ``__init__``), so the legacy
        # path keeps working too.
        #
        # ``connect`` is bounded at 5s so the TCP handshake itself cannot
        # consume the whole 8s budget; the remaining 3s is the per-request
        # read envelope.
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                connect=5.0,
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
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a text document into the LightRAG knowledge graph.

        Args:
            text: Document text content to ingest.
            file_source: Optional source identifier.
            request_id: Optional UUID4 correlation key. When set, any
                :class:`LightRAGClientError` raised by this call carries
                the same ``request_id`` so the operator log line and
                the API response payload can be cross-referenced.

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
                f"LightRAG ingest failed: [HTTPStatusError] HTTP "
                f"{exc.response.status_code} - {exc.response.text}",
                original_type=type(exc).__name__,
                original_message=str(exc),
                response_body=_truncate_response_body(exc.response.text),
                status_code=exc.response.status_code,
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG ingest failed: [{type(exc).__name__}] {exc}",
                original_type=type(exc).__name__,
                original_message=str(exc),
                request_id=request_id,
            ) from exc

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query(
        self,
        *,
        query: str,
        mode: str = "mix",
        include_references: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Query the LightRAG knowledge graph.

        Args:
            query: Natural language query.
            mode: Query mode (local, global, hybrid, mix, naive).
            include_references: Whether to include source references.
            request_id: Optional UUID4 correlation key (see :meth:`ingest`).

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
                f"LightRAG query failed: [HTTPStatusError] HTTP "
                f"{exc.response.status_code} - {exc.response.text}",
                original_type=type(exc).__name__,
                original_message=str(exc),
                response_body=_truncate_response_body(exc.response.text),
                status_code=exc.response.status_code,
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise LightRAGClientError(
                f"LightRAG query failed: [{type(exc).__name__}] {exc}",
                original_type=type(exc).__name__,
                original_message=str(exc),
                request_id=request_id,
            ) from exc

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
