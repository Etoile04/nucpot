"""Tests for health check endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app


@pytest.mark.asyncio
async def test_health_check_returns_ok() -> None:
    """Health endpoint returns status ok."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
