# nfm-node-client

Async Python client SDK for **resource nodes** in the NFMD 1+N data submission architecture.

This package is the W2 deliverable for the M2 data-submission sprint
(see `docs/technical-roadmap-nuclear-fuel-data-platform-1.7.md` §7.9.2).
A resource node uses it to register with the central hub, send
heartbeats, initiate upload sessions, and query sync status — all
behind a shared HTTP/2 connection pool with exponential backoff
retries.

## Installation

```bash
pip install nfm-node-client
```

## Quick start

```python
import asyncio
from nfm_node_client import Credentials, NfmNodeClient, NodeType


async def main() -> None:
    creds = Credentials(token="my-hub-token")
    client = NfmNodeClient(
        hub_url="https://hub.example.test",
        credentials=creds,
        heartbeat_interval=30.0,
    )
    try:
        # Register with the hub (returns the hub-assigned node_id).
        reg = await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        print(f"registered node_id={reg.node_id}")

        # Send a heartbeat (uses the cached node_id from register()).
        ack = await client.heartbeat()
        print(f"heartbeat ack={ack.ack}")

        # Initiate an upload session.
        session = await client.upload(
            data=b"hello",
            metadata={"source": "test"},
            file_name="hello.bin",
            total_size=5,
            chunk_size=5,
        )
        print(f"upload session_id={session.session_id}")

        # Query sync status.
        status = await client.get_sync_status()
        print(f"online={status.online} pending_downloads={status.pending_downloads}")
    finally:
        await client.close()


asyncio.run(main())
```

## Public API

- `NfmNodeClient(hub_url, credentials, *, heartbeat_interval=30.0, max_retries=3, backoff_base=0.5, backoff_max=30.0, pool_size=10, timeout=30.0)`
- `await client.register(*, name, node_type, api_endpoint, hub_node_id, public_key=None)`
- `await client.heartbeat(node_id=None)`
- `await client.upload(*, data, metadata, file_name, total_size, chunk_size, node_id=None)`
- `await client.get_sync_status(node_id=None)`
- `await client.start_heartbeat_loop()` / `await client.stop_heartbeat_loop()`
- `await client.close()`

Domain types: `ResourceNodeRegistration`, `HeartbeatResponse`, `UploadResult`, `SyncStatus`, `NodeType`, `Credentials`.

Exceptions (all subclasses of `NfmNodeClientError`): `RegistrationError`, `HeartbeatError`, `UploadError`, `SyncStatusError`, `RetriesExhaustedError`.

## Resilience

- **Connection pool:** shared `httpx.AsyncClient` with bounded
  `max_connections` and `max_keepalive_connections` (default 10).
- **Retry:** every public method retries up to `max_retries` times
  (default 3) on transient failures (5xx, 429, network errors) with
  exponential backoff (`backoff_base * 2**n`, capped at `backoff_max`).
- **Heartbeat loop:** optional background loop calls `heartbeat()` at
  the configured interval; transient errors are logged and the loop
  continues.

## Development

```bash
cd apps/nfm-node-client
pip install -e ".[dev]"
pytest            # run unit tests with coverage
ruff check .      # linter
mypy src          # type check
```

## References

- Contract spec §3.1.2 (数据汇交模块)
- Roadmap v1.7 §7.9.2 W2
- M2 data submission 1+N schema (NFM-2019): `hub_nodes`, `resource_nodes`, `upload_sessions`, `ingest_logs`
