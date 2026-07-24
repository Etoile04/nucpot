"""Async HTTP client for the LightRAG sidecar service (NFM-741.4).

Wraps the LightRAG REST API (default port 9621):
  POST /documents/text  — ingest text documents
  POST /query           — semantic / graph queries
  POST /graph/query     — Cypher queries via AGE
  GET  /health          — service health check

Features:
  - Circuit breaker: stops calling after consecutive failures
  - Retry with exponential backoff: configurable retries
  - Graceful degradation: returns structured fallback instead of crashing

Configuration via environment variables:
  LIGHTRAG_BASE_URL    - Full base URL (default: http://lightrag:8001)
  NFM_LIGHTRAG_HOST   - LightRAG server host (fallback, default: "lightrag")
  NFM_LIGHTRAG_PORT   - LightRAG server port (fallback, default: 8001)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://lightrag:8001"
_DEFAULT_HOST = "lightrag"
_DEFAULT_PORT = 8001


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LightRAGClientError(Exception):
    """Raised when a LightRAG API call fails."""


class LightRAGUnavailableError(LightRAGClientError):
    """Raised when LightRAG is unreachable after retries and circuit breaker.

    Callers should catch this and return a graceful degradation response
    instead of propagating the exception to crash the NFM API.
    """

    def __init__(self, message: str, *, is_circuit_open: bool = False) -> None:
        super().__init__(message)
        self.is_circuit_open = is_circuit_open


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


@dataclass
class CircuitState:
    """Tracks circuit breaker state.

    Immutable update pattern — use `with_*` methods to create new states.
    """

    failure_count: int = 0
    is_open: bool = False
    last_failure_time: float = 0.0
    half_open_attempts: int = 0

    # Configurable thresholds
    failure_threshold: int = field(default=5)
    recovery_timeout: float = field(default=30.0)
    max_half_open_attempts: int = field(default=1)

    def with_success(self) -> CircuitState:
        return CircuitState(
            failure_count=0,
            is_open=False,
            last_failure_time=self.last_failure_time,
            half_open_attempts=0,
            failure_threshold=self.failure_threshold,
            recovery_timeout=self.recovery_timeout,
            max_half_open_attempts=self.max_half_open_attempts,
        )

    def with_failure(self) -> CircuitState:
        now = time.monotonic()
        new_count = self.failure_count + 1 if not self.is_open else self.failure_count
        should_open = new_count >= self.failure_threshold
        return CircuitState(
            failure_count=new_count,
            is_open=should_open,
            last_failure_time=now,
            half_open_attempts=self.half_open_attempts,
            failure_threshold=self.failure_threshold,
            recovery_timeout=self.recovery_timeout,
            max_half_open_attempts=self.max_half_open_attempts,
        )

    def allow_request(self) -> bool:
        if not self.is_open:
            return True
        elapsed = time.monotonic() - self.last_failure_time
        if elapsed >= self.recovery_timeout:
            return self.half_open_attempts < self.max_half_open_attempts
        return False

    def with_half_open_attempt(self) -> CircuitState:
        return CircuitState(
            failure_count=self.failure_count,
            is_open=self.is_open,
            last_failure_time=self.last_failure_time,
            half_open_attempts=self.half_open_attempts + 1,
            failure_threshold=self.failure_threshold,
            recovery_timeout=self.recovery_timeout,
            max_half_open_attempts=self.max_half_open_attempts,
        )


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Configurable retry with exponential backoff."""

    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    backoff_factor: float = 2.0

    def get_delay(self, attempt: int) -> float:
        delay = self.base_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)


# ---------------------------------------------------------------------------
# Per-request timeout config
# ---------------------------------------------------------------------------


@dataclass
class TimeoutConfig:
    """Per-request-type timeout configuration."""

    health: float = 5.0
    ingest: float = 60.0
    query: float = 120.0
    graph_query: float = 60.0

    def get(self, request_type: str) -> float:
        return getattr(self, request_type, 30.0)


# ---------------------------------------------------------------------------
# Fallback response builder
# ---------------------------------------------------------------------------


def _unavailable_response(
    operation: str,
    error: LightRAGUnavailableError,
) -> dict[str, Any]:
    return {
        "available": False,
        "message": f"LightRAG service unavailable: {error}",
        "operation": operation,
    }


# ---------------------------------------------------------------------------
# Module-level config helper
# ---------------------------------------------------------------------------


def is_lightrag_configured() -> bool:
    """Check if LightRAG host is configured in environment."""
    return bool(
        os.environ.get("LIGHTRAG_BASE_URL")
        or os.environ.get("NFM_LIGHTRAG_HOST")
    )


