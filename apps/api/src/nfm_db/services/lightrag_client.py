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
_DEFAULT_TIMEOUT = 60.0


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
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host or os.environ.get("NFM_LIGHTRAG_HOST", _DEFAULT_HOST)
        self.port = port or int(os.environ.get("NFM_LIGHTRAG_PORT", str(_DEFAULT_PORT)))
        self.timeout = timeout
        self._base_url = f"http://{self.host}:{self.port}"
        self._http_client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self.timeout,
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
            response = await self._http_client.get("/health")
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
