"""Integration tests for /api/v1/viz endpoints.

The viz routes delegate to ontology_service functions that read from
external data.  We mock those service calls to isolate the HTTP layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nfm_db.schemas.viz import Node, Relationship, NvlResponse, VizStatsResponse


# ---------------------------------------------------------------------------
# Sample data used across multiple tests
# ---------------------------------------------------------------------------

_SAMPLE_NODES = [
    Node(
        id="test-alpha",
        name="Alpha Element",
        classes=["Element", "Metal"],
        properties={"atomic_number": "1"},
    ),
    Node(
        id="test-beta",
        name="Beta Compound",
        classes=["Compound", "Oxide"],
        properties={"formula": "BO"},
    ),
]

_SAMPLE_RELATIONSHIPS = [
    Relationship(
        id="rel-test-1",
        source="test-alpha",
        target="test-beta",
        type="COMPOSES",
    ),
]

_SAMPLE_NVL_RESPONSE = NvlResponse(
    nodes=_SAMPLE_NODES,
    relationships=_SAMPLE_RELATIONSHIPS,
)

_SAMPLE_STATS_RESPONSE = VizStatsResponse(
    total_nodes=2,
    total_relationships=1,
    class_counts={"Element": 1, "Metal": 1, "Compound": 1, "Oxide": 1},
)


# ---------------------------------------------------------------------------
# GET /api/v1/viz/nvl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_returns_data(mock_get_nvl, async_client) -> None:
    """Unfiltered request returns the full NVL response."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl")
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert len(body["relationships"]) == 1
    mock_get_nvl.assert_awaited_once_with(
        class_filter=None,
        search_term=None,
        max_nodes=None,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_with_class_filter(mock_get_nvl, async_client) -> None:
    """Class filter is passed through to the service."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl", params={"class": "Metal"})
    assert response.status_code == 200
    mock_get_nvl.assert_awaited_once_with(
        class_filter="Metal",
        search_term=None,
        max_nodes=None,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_with_search_term(mock_get_nvl, async_client) -> None:
    """Search term is passed through to the service."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl", params={"search": "uranium"})
    assert response.status_code == 200
    mock_get_nvl.assert_awaited_once_with(
        class_filter=None,
        search_term="uranium",
        max_nodes=None,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_with_max_nodes(mock_get_nvl, async_client) -> None:
    """max_nodes query param is passed to the service."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl", params={"max_nodes": 10})
    assert response.status_code == 200
    mock_get_nvl.assert_awaited_once_with(
        class_filter=None,
        search_term=None,
        max_nodes=10,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_with_all_params(mock_get_nvl, async_client) -> None:
    """All query parameters passed together."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get(
        "/api/v1/viz/nvl",
        params={"class": "Element", "search": "alpha", "max_nodes": 5},
    )
    assert response.status_code == 200
    mock_get_nvl.assert_awaited_once_with(
        class_filter="Element",
        search_term="alpha",
        max_nodes=5,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_empty_result(mock_get_nvl, async_client) -> None:
    """Service returning empty nodes/relationships serializes correctly."""
    empty_response = NvlResponse(nodes=[], relationships=[])
    mock_get_nvl.return_value = empty_response

    response = await async_client.get("/api/v1/viz/nvl")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == []
    assert body["relationships"] == []


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_invalid_max_nodes_rejects(mock_get_nvl, async_client) -> None:
    """max_nodes=0 should fail validation (ge=1)."""
    response = await async_client.get("/api/v1/viz/nvl", params={"max_nodes": 0})
    assert response.status_code == 422
    mock_get_nvl.assert_not_awaited()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_node_structure(mock_get_nvl, async_client) -> None:
    """Response nodes contain id, name, classes, and properties."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl")
    body = response.json()
    node = body["nodes"][0]
    assert "id" in node
    assert "name" in node
    assert "classes" in node
    assert "properties" in node


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_nvl_data", new_callable=AsyncMock)
async def test_get_nvl_relationship_structure(mock_get_nvl, async_client) -> None:
    """Response relationships contain id, source, target, and type."""
    mock_get_nvl.return_value = _SAMPLE_NVL_RESPONSE

    response = await async_client.get("/api/v1/viz/nvl")
    body = response.json()
    rel = body["relationships"][0]
    assert "id" in rel
    assert "source" in rel
    assert "target" in rel
    assert "type" in rel


# ---------------------------------------------------------------------------
# GET /api/v1/viz/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_viz_stats", new_callable=AsyncMock)
async def test_get_viz_stats_returns_data(mock_get_stats, async_client) -> None:
    """Stats endpoint returns ontology statistics."""
    mock_get_stats.return_value = _SAMPLE_STATS_RESPONSE

    response = await async_client.get("/api/v1/viz/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_nodes"] == 2
    assert body["total_relationships"] == 1
    assert body["class_counts"]["Element"] == 1
    mock_get_stats.assert_awaited_once()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.viz.get_viz_stats", new_callable=AsyncMock)
async def test_get_viz_stats_empty_ontology(mock_get_stats, async_client) -> None:
    """Stats for empty ontology returns zero counts."""
    empty_stats = VizStatsResponse(
        total_nodes=0,
        total_relationships=0,
        class_counts={},
    )
    mock_get_stats.return_value = empty_stats

    response = await async_client.get("/api/v1/viz/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_nodes"] == 0
    assert body["total_relationships"] == 0
    assert body["class_counts"] == {}
