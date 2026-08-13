"""Transport contracts and HTTP implementation for Hub synchronization.

The sync engine depends on this small transport surface instead of replacing
private I/O methods in tests.  A fake transport can be injected for fast unit
coverage; ``HttpHubTransport`` is used by real resource-node processes.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from nfm_node_client.exceptions import NfmNodeClientError
from nfm_node_client.offline_queue import PendingOperation


class HubTransportError(NfmNodeClientError):
    """Transport failure while talking to a Hub."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HubTransport(Protocol):
    """Minimal I/O contract required by ``SyncEngine``."""

    async def fetch_all_records(self) -> list[dict[str, Any]]: ...

    async def fetch_incremental_records(self, since: int) -> list[dict[str, Any]]: ...

    async def push_operation(self, operation: PendingOperation) -> int: ...

    async def close(self) -> None: ...


class HttpHubTransport:
    """HTTP transport for the Hub's authoritative sync API."""

    def __init__(
        self,
        *,
        hub_url: str,
        node_id: str,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not hub_url.strip():
            raise ValueError("hub_url is required")
        if not node_id.strip():
            raise ValueError("node_id is required")
        self._base_url = hub_url.rstrip("/")
        self._node_id = node_id
        self._headers = dict(headers or {})
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    @property
    def node_id(self) -> str:
        return self._node_id

    async def fetch_all_records(self) -> list[dict[str, Any]]:
        """Fetch all records from the Hub."""
        response = await self._request("GET", self._sync_path(), params={"since": 0})
        data = response.json().get("data", {})
        return list(data.get("items", []))

    async def fetch_incremental_records(self, since: int) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", self._sync_path(), params={"since": since}
        )
        data = response.json().get("data", {})
        return list(data.get("items", []))

    async def push_operation(self, operation: PendingOperation) -> int:
        operation_id = self._operation_id(operation)
        try:
            operation_uuid = uuid.UUID(operation_id)
        except ValueError:
            operation_uuid = uuid.uuid5(uuid.NAMESPACE_URL, operation_id)
        payload = {
            "operation_id": str(operation_uuid),
            "op_type": operation.op_type.value,
            "entity_type": operation.entity_type,
            "entity_id": operation.entity_id,
            "payload": operation.payload,
            "priority": operation.priority,
        }
        response = await self._request("POST", self._sync_path(), json=payload)
        body = response.json()
        data = body.get("data", body)
        return int(data.get("watermark", data.get("sync_id", 0)))

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise HubTransportError(str(exc)) from exc
        if response.status_code >= 400:
            raise HubTransportError(
                f"Hub returned {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        return response

    def _sync_path(self) -> str:
        return f"/api/v1/hub/nodes/{self._node_id}/sync-data"

    @staticmethod
    def _operation_id(operation: PendingOperation) -> str:
        if operation.row_id is not None:
            return str(operation.row_id)
        return f"{operation.entity_type}:{operation.entity_id}:{operation.op_type.value}"

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["HubTransport", "HubTransportError", "HttpHubTransport"]
