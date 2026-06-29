"""Tests for NVL visualization API endpoints (TDD: RED phase)."""

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app


@pytest.mark.asyncio
async def test_get_nvl_returns_valid_structure() -> None:
    """GET /api/v1/viz/nvl returns NVL JSON with nodes and relationships."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/viz/nvl")

    assert response.status_code == 200
    data = response.json()

    # Verify NVL structure
    assert "nodes" in data
    assert "relationships" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["relationships"], list)


@pytest.mark.asyncio
async def test_get_nvl_filters_by_class() -> None:
    """GET /api/v1/viz/nvl?class=Metal filters nodes by class subtree."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/viz/nvl?class=Metal")

    assert response.status_code == 200
    data = response.json()

    # All nodes should belong to Metal class or its subclasses
    for node in data["nodes"]:
        assert "Metal" in node.get("classes", [])


@pytest.mark.asyncio
async def test_get_nvl_filters_by_search_term() -> None:
    """GET /api/v1/viz/nvl?search=Uranium filters nodes by search term."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/viz/nvl?search=Uranium")

    assert response.status_code == 200
    data = response.json()

    # All nodes should contain the search term in name or properties
    for node in data["nodes"]:
        name = node.get("name", "").lower()
        assert "uranium" in name


@pytest.mark.asyncio
async def test_get_nvl_limits_max_nodes() -> None:
    """GET /api/v1/viz/nvl?max_nodes=500 limits node count."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/viz/nvl?max_nodes=500")

    assert response.status_code == 200
    data = response.json()

    # Node count should not exceed max_nodes
    assert len(data["nodes"]) <= 500


@pytest.mark.asyncio
async def test_get_viz_stats_returns_ontology_statistics() -> None:
    """GET /api/v1/viz/stats returns ontology statistics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/viz/stats")

    assert response.status_code == 200
    data = response.json()

    # Verify stats structure
    assert "total_nodes" in data
    assert "total_relationships" in data
    assert "class_counts" in data
    assert isinstance(data["class_counts"], dict)


@pytest.mark.asyncio
async def test_viz_endpoints_have_cors_headers() -> None:
    """Visualization endpoints have CORS middleware configured."""
    # CORS is configured globally in main.py
    # Check that the middleware is present
    from fastapi.middleware.cors import CORSMiddleware

    cors_middleware_found = False
    for middleware in app.user_middleware:
        if middleware.cls == CORSMiddleware:
            cors_middleware_found = True
            # Verify CORS allows localhost:3000 for development
            assert "http://localhost:3000" in middleware.kwargs.get("allow_origins", [])
            break

    assert cors_middleware_found, "CORS middleware not configured"


@pytest.mark.asyncio
@pytest.mark.performance
async def test_get_nvl_response_time_under_2s() -> None:
    """Full ontology response time is under 2 seconds."""
    import time

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start = time.time()
        response = await client.get("/api/v1/viz/nvl")
        elapsed = time.time() - start

    assert response.status_code == 200
    assert elapsed < 2.0, f"Response time {elapsed:.2f}s exceeds 2s threshold"
