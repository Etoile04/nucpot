"""Tests for nfm_node_client.client — NfmNodeClient main API."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

from nfm_node_client import (
    Credentials,
    HeartbeatResponse,
    NfmNodeClient,
    NodeType,
    ResourceNodeRegistration,
    RetryPolicy,
    SyncStatus,
    UploadResult,
)
from nfm_node_client.exceptions import (
    HeartbeatError,
    RegistrationError,
    SyncStatusError,
    UploadError,
)
from nfm_node_client.retry import RetryPolicy as RetryPolicyType

from tests.conftest import (
    HUB_NODE_ID,
    HUB_URL,
    NODE_ID,
    TOKEN,
    make_client,
    make_credentials,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_payload(node_id: uuid.UUID = NODE_ID) -> dict[str, Any]:
    """Build a /register response body matching ResourceNodeRead."""
    return {
        "id": str(node_id),
        "hub_node_id": str(HUB_NODE_ID),
        "name": "obs-1",
        "node_type": "observatory",
        "api_endpoint": "https://obs-1.example.test",
        "public_key": None,
        "status": "active",
        "last_heartbeat": None,
        "offline_since": None,
        "sync_watermark": None,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }


def _heartbeat_payload() -> dict[str, Any]:
    """Build a /heartbeat response body."""
    return {
        "node_id": str(NODE_ID),
        "ack": True,
        "received_at": "2026-07-01T00:00:00Z",
    }


def _upload_payload(session_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Build an /upload response body matching UploadSessionRead."""
    return {
        "id": str(session_id or uuid.uuid4()),
        "resource_node_id": str(NODE_ID),
        "file_name": "data.bin",
        "total_size": 1024,
        "chunk_size": 256,
        "total_chunks": 4,
        "uploaded_chunks": 0,
        "resume_token": None,
        "sha256_full": None,
        "status": "pending",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
    }


def _sync_status_payload() -> dict[str, Any]:
    """Build a /sync-status response body."""
    return {
        "node_id": str(NODE_ID),
        "online": True,
        "last_heartbeat": "2026-07-01T00:00:00Z",
        "sync_watermark": "2026-07-01T00:00:00Z",
        "pending_uploads": 0,
        "pending_downloads": 3,
    }


