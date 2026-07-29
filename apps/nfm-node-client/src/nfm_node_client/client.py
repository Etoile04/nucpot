"""Main NfmNodeClient — the user-facing resource-node client.

Each method (``register``, ``heartbeat``, ``upload``, ``get_sync_status``)
invokes the hub API through a shared connection pool with exponential
backoff retries.

The class is structured so that the same retry helper wraps every call,
so AC-4 (3 retries with backoff on transient failures) is enforced
uniformly across the public surface.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from nfm_node_client.exceptions import (
    HeartbeatError,
    RegistrationError,
    SyncStatusError,
    UploadError,
)
from nfm_node_client.pool import ConnectionPool
from nfm_node_client.retry import RetryPolicy, retry_async
from nfm_node_client.types import (
    Credentials,
    HeartbeatResponse,
    NodeType,
    ResourceNodeRegistration,
    SyncStatus,
    UploadResult,
)


T = TypeVar("T")
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_HEARTBEAT_INTERVAL = 30.0
_DEFAULT_POOL_SIZE = 10
DEFAULT_BASE_PATH = "/api/v1/hub/nodes"
_LOGGER = logging.getLogger("nfm_node_client")


def _is_retryable_response(status: int) -> bool:
    """Retry on 5xx and 429."""
    return status >= 500 or status == 429


class NfmNodeClient:
    """Async client for a resource node to talk to its hub.

    Construction stores the hub URL and credentials; the connection pool
    is created lazily on first use so creating a client is cheap.
    """

    def __init__(
        self,
        hub_url: str,
        credentials: Credentials,
        *,
        heartbeat_interval: float = _DEFAULT_HEARTBEAT_INTERVAL,
        retry_policy: RetryPolicy | None = None,
        max_retries: int | None = None,
        backoff_base: float | None = None,
        backoff_max: float | None = None,
        pool_size: int = _DEFAULT_POOL_SIZE,
        timeout: float = _DEFAULT_TIMEOUT,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not hub_url or not hub_url.strip():
            raise ValueError("hub_url is required")
        if credentials is None:
            raise ValueError("credentials is required")
        if not credentials.token or not credentials.token.strip():
            raise ValueError("credentials.token must be non-empty")
        if heartbeat_interval <= 0:
            raise ValueError(f"heartbeat_interval must be > 0, got {heartbeat_interval}")

        self._hub_url = hub_url.rstrip("/")
        self._credentials = credentials
        self._heartbeat_interval = float(heartbeat_interval)
        self.retry_policy: RetryPolicy = self._build_retry_policy(
            retry_policy=retry_policy,
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        self._pool = ConnectionPool(pool_size=pool_size)
        self._timeout = timeout
        self._external_client = http_client
        self._owns_client = http_client is None
        self._node_id: uuid.UUID | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_retry_policy(
        *,
        retry_policy: RetryPolicy | None,
        max_retries: int | None,
        backoff_base: float | None,
        backoff_max: float | None,
    ) -> RetryPolicy:
        if retry_policy is not None:
            if any(p is not None for p in (max_retries, backoff_base, backoff_max)):
                raise ValueError(
                    "Pass either retry_policy= or the individual max_retries / "
                    "backoff_base / backoff_max overrides, not both."
                )
            return retry_policy
        kwargs: dict[str, float] = {}
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        if backoff_base is not None:
            kwargs["backoff_base"] = backoff_base
        if backoff_max is not None:
            kwargs["backoff_max"] = backoff_max
        return RetryPolicy(**kwargs)

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def hub_url(self) -> str:
        """Base URL of the hub (no trailing slash)."""
        return self._hub_url

    @property
    def credentials(self) -> Credentials:
        """Bearer credentials used for hub API calls."""
        return self._credentials

    @property
    def heartbeat_interval(self) -> float:
        """Heartbeat interval in seconds for the background loop."""
        return self._heartbeat_interval

    @property
    def pool_size(self) -> int:
        """Maximum number of concurrent HTTP connections."""
        return self._pool.pool_size

    @property
    def node_id(self) -> uuid.UUID | None:
        """The node_id assigned by the hub after registration, if any."""
        return self._node_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _http_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client (external if injected)."""
        if self._external_client is not None:
            return self._external_client
        return self._pool.shared_client

    def _auth_headers(self) -> dict[str, str]:
        """Return the auth headers for the next request."""
        return self._credentials.auth_headers()

    def _request_kwargs(self) -> dict[str, Any]:
        """Common kwargs for httpx request calls."""
        return {
            "headers": self._auth_headers(),
            "timeout": self._timeout,
        }

    # ------------------------------------------------------------------
    # Public API — register
    # ------------------------------------------------------------------

    async def register(
        self,
        *,
        name: str,
        node_type: NodeType,
        api_endpoint: str,
        hub_node_id: uuid.UUID,
        public_key: str | None = None,
    ) -> ResourceNodeRegistration:
        """Register this resource node with the hub.

        Returns a :class:`ResourceNodeRegistration` containing the
        hub-assigned ``node_id``. After a successful registration the
        client caches the node_id so subsequent heartbeat / upload /
        sync-status calls don't need to pass it explicitly.
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        if not api_endpoint or not api_endpoint.strip():
            raise ValueError("api_endpoint is required")
        if node_type is None:
            raise ValueError("node_type is required")

        body = {
            "hub_node_id": str(hub_node_id),
            "name": name,
            "node_type": node_type.value,
            "api_endpoint": api_endpoint,
            "public_key": public_key,
        }

        async def post() -> httpx.Response:
            response = await self._http_client().post(
                f"{DEFAULT_BASE_PATH}/register",
                json=body,
                **self._request_kwargs(),
            )
            if _is_retryable_response(response.status_code):
                raise RegistrationError(
                    f"hub returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response

        response = await retry_async(post, self.retry_policy)
        if response.status_code >= 400:
            raise RegistrationError(
                f"register failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        payload = response.json()
        registration = ResourceNodeRegistration.from_api(payload)
        self._node_id = registration.node_id
        _LOGGER.info(
            "registered resource node node_id=%s hub_node_id=%s",
            registration.node_id,
            registration.hub_node_id,
        )
        return registration

    # ------------------------------------------------------------------
    # Public API — heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(
        self,
        node_id: uuid.UUID | None = None,
    ) -> HeartbeatResponse:
        """Send a heartbeat ping to the hub.

        If ``node_id`` is omitted, the value cached from a previous
        ``register()`` call is used. Raises ``ValueError`` if no node_id
        is available.
        """
        effective_node_id = node_id or self._node_id
        if effective_node_id is None:
            raise ValueError(
                "node_id is required (call register() first or pass node_id)"
            )

        async def post() -> httpx.Response:
            response = await self._http_client().post(
                f"{DEFAULT_BASE_PATH}/{effective_node_id}/heartbeat",
                **self._request_kwargs(),
            )
            if _is_retryable_response(response.status_code):
                raise HeartbeatError(
                    f"hub returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response

        response = await retry_async(post, self.retry_policy)
        if response.status_code >= 400:
            raise HeartbeatError(
                f"heartbeat failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return HeartbeatResponse.from_api(response.json())

    async def start_heartbeat_loop(self) -> None:
        """Start a background task that calls heartbeat() at the configured interval.

        Idempotent: calling this twice does not start a second loop.
        """
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._stop_event = asyncio.Event()
        interval = self._heartbeat_interval
        stop_event = self._stop_event

        async def loop() -> None:
            assert stop_event is not None
            while not stop_event.is_set():
                try:
                    await self.heartbeat()
                except Exception as exc:  # noqa: BLE001 - heartbeat is best-effort
                    _LOGGER.warning("heartbeat failed (will retry): %s", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue

        self._heartbeat_task = asyncio.create_task(loop())

    async def stop_heartbeat_loop(self) -> None:
        """Stop the background heartbeat loop. Safe to call when not running."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._heartbeat_task is not None:
            try:
                await asyncio.wait_for(self._heartbeat_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._heartbeat_task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._heartbeat_task = None
        self._stop_event = None

    # ------------------------------------------------------------------
    # Public API — upload
    # ------------------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        metadata: dict[str, Any],
        file_name: str,
        total_size: int,
        chunk_size: int,
        node_id: uuid.UUID | None = None,
    ) -> UploadResult:
        """Initiate an upload session on the hub.

        ``data`` here describes the file metadata; the actual chunked
        body transfer is handled by the ``nfm_node_client.upload_queue``
        module (W5-6 per §7.9.2 of the roadmap). This method creates the
        :class:`UploadSessionRead` record on the hub and returns the
        session metadata so callers can resume the chunked upload.
        """
        if not file_name or not file_name.strip():
            raise ValueError("file_name is required")
        if total_size is None or total_size <= 0:
            raise ValueError(f"total_size must be > 0, got {total_size}")
        if chunk_size is None or chunk_size <= 0:
            raise ValueError(f"chunk_size must be > 0, got {chunk_size}")

        effective_node_id = node_id or self._node_id
        if effective_node_id is None:
            raise ValueError("node_id is required (call register() first)")

        total_chunks = (total_size + chunk_size - 1) // chunk_size
        body = {
            "resource_node_id": str(effective_node_id),
            "file_name": file_name,
            "total_size": total_size,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "metadata": metadata,
        }

        async def post() -> httpx.Response:
            response = await self._http_client().post(
                f"{DEFAULT_BASE_PATH}/{effective_node_id}/upload",
                json=body,
                **self._request_kwargs(),
            )
            if _is_retryable_response(response.status_code):
                raise UploadError(
                    f"hub returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response

        response = await retry_async(post, self.retry_policy)
        if response.status_code >= 400:
            raise UploadError(
                f"upload init failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return UploadResult.from_api(response.json())

    # ------------------------------------------------------------------
    # Public API — get_sync_status
    # ------------------------------------------------------------------

    async def get_sync_status(
        self,
        node_id: uuid.UUID | None = None,
    ) -> SyncStatus:
        """Query the hub for this node's sync status."""
        effective_node_id = node_id or self._node_id
        if effective_node_id is None:
            raise ValueError("node_id is required (call register() first)")

        async def get() -> httpx.Response:
            response = await self._http_client().get(
                f"{DEFAULT_BASE_PATH}/{effective_node_id}/sync-status",
                **self._request_kwargs(),
            )
            if _is_retryable_response(response.status_code):
                raise SyncStatusError(
                    f"hub returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
            return response

        response = await retry_async(get, self.retry_policy)
        if response.status_code >= 400:
            raise SyncStatusError(
                f"sync-status failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return SyncStatus.from_api(response.json())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Stop the heartbeat loop and close the underlying HTTP client.

        Idempotent: calling close() twice is a no-op.
        """
        if self._closed:
            return
        await self.stop_heartbeat_loop()
        if self._owns_client:
            await self._pool.close()
        self._closed = True

    def __repr__(self) -> str:
        return f"NfmNodeClient(hub_url={self._hub_url!r})"


__all__ = ["NfmNodeClient"]
