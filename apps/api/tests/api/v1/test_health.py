"""Integration tests for GET /api/v1/health endpoint."""

from __future__ import annotations

import pytest

from monitoring.worker_health import worker_health


@pytest.mark.asyncio
async def test_health_check_returns_ok(async_client) -> None:
    """GET /health should return status ok with monitoring fields."""
    worker_health.reset()
    try:
        response = await async_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["consecutive_failures"] == 0
    finally:
        worker_health.reset()


@pytest.mark.asyncio
async def test_health_check_response_body_keys(async_client) -> None:
    """Response should contain the 4 worker_health snapshot keys."""
    worker_health.reset()
    try:
        response = await async_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {
            "status",
            "consecutive_failures",
            "last_success_at",
            "last_error",
        }
        assert isinstance(body["status"], str)
    finally:
        worker_health.reset()


@pytest.mark.asyncio
async def test_health_check_post_not_allowed(async_client) -> None:
    """POST /health should return 405 Method Not Allowed."""
    response = await async_client.post("/api/v1/health")
    assert response.status_code == 405