def _auth_headers() -> dict[str, str]:
    """Expected Authorization headers."""
    return {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_client_requires_hub_url() -> None:
    """NfmNodeClient rejects empty hub_url."""
    with pytest.raises(ValueError, match="hub_url"):
        NfmNodeClient(hub_url="", credentials=make_credentials())


@pytest.mark.unit
def test_client_requires_credentials() -> None:
    """NfmNodeClient rejects missing credentials."""
    with pytest.raises(ValueError, match="credentials"):
        NfmNodeClient(hub_url=HUB_URL, credentials=None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_client_rejects_empty_token() -> None:
    """NfmNodeClient rejects empty bearer token."""
    with pytest.raises(ValueError, match="token"):
        NfmNodeClient(hub_url=HUB_URL, credentials=Credentials(token=""))


@pytest.mark.unit
def test_client_defaults() -> None:
    """NfmNodeClient defaults: 30s heartbeat interval, 3 retries, 10-pool size."""
    client = make_client()
    assert client.heartbeat_interval == 30.0
    assert isinstance(client.retry_policy, RetryPolicy)
    assert client.retry_policy.max_retries == 3
    assert client.pool_size == 10


@pytest.mark.unit
def test_client_accepts_overrides() -> None:
    """NfmNodeClient accepts custom heartbeat interval, retries, pool size."""
    client = make_client(
        heartbeat_interval=10.0,
        max_retries=5,
        pool_size=20,
    )
    assert client.heartbeat_interval == 10.0
    assert client.retry_policy.max_retries == 5
    assert client.pool_size == 20


@pytest.mark.unit
def test_client_rejects_negative_heartbeat_interval() -> None:
    """NfmNodeClient rejects non-positive heartbeat interval."""
    with pytest.raises(ValueError, match="heartbeat_interval"):
        make_client(heartbeat_interval=0.0)
    with pytest.raises(ValueError, match="heartbeat_interval"):
        make_client(heartbeat_interval=-1.0)


@pytest.mark.unit
def test_client_trims_trailing_slash_from_hub_url() -> None:
    """Hub URL strips trailing slash for clean URL composition."""
    client = make_client(hub_url=HUB_URL + "/")
    assert client.hub_url == HUB_URL


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_register_calls_hub_and_returns_node_id() -> None:
    """register() POSTs and returns a ResourceNodeRegistration with node_id."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(201, json=_register_payload())
    )
    client = make_client(transport=handler)
    try:
        result = await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
        )
    finally:
        await client.close()

    assert isinstance(result, ResourceNodeRegistration)
    assert result.node_id == NODE_ID
    assert result.hub_node_id == HUB_NODE_ID
    assert result.name == "obs-1"
    assert result.node_type == NodeType.OBSERVATORY


@pytest.mark.unit
async def test_register_sends_bearer_token() -> None:
    """register() includes the bearer token in the Authorization header."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.content.decode("utf-8"))
        return httpx.Response(201, json=_register_payload())

    client = make_client(transport=httpx.MockTransport(handler))
    try:
        await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
        )
    finally:
        await client.close()

    assert captured["headers"]["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.unit
async def test_register_posts_payload_to_hub_nodes_endpoint() -> None:
    """register() hits /api/v1/hub/nodes/register with the right payload."""
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content.decode("utf-8"))
        return httpx.Response(201, json=_register_payload())

    client = make_client(transport=httpx.MockTransport(handler))
    try:
        await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
            public_key="pk-xyz",
        )
    finally:
        await client.close()

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/v1/hub/nodes/register")
    body = captured["body"]
    assert body["name"] == "obs-1"
    assert body["node_type"] == "observatory"
    assert body["api_endpoint"] == "https://obs-1.example.test"
    assert body["hub_node_id"] == str(HUB_NODE_ID)
    assert body["public_key"] == "pk-xyz"


@pytest.mark.unit
async def test_register_raises_registration_error_on_4xx() -> None:
    """register() raises RegistrationError when the hub rejects the request."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(400, json={"detail": "invalid"})
    )
    client = make_client(transport=handler)
    try:
        with pytest.raises(RegistrationError) as exc_info:
            await client.register(
                name="obs-1",
                node_type=NodeType.OBSERVATORY,
                api_endpoint="https://obs-1.example.test",
                hub_node_id=HUB_NODE_ID,
            )
    finally:
        await client.close()
    assert exc_info.value.status_code == 400


@pytest.mark.unit
async def test_register_retries_on_5xx() -> None:
    """register() retries on 5xx, eventually succeeds."""
    attempts = 0
    responses = [
        httpx.Response(503, json={"detail": "down"}),
        httpx.Response(503, json={"detail": "down"}),
        httpx.Response(201, json=_register_payload()),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal attempts
        response = responses[attempts]
        attempts += 1
        return response

    client = make_client(
        transport=httpx.MockTransport(handler),
        max_retries=3,
        backoff_base=0.001,
        backoff_max=0.01,
    )
    try:
        result = await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
        )
    finally:
        await client.close()
    assert result.node_id == NODE_ID
    assert attempts == 3


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_heartbeat_returns_ack_response() -> None:
    """heartbeat() returns a HeartbeatResponse with ack=True."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(200, json=_heartbeat_payload())
    )
    client = make_client(transport=handler)
    try:
        result = await client.heartbeat(node_id=NODE_ID)
    finally:
        await client.close()
    assert isinstance(result, HeartbeatResponse)
    assert result.node_id == NODE_ID
    assert result.ack is True


@pytest.mark.unit
async def test_heartbeat_requires_node_id() -> None:
    """heartbeat() raises if no node_id available (not registered)."""
    client = make_client()
    with pytest.raises(ValueError, match="node_id"):
        await client.heartbeat()
    await client.close()


