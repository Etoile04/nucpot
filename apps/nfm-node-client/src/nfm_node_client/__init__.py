"""nfm_node_client — Python client SDK for resource nodes in the 1+N architecture.

This package provides an async client (``NfmNodeClient``) so a resource
node can register with the hub, send heartbeats, upload data, and
query sync status. Every public method is wrapped in an exponential
backoff retry policy (3 retries by default) and shares a single
``httpx.AsyncClient`` connection pool.

Quick start::

    import asyncio
    from nfm_node_client import Credentials, NfmNodeClient, NodeType

    async def main():
        creds = Credentials(token="my-hub-token")
        client = NfmNodeClient("https://hub.example.test", creds)
        try:
            reg = await client.register(
                name="obs-1",
                node_type=NodeType.OBSERVATORY,
                api_endpoint="https://obs-1.example.test",
                hub_node_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
            ack = await client.heartbeat()
            print(ack)
        finally:
            await client.close()

    asyncio.run(main())
"""

from __future__ import annotations

from nfm_node_client.client import NfmNodeClient
from nfm_node_client.exceptions import (
    HeartbeatError,
    NfmNodeClientError,
    RegistrationError,
    RetriesExhaustedError,
    SyncStatusError,
    UploadError,
)
from nfm_node_client.pool import ConnectionPool
from nfm_node_client.retry import RetryPolicy, compute_backoff_delay, retry_async
from nfm_node_client.types import (
    Credentials,
    HeartbeatResponse,
    NodeType,
    ResourceNodeRegistration,
    SyncStatus,
    UploadResult,
)


__version__ = "0.1.0"


__all__ = [
    "ConnectionPool",
    "Credentials",
    "HeartbeatError",
    "HeartbeatResponse",
    "NfmNodeClient",
    "NfmNodeClientError",
    "NodeType",
    "RegistrationError",
    "ResourceNodeRegistration",
    "RetriesExhaustedError",
    "RetryPolicy",
    "SyncStatus",
    "SyncStatusError",
    "UploadError",
    "UploadResult",
    "__version__",
    "compute_backoff_delay",
    "retry_async",
]
