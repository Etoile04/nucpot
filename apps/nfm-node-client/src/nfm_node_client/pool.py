"""Connection pool for the nfm_node_client SDK.

A thin wrapper around :class:`httpx.Limits` so the public API stays
predictable and the SDK owns a single shared :class:`httpx.AsyncClient`
instance per :class:`NfmNodeClient`.
"""

from __future__ import annotations

import httpx


class ConnectionPool:
    """Represents a bounded HTTP connection pool for the hub API.

    The pool size bounds both the maximum number of concurrent connections
    and the size of the keepalive pool, so an idle client doesn't churn
    sockets as it warms up.
    """

    def __init__(self, pool_size: int = 10) -> None:
        if pool_size <= 0:
            raise ValueError(f"pool_size must be > 0, got {pool_size}")
        self._pool_size = pool_size
        self._client: httpx.AsyncClient | None = None

    @property
    def pool_size(self) -> int:
        """Maximum number of concurrent HTTP connections."""
        return self._pool_size

    def to_httpx_limits(self) -> httpx.Limits:
        """Return the httpx Limits object for this pool."""
        return httpx.Limits(
            max_connections=self._pool_size,
            max_keepalive_connections=self._pool_size,
        )

    @property
    def shared_client(self) -> httpx.AsyncClient:
        """Return the lazily-constructed shared AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient(limits=self.to_httpx_limits())
        return self._client

    @property
    def is_closed(self) -> bool:
        """Whether the underlying client has been closed."""
        return self._client is not None and self._client.is_closed

    async def close(self) -> None:
        """Close the underlying httpx client. Idempotent."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


__all__ = ["ConnectionPool"]