@pytest.mark.unit
async def test_heartbeat_remembers_node_id_after_register() -> None:
    """Once registered, heartbeat() can be called without explicit node_id."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(
            201 if req.method == "POST" else 200,
            json=_register_payload() if req.method == "POST" else _heartbeat_payload(),
        )
    )
    client = make_client(transport=handler)
    try:
        await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
        )
        result = await client.heartbeat()
    finally:
        await client.close()
    assert result.node_id == NODE_ID


@pytest.mark.unit
async def test_heartbeat_raises_heartbeat_error_on_5xx() -> None:
    """heartbeat() raises HeartbeatError when the hub is down."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(500, json={"detail": "boom"})
    )
    client = make_client(
        transport=handler,
        max_retries=1,
        backoff_base=0.001,
        backoff_max=0.01,
    )
    try:
        with pytest.raises(HeartbeatError):
            await client.heartbeat(node_id=NODE_ID)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_upload_returns_session_result() -> None:
    """upload() returns UploadResult with session_id from the hub."""
    sid = uuid.uuid4()
    handler = httpx.MockTransport(
        lambda req: httpx.Response(201, json=_upload_payload(sid))
    )
    client = make_client(transport=handler)
    try:
        result = await client.upload(
            node_id=NODE_ID,
            data=b"hello",
            metadata={"source": "test"},
            file_name="hello.bin",
            total_size=5,
            chunk_size=5,
        )
    finally:
        await client.close()
    assert isinstance(result, UploadResult)
    assert result.session_id == sid
    assert result.resource_node_id == NODE_ID
    assert result.total_size == 5
    assert result.total_chunks == 1


@pytest.mark.unit
async def test_upload_requires_data_and_size() -> None:
    """upload() validates data/required kwargs."""
    client = make_client()
    with pytest.raises(ValueError, match="file_name"):
        await client.upload(
            node_id=NODE_ID,
            data=b"x",
            metadata={},
            file_name="",
            total_size=1,
            chunk_size=1,
        )
    await client.close()


@pytest.mark.unit
async def test_upload_raises_upload_error_on_4xx() -> None:
    """upload() raises UploadError when the hub rejects."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(413, json={"detail": "too big"})
    )
    client = make_client(
        transport=handler,
        max_retries=1,
        backoff_base=0.001,
        backoff_max=0.01,
    )
    try:
        with pytest.raises(UploadError) as exc_info:
            await client.upload(
                node_id=NODE_ID,
                data=b"x",
                metadata={},
                file_name="data.bin",
                total_size=1,
                chunk_size=1,
            )
    finally:
        await client.close()
    assert exc_info.value.status_code == 413


# ---------------------------------------------------------------------------
# get_sync_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_sync_status_returns_status() -> None:
    """get_sync_status() returns a SyncStatus with online/watermark info."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(200, json=_sync_status_payload())
    )
    client = make_client(transport=handler)
    try:
        result = await client.get_sync_status(node_id=NODE_ID)
    finally:
        await client.close()
    assert isinstance(result, SyncStatus)
    assert result.node_id == NODE_ID
    assert result.online is True
    assert result.pending_downloads == 3


@pytest.mark.unit
async def test_get_sync_status_raises_sync_status_error_on_5xx() -> None:
    """get_sync_status() raises SyncStatusError when the hub errors."""
    handler = httpx.MockTransport(
        lambda req: httpx.Response(503, json={"detail": "down"})
    )
    client = make_client(
        transport=handler,
        max_retries=1,
        backoff_base=0.001,
        backoff_max=0.01,
    )
    try:
        with pytest.raises(SyncStatusError):
            await client.get_sync_status(node_id=NODE_ID)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Background heartbeat loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_start_heartbeat_loop_fires_at_interval() -> None:
    """Background heartbeat loop calls heartbeat() at the configured interval."""
    calls = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_heartbeat_payload())

    client = make_client(transport=httpx.MockTransport(handler), heartbeat_interval=0.05)
    try:
        # Replace the heartbeat coroutine with a counter.
        async def counting_heartbeat() -> HeartbeatResponse:
            nonlocal calls
            calls += 1
            return HeartbeatResponse(
                node_id=NODE_ID,
                ack=True,
                received_at="now",
            )

        client._heartbeat = counting_heartbeat  # type: ignore[method-assign]
        await client.start_heartbeat_loop()
        await asyncio.sleep(0.18)  # ~3 ticks at 0.05s interval
        await client.stop_heartbeat_loop()
    finally:
        await client.close()
    assert calls >= 2


