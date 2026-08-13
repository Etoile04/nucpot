"""API endpoint tests for KG query routes (NFM-858).

Tests the three GET endpoints via the async_client (SQLite-backed).

NOTE: Tests are currently skipped because the kg_query_service API was
refactored in NFM-1142 and the /api/v1/kg/query/* endpoints were removed.
Tracked as a follow-up issue.
"""
from __future__ import annotations

import json
import uuid

import pytest

from nfm_db.models.kg import KGEdge, KGNode

pytestmark = pytest.mark.skip(reason="KG query endpoints removed in NFM-1142 refactor; tests need rewrite")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NODE_A = uuid.uuid4()
_NODE_B = uuid.uuid4()
_NODE_C = uuid.uuid4()
_NODE_D = uuid.uuid4()


@pytest.fixture
async def seed_graph(db_session) -> dict[str, uuid.UUID]:
    """Seed a small test graph: Material -> Property -> Experiment."""
    nodes = [
        KGNode(
            id=_NODE_A,
            node_type="Material",
            label="Uranium Dioxide",
            aliases=json.dumps(["UO2"]),
            properties={"formula": "UO2"},
            confidence=0.95,
            status="active",
        ),
        KGNode(
            id=_NODE_B,
            node_type="Property",
            label="Density",
            properties={"unit": "g/cm³"},
            confidence=0.9,
            status="active",
        ),
        KGNode(
            id=_NODE_C,
            node_type="Property",
            label="Melting Point",
            properties={"unit": "K"},
            confidence=0.85,
            status="active",
        ),
        KGNode(
            id=_NODE_D,
            node_type="Experiment",
            label="High-Temp Test 2025",
            properties={"method": "LFA"},
            confidence=0.8,
            status="active",
        ),
    ]
    for node in nodes:
        db_session.add(node)

    edges = [
        KGEdge(
            source_node_id=_NODE_A,
            target_node_id=_NODE_B,
            relation_type="hasProperty",
            confidence=0.9,
        ),
        KGEdge(
            source_node_id=_NODE_A,
            target_node_id=_NODE_C,
            relation_type="hasProperty",
            confidence=0.85,
        ),
        KGEdge(
            source_node_id=_NODE_D,
            target_node_id=_NODE_A,
            relation_type="measuredIn",
            confidence=0.8,
        ),
    ]
    for edge in edges:
        db_session.add(edge)

    await db_session.flush()
    return {
        "material": _NODE_A,
        "density": _NODE_B,
        "melting": _NODE_C,
        "experiment": _NODE_D,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/kg/query/property
# ---------------------------------------------------------------------------


class TestPropertyQueryEndpoint:
    """Tests for the property query endpoint."""

    @pytest.mark.asyncio
    async def test_empty_result(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"label": "nonexistent"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0
        assert body["data"]["nodes"] == []

    @pytest.mark.asyncio
    async def test_exact_label(self, async_client, db_session, seed_graph) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"label": "Uranium Dioxide"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["nodes"][0]["label"] == "Uranium Dioxide"

    @pytest.mark.asyncio
    async def test_fuzzy_label(self, async_client, db_session, seed_graph) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"label": "density", "fuzzy": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_filter_node_type(self, async_client, db_session, seed_graph) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"node_type": "Property"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        for node in data["nodes"]:
            assert node["node_type"] == "Property"

    @pytest.mark.asyncio
    async def test_invalid_node_type(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"node_type": "Bogus"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_pagination_limit(self, async_client, db_session, seed_graph) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/property", params={"limit": 2},
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]["nodes"]) <= 2


# ---------------------------------------------------------------------------
# GET /api/v1/kg/query/relation
# ---------------------------------------------------------------------------


class TestRelationQueryEndpoint:
    """Tests for the relation query endpoint."""

    @pytest.mark.asyncio
    async def test_outgoing_edges(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"source_node_id": str(ids["material"]), "direction": "outgoing"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_incoming_edges(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"target_node_id": str(ids["material"]), "direction": "incoming"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_filter_relation_type(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={
                "source_node_id": str(ids["material"]),
                "relation_type": "hasProperty",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        for edge in data["edges"]:
            assert edge["relation_type"] == "hasProperty"

    @pytest.mark.asyncio
    async def test_invalid_uuid_returns_422(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"source_node_id": "not-a-uuid"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_relation_type(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"relation_type": "nonexistent"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_nodes_populated(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/relation",
            params={"source_node_id": str(ids["material"]), "direction": "outgoing"},
        )
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["data"]["nodes"]}
        assert str(ids["material"]) in node_ids
        assert str(ids["density"]) in node_ids


# ---------------------------------------------------------------------------
# GET /api/v1/kg/query/path
# ---------------------------------------------------------------------------


class TestPathQueryEndpoint:
    """Tests for the path query endpoint."""

    @pytest.mark.asyncio
    async def test_direct_path(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(ids["material"]),
                "target_node_id": str(ids["density"]),
                "max_depth": 1,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["paths"][0]["length"] == 1

    @pytest.mark.asyncio
    async def test_two_hop_path(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(ids["experiment"]),
                "target_node_id": str(ids["density"]),
                "max_depth": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] >= 1
        assert data["paths"][0]["length"] == 2

    @pytest.mark.asyncio
    async def test_no_path(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        isolated = uuid.uuid4()
        db_session.add(
            KGNode(
                id=isolated,
                node_type="Material",
                label="Isolated",
                confidence=1.0,
                status="active",
            )
        )
        await db_session.flush()

        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(ids["material"]),
                "target_node_id": str(isolated),
                "max_depth": 3,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_invalid_source_uuid(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={"source_node_id": "bad", "target_node_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_target_uuid(self, async_client) -> None:
        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={"source_node_id": str(uuid.uuid4()), "target_node_id": "bad"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_params(self, async_client) -> None:
        resp = await async_client.get("/api/v1/kg/query/path")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_relation_type_filter(self, async_client, db_session, seed_graph) -> None:
        ids = seed_graph
        resp = await async_client.get(
            "/api/v1/kg/query/path",
            params={
                "source_node_id": str(ids["material"]),
                "target_node_id": str(ids["density"]),
                "max_depth": 1,
                "relation_types": "hasProperty",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1
