"""Tests for nfm_node_client.pool — connection pool."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from nfm_node_client.pool import ConnectionPool


@pytest.mark.unit
def test_pool_default_size_is_10() -> None:
    """Default pool size matches the spec (10 connections)."""
    pool = ConnectionPool()
    assert pool.pool_size == 10


@pytest.mark.unit
def test_pool_custom_size() -> None:
    """ConnectionPool accepts custom pool size."""
    pool = ConnectionPool(pool_size=25)
    assert pool.pool_size == 25


@pytest.mark.unit
def test_pool_rejects_zero_size() -> None:
    """ConnectionPool rejects zero or negative pool size."""
    with pytest.raises(ValueError, match="pool_size"):
        ConnectionPool(pool_size=0)
    with pytest.raises(ValueError, match="pool_size"):
        ConnectionPool(pool_size=-3)


@pytest.mark.unit
def test_pool_httpx_limits() -> None:
    """ConnectionPool exposes httpx.Limits with max_connections matching pool_size."""
    pool = ConnectionPool(pool_size=8)
    limits = pool.to_httpx_limits()
    assert isinstance(limits, httpx.Limits)
    assert limits.max_connections == 8
    assert limits.max_keepalive_connections == 8


@pytest.mark.unit
def test_pool_holds_max_keepalive_equal_to_pool_size() -> None:
    """Keepalive pool is sized to match the connection pool (no idle churn)."""
    pool = ConnectionPool(pool_size=4)
    assert pool.to_httpx_limits().max_keepalive_connections == 4


@pytest.mark.unit
@pytest.mark.parametrize("pool_size", [1, 5, 10, 50, 100])
def test_pool_round_trip_for_various_sizes(pool_size: int) -> None:
    """Pool sizes round-trip cleanly into httpx.Limits."""
    pool = ConnectionPool(pool_size=pool_size)
    assert pool.to_httpx_limits().max_connections == pool_size


@pytest.mark.unit
async def test_pool_concurrent_requests_share_client() -> None:
    """ConnectionPool reuses a single httpx.AsyncClient instance."""
    pool = ConnectionPool(pool_size=5)
    client1 = pool.shared_client
    client2 = pool.shared_client
    assert client1 is client2
    # AsyncClient owns its own loop; only sanity check the type.
    assert isinstance(client1, httpx.AsyncClient)


@pytest.mark.unit
async def test_pool_close_is_idempotent() -> None:
    """Calling close() multiple times does not raise."""
    pool = ConnectionPool(pool_size=2)
    await pool.close()
    await pool.close()
    assert pool.is_closed is True


@pytest.mark.unit
async def test_pool_close_releases_client() -> None:
    """After close(), the underlying httpx client is closed."""
    pool = ConnectionPool(pool_size=2)
    client = pool.shared_client
    await pool.close()
    assert client.is_closed is True
