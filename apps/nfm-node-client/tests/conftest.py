"""Shared pytest fixtures for nfm_node_client tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from nfm_node_client import Credentials, NfmNodeClient


HUB_URL = "https://hub.example.test"
TOKEN = "test-bearer-token-1234567890"
NODE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
HUB_NODE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def make_credentials() -> Credentials:
    """Default credentials for tests."""
    return Credentials(token=TOKEN)


def make_client(
    transport: httpx.MockTransport | None = None,
    *,
    hub_url: str = HUB_URL,
    **kwargs: Any,
) -> NfmNodeClient:
    """Build a NfmNodeClient with optional mock transport."""
    http_client = httpx.AsyncClient(
        base_url=hub_url,
        transport=transport,
        timeout=2.0,
    )
    return NfmNodeClient(
        hub_url=hub_url,
        credentials=make_credentials(),
        http_client=http_client,
        **kwargs,
    )


@pytest.fixture
def hub_url() -> str:
    """Hub base URL fixture."""
    return HUB_URL


@pytest.fixture
def credentials() -> Credentials:
    """Default credentials fixture."""
    return make_credentials()


@pytest.fixture
def node_id() -> uuid.UUID:
    """Default node_id fixture."""
    return NODE_ID


@pytest.fixture
def hub_node_id() -> uuid.UUID:
    """Default hub_node_id fixture."""
    return HUB_NODE_ID


@pytest.fixture
def mock_transport() -> Iterator[httpx.MockTransport]:
    """httpx MockTransport with no handlers — tests register handlers as needed."""
    transport = httpx.MockTransport(lambda req: httpx.Response(501))
    yield transport
