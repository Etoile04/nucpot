"""Tests for KG Graph API endpoint (NFM-1280).

Tests for GET /api/v1/kg/graph covering:
- Focal node resolution (UUID, type:label, bare label, case-insensitive)
- Depth boundary enforcement (1..3)
- Status filtering (active, all)
- Missing focal (404)
- Cycles and self-loops
- Multi-edges
- Response shape contract
- Hard caps on nodes/edges
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.kg_graph import router
from nfm_db.database import get_db
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_graph import (
    KGSubgraphNode,
    build_neighborhood_subgraph,
    resolve_focal_node,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_A_UUID = uuid.UUID("a0000001-0000-0000-0000-000000000001")
_B_UUID = uuid.UUID("a0000001-0000-0000-0000-000000000002")
_C_UUID = uuid.UUID("a0000001-0000-0000-0000-000000000003")
_D_UUID = uuid.UUID("a0000001-0000-0000-0000-000000000004")
_E_UUID = uuid.UUID("a0000001-0000-0000-0000-000000000005")


def _make_node(
    node_id: uuid.UUID = _A_UUID,
    label: str = "ZrO2",
    node_type: str = "Material",
    status: str = "active",
    confidence: float = 0.95,
    source_id: uuid.UUID | None = None,
) -> KGNode:
    return KGNode(
        id=node_id,
        node_type=node_type,
        label=label,
        status=status,
        confidence=confidence,
        properties={"formula": label},
        source_id=source_id,
    )


def _make_edge(
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation_type: str = "hasProperty",
    confidence: float = 0.9,
) -> KGEdge:
    return KGEdge(
        source_node_id=source_id,
        target_node_id=target_id,
        relation_type=relation_type,
        confidence=confidence,
        properties={},
    )


def _make_client(db_override=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    if db_override is not None:
        app.dependency_overrides[get_db] = db_override
    return TestClient(app)


async def _seed_linear_chain(session: AsyncSession) -> KGNode:
    nodes = [
        _make_node(node_id=_A_UUID, label="ZrO2"),
        _make_node(node_id=_B_UUID, label="MeltingPoint"),
        _make_node(node_id=_C_UUID, label="Experiment1"),
        _make_node(node_id=_D_UUID, label="Condition1"),
    ]
    edges = [
        _make_edge(_A_UUID, _B_UUID, "hasProperty"),
        _make_edge(_B_UUID, _C_UUID, "relatedTo"),
        _make_edge(_C_UUID, _D_UUID, "measuredIn"),
    ]
    for n in nodes:
        session.add(n)
    for e in edges:
        session.add(e)
    await session.flush()
    return nodes[0]


async def _seed_triangle(session: AsyncSession) -> KGNode:
    nodes = [
        _make_node(node_id=_A_UUID, label="NodeA"),
        _make_node(node_id=_B_UUID, label="NodeB"),
        _make_node(node_id=_C_UUID, label="NodeC"),
    ]
    edges = [
        _make_edge(_A_UUID, _B_UUID, "hasProperty"),
        _make_edge(_A_UUID, _C_UUID, "hasProperty"),
        _make_edge(_B_UUID, _C_UUID, "relatedTo"),
    ]
    for n in nodes:
        session.add(n)
    for e in edges:
        session.add(e)
    await session.flush()
    return nodes[0]


async def _seed_self_loop(session: AsyncSession) -> KGNode:
    node = _make_node(node_id=_A_UUID, label="SelfRef")
    session.add(node)
    session.add(_make_edge(_A_UUID, _A_UUID, "relatedTo"))
    await session.flush()
    return node


async def _seed_multi_edge(session: AsyncSession) -> KGNode:
    nodes = [
        _make_node(node_id=_A_UUID, label="NodeA"),
        _make_node(node_id=_B_UUID, label="NodeB"),
    ]
    edges = [
        _make_edge(_A_UUID, _B_UUID, "hasProperty"),
        _make_edge(_A_UUID, _B_UUID, "measuredIn"),
    ]
    for n in nodes:
        session.add(n)
    for e in edges:
        session.add(e)
    await session.flush()
    return nodes[0]


async def _seed_inactive_node(session: AsyncSession) -> KGNode:
    focal = _make_node(node_id=_A_UUID, label="ActiveMat", status="active")
    inactive = _make_node(
        node_id=_B_UUID,
        label="DeprecatedMat",
        status="deprecated",
    )
    session.add(focal)
    session.add(inactive)
    session.add(_make_edge(_A_UUID, _B_UUID, "relatedTo"))
    await session.flush()
    return focal


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestFocalResolution:
    @pytest.mark.asyncio
    async def test_uuid_form(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_type_label_form(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "Material:ZrO2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_bare_label(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "ZrO2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_case_insensitive_label_fallback(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "zro2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_whitespace_trim(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "  ZrO2  ", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "   ", "depth": 1})
        assert resp.status_code == 422
        assert "nodeId must not be empty" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_malformed_uuid_treated_as_label(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "not-a-valid-uuid", "depth": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ambiguous_label_returns_404(self, db_session: AsyncSession) -> None:
        db_session.add(_make_node(node_id=_A_UUID, label="DupLabel", node_type="Material"))
        db_session.add(_make_node(node_id=_B_UUID, label="DupLabel", node_type="Property"))
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "DupLabel", "depth": 1})
        assert resp.status_code == 404


class TestDepthBoundary:
    @pytest.mark.asyncio
    async def test_depth_1_returns_focal_plus_1hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_A_UUID) in node_ids
        assert str(_B_UUID) in node_ids
        assert str(_C_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_depth_2_reaches_2hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 2})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_C_UUID) in node_ids
        assert str(_D_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_depth_3_reaches_3hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 3})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_D_UUID) in node_ids

    @pytest.mark.asyncio
    async def test_depth_0_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 0})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_4_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 4})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_negative_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": -1})
        assert resp.status_code == 422


class TestStatusFilter:
    @pytest.mark.asyncio
    async def test_default_active_excludes_inactive(self, db_session: AsyncSession) -> None:
        await _seed_inactive_node(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_B_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_status_all_returns_inactive(self, db_session: AsyncSession) -> None:
        await _seed_inactive_node(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1, "status": "all"})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_B_UUID) in node_ids

    @pytest.mark.asyncio
    async def test_inactive_focal_returns_404_with_active_status(
        self, db_session: AsyncSession
    ) -> None:
        node = _make_node(node_id=_A_UUID, label="DeprecatedMat", status="deprecated")
        db_session.add(node)
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_inactive_focal_visible_with_status_all(self, db_session: AsyncSession) -> None:
        node = _make_node(node_id=_A_UUID, label="DeprecatedMat", status="deprecated")
        db_session.add(node)
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1, "status": "all"})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)


class TestMissingFocal:
    @pytest.mark.asyncio
    async def test_unknown_uuid_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        fake_id = str(uuid.uuid4())
        resp = client.get("/kg/graph", params={"nodeId": fake_id, "depth": 1})
        assert resp.status_code == 404
        assert fake_id in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_type_label_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "Material:NoSuchMaterial", "depth": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_bare_label_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": "NonExistentLabel", "depth": 1})
        assert resp.status_code == 404


class TestCycleAndSelfLoop:
    @pytest.mark.asyncio
    async def test_self_loop_preserved(self, db_session: AsyncSession) -> None:
        await _seed_self_loop(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        edges = resp.json()["edges"]
        assert len(edges) >= 1
        assert edges[0]["source"] == edges[0]["target"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_triangle_correct_depths(self, db_session: AsyncSession) -> None:
        await _seed_triangle(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 2})
        assert resp.status_code == 200
        body = resp.json()
        node_depths = {n["id"]: n["properties"]["__depth"] for n in body["nodes"]}
        assert node_depths[str(_A_UUID)] == 0
        assert node_depths[str(_B_UUID)] == 1
        assert node_depths[str(_C_UUID)] == 1
        edge_set = {(e["source"], e["target"], e["type"]) for e in body["edges"]}
        assert len(edge_set) == 3


class TestMultiEdge:
    @pytest.mark.asyncio
    async def test_multi_edge_both_returned(self, db_session: AsyncSession) -> None:
        await _seed_multi_edge(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        types = {e["type"] for e in resp.json()["edges"]}
        assert "hasProperty" in types
        assert "measuredIn" in types


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_focal_present_in_nodes_with_depth_0(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["focal"]["id"] == str(_A_UUID)
        assert body["focal"]["depth"] == 0
        focal_nodes = [n for n in body["nodes"] if n["id"] == str(_A_UUID)]
        assert len(focal_nodes) == 1
        assert focal_nodes[0]["properties"]["__depth"] == 0

    @pytest.mark.asyncio
    async def test_depth_is_int(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 2})
        assert resp.status_code == 200
        for node in resp.json()["nodes"]:
            assert isinstance(node["properties"]["__depth"], int)

    @pytest.mark.asyncio
    async def test_nodes_sorted_deterministically(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 2})
        nodes = resp.json()["nodes"]
        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            a_key = (a["properties"]["__depth"], a["label"])
            b_key = (b["properties"]["__depth"], b["label"])
            assert a_key <= b_key

    @pytest.mark.asyncio
    async def test_edges_sorted_deterministically(self, db_session: AsyncSession) -> None:
        await _seed_triangle(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 2})
        edges = resp.json()["edges"]
        for i in range(len(edges) - 1):
            a, b = edges[i], edges[i + 1]
            assert (a["source"], a["target"], a["type"]) <= (b["source"], b["target"], b["type"])

    @pytest.mark.asyncio
    async def test_node_schema_fields(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        node = resp.json()["nodes"][0]
        for field in ("id", "label", "type", "properties", "status", "confidence"):
            assert field in node
        assert "source_id" in node

    @pytest.mark.asyncio
    async def test_edge_schema_fields(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        edge = resp.json()["edges"][0]
        for field in ("source", "target", "type", "properties", "confidence"):
            assert field in edge

    @pytest.mark.asyncio
    async def test_focal_alone_no_edges(self, db_session: AsyncSession) -> None:
        node = _make_node(node_id=_A_UUID, label="IsolatedNode")
        db_session.add(node)
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 1})
        body = resp.json()
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["id"] == str(_A_UUID)
        assert len(body["edges"]) == 0


class TestHardCaps:
    @pytest.mark.asyncio
    async def test_caps_log_warning(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        with (
            patch("nfm_db.services.kg_graph.MAX_NODES", 1),
            patch("nfm_db.services.kg_graph.MAX_EDGES", 1),
            patch("nfm_db.services.kg_graph.logger") as mock_logger,
        ):
            resp = client.get("/kg/graph", params={"nodeId": str(_A_UUID), "depth": 3})
            assert resp.status_code == 200
            assert mock_logger.warning.called


class TestServiceContract:
    """Locked contract #3: ``properties.__depth`` is injected by the service."""

    @pytest.mark.asyncio
    async def test_service_node_carries_depth_in_properties(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_linear_chain(db_session)
        focal = await resolve_focal_node(db_session, str(_A_UUID))
        assert focal is not None
        subgraph = await build_neighborhood_subgraph(db_session, focal, 2)

        assert len(subgraph.nodes) > 0
        for node in subgraph.nodes:
            assert isinstance(node, KGSubgraphNode)
            assert "__depth" in node.properties
            assert isinstance(node.properties["__depth"], int)
            assert node.properties["__depth"] >= 0

    @pytest.mark.asyncio
    async def test_service_nodes_sorted_by_depth_then_label(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_linear_chain(db_session)
        focal = await resolve_focal_node(db_session, str(_A_UUID))
        assert focal is not None
        subgraph = await build_neighborhood_subgraph(db_session, focal, 2)

        for i in range(len(subgraph.nodes) - 1):
            a, b = subgraph.nodes[i], subgraph.nodes[i + 1]
            a_key = (a.properties["__depth"], a.label)
            b_key = (b.properties["__depth"], b.label)
            assert a_key <= b_key