def _resolve_base_url() -> str:
    """Resolve base URL from environment variables."""
    explicit = os.environ.get("LIGHTRAG_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("NFM_LIGHTRAG_HOST", _DEFAULT_HOST)
    port = os.environ.get("NFM_LIGHTRAG_PORT", str(_DEFAULT_PORT))
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LightRAGClient:
    """Async HTTP client for the LightRAG sidecar service.

    Features circuit breaker, retry with exponential backoff,
    and graceful degradation.

    Usage::

        async with LightRAGClient() as client:
            result = await client.query_with_fallback(query="What is UO2?")
            if not result.get("available", True):
                # handle gracefully
                pass
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        timeout: float = 30.0,
        timeout_config: TimeoutConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
    ) -> None:
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host or port:
            h = host or os.environ.get("NFM_LIGHTRAG_HOST", _DEFAULT_HOST)
            p = port or int(os.environ.get("NFM_LIGHTRAG_PORT", str(_DEFAULT_PORT)))
            resolved_url = f"http://{h}:{p}"
        else:
            resolved_url = _resolve_base_url()

        self._base_url = resolved_url
        self._timeout = timeout
        self._timeout_config = timeout_config or TimeoutConfig()
        self._retry_policy = retry_policy or RetryPolicy()
        self._circuit = CircuitState(
            failure_threshold=circuit_threshold,
            recovery_timeout=circuit_recovery_timeout,
        )
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def circuit_state(self) -> CircuitState:
        return self._circuit

    # ------------------------------------------------------------------
    # Internal: retry + circuit breaker wrapper
    # ------------------------------------------------------------------

    async def _execute_with_resilience(
        self,
        method: str,
        path: str,
        request_type: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not self._circuit.allow_request():
            raise LightRAGUnavailableError(
                "Circuit breaker is open — LightRAG service is unreachable",
                is_circuit_open=True,
            )

        timeout = self._timeout_config.get(request_type)
        last_exc: Exception | None = None

        for attempt in range(self._retry_policy.max_retries + 1):
            if attempt > 0:
                delay = self._retry_policy.get_delay(attempt - 1)
                logger.warning(
                    "LightRAG retry %d/%d for %s %s (delay=%.1fs)",
                    attempt,
                    self._retry_policy.max_retries,
                    method,
                    path,
                    delay,
                )
                await asyncio.sleep(delay)

            try:
                response = await self._http_client.request(
                    method,
                    path,
                    timeout=timeout,
                    **kwargs,
                )
                self._circuit = self._circuit.with_success()
                return response
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "LightRAG %s %s failed (attempt %d/%d): %s",
                    method,
                    path,
                    attempt + 1,
                    self._retry_policy.max_retries + 1,
                    exc,
                )

        self._circuit = self._circuit.with_failure()
        raise LightRAGUnavailableError(
            f"LightRAG {method} {path} failed after {self._retry_policy.max_retries + 1} attempts: {last_exc}",
            is_circuit_open=self._circuit.is_open,
        ) from last_exc

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Check if the LightRAG service is healthy.

        Returns a dict with status info. Never raises — returns
        a fallback dict on any failure (graceful degradation).
        """
        try:
            response = await self._execute_with_resilience(
                "GET", "/health", request_type="health",
            )
            return response.json()
        except LightRAGUnavailableError as exc:
            return {
                "status": "unavailable",
                "available": False,
                "message": str(exc),
            }

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    async def ingest_document(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest a text document into the LightRAG knowledge graph.

        Args:
            text: Document text content to ingest.
            metadata: Optional metadata dict (file_source, etc.).

        Returns:
            Parsed JSON response from LightRAG.

        Raises:
            LightRAGUnavailableError: When service is unreachable.
        """
        payload: dict[str, Any] = {"text": text}
        if metadata:
            payload.update(metadata)

        try:
            response = await self._execute_with_resilience(
                "POST", "/documents/text", request_type="ingest", json=payload,
            )
            response.raise_for_status()
            return response.json()
        except LightRAGUnavailableError:
            raise
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG ingest failed: HTTP {exc.response.status_code}"
            ) from exc

    async def ingest(
        self,
        *,
        text: str,
        file_source: str | None = None,
    ) -> dict[str, Any]:
        """Legacy ingest method — delegates to ingest_document."""
        metadata: dict[str, Any] = {}
        if file_source is not None:
            metadata["file_source"] = file_source
        return await self.ingest_document(text=text, metadata=metadata)

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

        Raises LightRAGUnavailableError when service is unreachable.
        Use query_with_fallback() for graceful degradation.
        """
        payload: dict[str, Any] = {
            "query": query,
            "mode": mode,
            "include_references": include_references,
        }

        try:
            response = await self._execute_with_resilience(
                "POST", "/query", request_type="query", json=payload,
            )
            response.raise_for_status()
            return response.json()
        except LightRAGUnavailableError:
            raise
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG query failed: HTTP {exc.response.status_code}"
            ) from exc

    async def query_with_fallback(
        self,
        *,
        query: str,
        mode: str = "mix",
        include_references: bool = False,
    ) -> dict[str, Any]:
        """Query with graceful degradation.

        Returns {available: false, message: ...} when LightRAG is down
        instead of raising an exception.
        """
        try:
            return await self.query(
                query=query, mode=mode, include_references=include_references,
            )
        except LightRAGUnavailableError as exc:
            return _unavailable_response("query", exc)

    # ------------------------------------------------------------------
    # Graph Query (Cypher via AGE)
    # ------------------------------------------------------------------

    async def graph_query(self, cypher: str) -> dict[str, Any]:
        """Execute a Cypher query against the LightRAG knowledge graph via AGE.

        Args:
            cypher: Cypher query string.

        Returns:
            Parsed JSON response.

        Raises:
            LightRAGUnavailableError: When service is unreachable.
        """
        payload = {"query": cypher}

        try:
            response = await self._execute_with_resilience(
                "POST", "/graph/query", request_type="graph_query", json=payload,
            )
            response.raise_for_status()
            return response.json()
        except LightRAGUnavailableError:
            raise
        except httpx.HTTPStatusError as exc:
            raise LightRAGClientError(
                f"LightRAG graph_query failed: HTTP {exc.response.status_code}"
            ) from exc

    async def graph_query_with_fallback(self, cypher: str) -> dict[str, Any]:
        """Graph query with graceful degradation."""
        try:
            return await self.graph_query(cypher=cypher)
        except LightRAGUnavailableError as exc:
            return _unavailable_response("graph_query", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> LightRAGClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