@pytest.mark.unit
async def test_start_heartbeat_loop_is_idempotent() -> None:
    """start_heartbeat_loop() is a no-op if already running."""
    client = make_client(heartbeat_interval=0.05)
    try:
        await client.start_heartbeat_loop()
        await client.start_heartbeat_loop()  # should not raise
        await client.stop_heartbeat_loop()
    finally:
        await client.close()


@pytest.mark.unit
async def test_stop_heartbeat_loop_without_start_is_safe() -> None:
    """stop_heartbeat_loop() before start_heartbeat_loop() is a no-op."""
    client = make_client()
    try:
        await client.stop_heartbeat_loop()
    finally:
        await client.close()


@pytest.mark.unit
async def test_heartbeat_loop_swallows_errors_until_stopped() -> None:
    """Heartbeat loop continues after transient errors and stops cleanly."""
    calls = 0

    async def flaky_heartbeat() -> HeartbeatResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HeartbeatError("hub hiccup", status_code=503)
        return HeartbeatResponse(node_id=NODE_ID, ack=True, received_at="now")

    client = make_client(heartbeat_interval=0.05)
    try:
        client._heartbeat = flaky_heartbeat  # type: ignore[method-assign]
        await client.start_heartbeat_loop()
        await asyncio.sleep(0.18)
        await client.stop_heartbeat_loop()
    finally:
        await client.close()
    assert calls >= 2


# ---------------------------------------------------------------------------
# Close & lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_client_close_is_idempotent() -> None:
    """close() may be called multiple times."""
    client = make_client()
    await client.close()
    await client.close()


@pytest.mark.unit
async def test_client_close_without_register_is_safe() -> None:
    """close() works even if register/heartbeat were never called."""
    client = make_client()
    await client.close()


@pytest.mark.unit
async def test_client_repr_includes_hub_url() -> None:
    """__repr__ includes the hub URL for debugging."""
    client = make_client()
    try:
        text = repr(client)
    finally:
        await client.close()
    assert HUB_URL in text
    assert "NfmNodeClient" in text


# ---------------------------------------------------------------------------
# Integration: register → heartbeat → upload → get_sync_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_full_lifecycle_round_trip() -> None:
    """End-to-end: register, then heartbeat, upload, get_sync_status."""
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if req.method == "POST" and "/register" in req.url.path:
            return httpx.Response(201, json=_register_payload())
        if req.method == "POST" and "/heartbeat" in req.url.path:
            return httpx.Response(200, json=_heartbeat_payload())
        if req.method == "POST" and "/upload" in req.url.path:
            return httpx.Response(201, json=_upload_payload())
        if req.method == "GET" and "/sync-status" in req.url.path:
            return httpx.Response(200, json=_sync_status_payload())
        return httpx.Response(404, json={"detail": "not found"})

    client = make_client(transport=httpx.MockTransport(handler))
    try:
        registered = await client.register(
            name="obs-1",
            node_type=NodeType.OBSERVATORY,
            api_endpoint="https://obs-1.example.test",
            hub_node_id=HUB_NODE_ID,
        )
        assert registered.node_id == NODE_ID

        ack = await client.heartbeat()
        assert ack.ack is True

        upload = await client.upload(
            data=b"hello",
            metadata={"source": "test"},
            file_name="hello.bin",
            total_size=5,
            chunk_size=5,
        )
        assert upload.resource_node_id == NODE_ID

        status = await client.get_sync_status()
        assert status.node_id == NODE_ID
    finally:
        await client.close()
    assert call_count == 4
