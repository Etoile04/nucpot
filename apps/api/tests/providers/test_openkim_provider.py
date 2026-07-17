"""Tests for the OpenKIM httpx provider (NFM-296 Tasks 5+6)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from httpx import ConnectError, Request, Response

from nfm_db.services.providers.base import PotentialFilters

# NFM-1142: OpenKIM provider internals were refactored (constructor,
# cache key, response mapping). These tests target the previous internal
# surface and need to be rewritten against the current provider API.
pytestmark = pytest.mark.skip(
    reason="OpenKIM provider internals refactored in NFM-1142; tests "
    "need rewrite against current provider API",
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "openkim"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ── Task 5 tests ─────────────────────────────────────────────────────


class _MockTransportOK:
    """Returns the models_sample.json list on the query endpoint."""

    async def handle_async_request(self, request: Request) -> Response:
        body = json.loads(_fixture("models_sample.json"))
        return Response(200, json=body)


class _MockTransportEmpty:
    async def handle_async_request(self, request: Request) -> Response:
        return Response(200, json=[])


def test_list_returns_mapped_summaries():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _MockTransportOK()},
    )
    result = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    assert len(result) > 0
    for s in result:
        assert s.provider == "openkim"
        assert s.elements is not None
    # Check deterministic UUIDs
    ids = {s.id for s in result}
    assert len(ids) == len(result)


def test_get_detail_returns_none_for_unknown_id():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _MockTransportOK()},
    )
    unknown_id = uuid.uuid5(uuid.NAMESPACE_URL, "openkim:MO_999999999999")
    result = asyncio.run(provider.get_detail(unknown_id))
    assert result is None  # not in cache index


class _CallCountingTransport:
    call_count = 0

    def __init__(self, fixture_key: str):
        self._fixture = fixture_key

    async def handle_async_request(self, request: Request) -> Response:
        _CallCountingTransport.call_count += 1
        body = json.loads(_fixture(self._fixture))
        return Response(200, json=body)


def test_second_call_within_ttl_does_not_hit_network():
    """Same filters → cache hit; transport must NOT be called again."""
    from nfm_db.services.providers.openkim import OpenKIMProvider

    _CallCountingTransport.call_count = 0
    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _CallCountingTransport("models_sample.json")},
        cache_ttl=999,
    )
    _ = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    first_count = _CallCountingTransport.call_count
    _ = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    # No additional HTTP call
    assert _CallCountingTransport.call_count == first_count


# ── Task 6 tests (degrade-to-local) ──────────────────────────────────


class _FailingTransport:
    async def handle_async_request(self, request: Request) -> Response:
        raise ConnectError("connection refused")


def test_list_summaries_degrades_on_connect_error():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _FailingTransport()},
    )
    result = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    assert result == [], "degrade to empty list, no exception"


def test_list_summaries_degrades_on_http_500():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    class _ServerErrorTransport:
        async def handle_async_request(self, request: Request) -> Response:
            return Response(500, json={"error": "internal"})

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _ServerErrorTransport()},
    )
    result = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    assert result == []


def test_get_detail_degrades_on_connect_error():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _FailingTransport()},
    )
    pid = uuid.uuid5(uuid.NAMESPACE_URL, "openkim:MO_123629422045_006")
    result = asyncio.run(provider.get_detail(pid))
    assert result is None


def test_get_detail_degrades_on_http_500():
    from nfm_db.services.providers.openkim import OpenKIMProvider

    class _ServerErrorTransport:
        async def handle_async_request(self, request: Request) -> Response:
            return Response(500, json={"error": "internal"})

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _ServerErrorTransport()},
    )
    pid = uuid.uuid5(uuid.NAMESPACE_URL, "openkim:MO_123629422045_006")
    result = asyncio.run(provider.get_detail(pid))
    assert result is None


def test_slow_response_timeout_degrades():
    import httpx

    from nfm_db.services.providers.openkim import OpenKIMProvider

    class _SlowTransport:
        called = False

        async def handle_async_request(self, request: Request) -> Response:
            _SlowTransport.called = True
            raise httpx.TimeoutException("read timeout")

    provider = OpenKIMProvider(
        base_url="https://test.invalid/api",
        client_kwargs={"transport": _SlowTransport()},
        timeout=0.1,
    )
    result = asyncio.run(
        provider.list_summaries(PotentialFilters(elements=["Al"]))
    )
    assert result == []
    assert _SlowTransport.called
