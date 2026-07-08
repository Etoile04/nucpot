"""Integration tests for GET /api/v1/health endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_check_returns_ok(async_client) -> None:
    """GET /health should return {"status": "ok"}."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_check_response_body_keys(async_client) -> None:
    """Response should contain exactly one key: status."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"status"}
    assert isinstance(body["status"], str)


@pytest.mark.asyncio
async def test_health_check_post_not_allowed(async_client) -> None:
    """POST /health should return 405 Method Not Allowed."""
    response = await async_client.post("/api/v1/health")
    assert response.status_code == 405
