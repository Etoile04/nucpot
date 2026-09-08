"""Tests for KG Graph API endpoint (NFM-1280, NFM-4083).

Tests for GET /api/v1/kg/graph/subgraph covering:
- Focal node resolution (UUID, type:label, bare label, case-insensitive)
- materials.id → KG focal translation (NFM-4083)
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
from nfm_db.models.material import Material
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
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_type_label_form(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "Material:ZrO2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_bare_label(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "ZrO2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_case_insensitive_label_fallback(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "zro2", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_whitespace_trim(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "  ZrO2  ", "depth": 1})
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "   ", "depth": 1})
        assert resp.status_code == 422
        assert "nodeId must not be empty" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_malformed_uuid_treated_as_label(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "not-a-valid-uuid", "depth": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ambiguous_label_returns_404(self, db_session: AsyncSession) -> None:
        db_session.add(_make_node(node_id=_A_UUID, label="DupLabel", node_type="Material"))
        db_session.add(_make_node(node_id=_B_UUID, label="DupLabel", node_type="Property"))
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "DupLabel", "depth": 1})
        assert resp.status_code == 404


class TestDepthBoundary:
    @pytest.mark.asyncio
    async def test_depth_1_returns_focal_plus_1hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_A_UUID) in node_ids
        assert str(_B_UUID) in node_ids
        assert str(_C_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_depth_2_reaches_2hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 2})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_C_UUID) in node_ids
        assert str(_D_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_depth_3_reaches_3hop(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 3})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_D_UUID) in node_ids

    @pytest.mark.asyncio
    async def test_depth_0_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 0})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_4_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 4})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_depth_negative_returns_422(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": -1})
        assert resp.status_code == 422


class TestStatusFilter:
    @pytest.mark.asyncio
    async def test_default_active_excludes_inactive(self, db_session: AsyncSession) -> None:
        await _seed_inactive_node(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert str(_B_UUID) not in node_ids

    @pytest.mark.asyncio
    async def test_status_all_returns_inactive(self, db_session: AsyncSession) -> None:
        await _seed_inactive_node(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1, "status": "all"}
        )
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
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_inactive_focal_visible_with_status_all(self, db_session: AsyncSession) -> None:
        node = _make_node(node_id=_A_UUID, label="DeprecatedMat", status="deprecated")
        db_session.add(node)
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1, "status": "all"}
        )
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)


class TestMissingFocal:
    @pytest.mark.asyncio
    async def test_unknown_uuid_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        fake_id = str(uuid.uuid4())
        resp = client.get("/kg/graph/subgraph", params={"nodeId": fake_id, "depth": 1})
        assert resp.status_code == 404
        assert fake_id in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_type_label_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph", params={"nodeId": "Material:NoSuchMaterial", "depth": 1}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_bare_label_returns_404(self, db_session: AsyncSession) -> None:
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": "NonExistentLabel", "depth": 1})
        assert resp.status_code == 404


class TestCycleAndSelfLoop:
    @pytest.mark.asyncio
    async def test_self_loop_preserved(self, db_session: AsyncSession) -> None:
        await _seed_self_loop(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        edges = resp.json()["edges"]
        assert len(edges) >= 1
        assert edges[0]["source"] == edges[0]["target"] == str(_A_UUID)

    @pytest.mark.asyncio
    async def test_triangle_correct_depths(self, db_session: AsyncSession) -> None:
        await _seed_triangle(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 2})
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
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        assert resp.status_code == 200
        types = {e["type"] for e in resp.json()["edges"]}
        assert "hasProperty" in types
        assert "measuredIn" in types


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_focal_present_in_nodes_with_depth_0(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
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
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 2})
        assert resp.status_code == 200
        for node in resp.json()["nodes"]:
            assert isinstance(node["properties"]["__depth"], int)

    @pytest.mark.asyncio
    async def test_nodes_sorted_deterministically(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 2})
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
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 2})
        edges = resp.json()["edges"]
        for i in range(len(edges) - 1):
            a, b = edges[i], edges[i + 1]
            assert (a["source"], a["target"], a["type"]) <= (b["source"], b["target"], b["type"])

    @pytest.mark.asyncio
    async def test_node_schema_fields(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        node = resp.json()["nodes"][0]
        for field in ("id", "label", "type", "properties", "status", "confidence"):
            assert field in node
        assert "source_id" in node

    @pytest.mark.asyncio
    async def test_edge_schema_fields(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
        edge = resp.json()["edges"][0]
        for field in ("source", "target", "type", "properties", "confidence"):
            assert field in edge

    @pytest.mark.asyncio
    async def test_focal_alone_no_edges(self, db_session: AsyncSession) -> None:
        node = _make_node(node_id=_A_UUID, label="IsolatedNode")
        db_session.add(node)
        await db_session.flush()
        client = _make_client(lambda: db_session)
        resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 1})
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
            resp = client.get("/kg/graph/subgraph", params={"nodeId": str(_A_UUID), "depth": 3})
            assert resp.status_code == 200
            assert mock_logger.warning.called


class TestMaterialIdTranslation:
    """NFM-4083: ``materials.id`` UUID resolves to the ``KGNode`` for that material.

    The frontend passes ``materials.id`` (e.g. ``068dc946-…`` for UO2) to
    ``/kg/graph/subgraph``, but ``kg_nodes`` has an independent UUID space
    (``496cf283-…`` for the UO2 KG node).  The endpoint must bridge the
    two via ``materials.name`` so each material page renders its own
    subgraph instead of the global pool.
    """

    _MATERIAL_UUID = uuid.UUID("068dc946-0000-0000-0000-000000000001")

    async def test_materials_id_resolves_to_matching_kg_node(
        self, db_session: AsyncSession
    ) -> None:
        # Seed a Material row with the "frontend-side" UUID the page sends.
        material = Material(
            id=self._MATERIAL_UUID,
            name="UO2",
            formula="UO2",
            is_active=True,
        )
        db_session.add(material)

        # Seed the matching KG node under a different UUID (the KG space).
        kg_node = _make_node(
            node_id=_A_UUID,
            label="UO2",
            node_type="Material",
        )
        db_session.add(kg_node)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._MATERIAL_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Focal is the KG node, NOT the material row.
        assert body["focal"]["id"] == str(_A_UUID)

    async def test_unknown_materials_id_returns_404(self, db_session: AsyncSession) -> None:
        # No material row, no kg row — should be a clean 404, not a 500.
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._MATERIAL_UUID), "depth": 1},
        )
        assert resp.status_code == 404
        assert str(self._MATERIAL_UUID) in resp.json()["detail"]

    async def test_materials_id_with_no_matching_kg_node_returns_404(
        self, db_session: AsyncSession
    ) -> None:
        # Material exists, but no Material KG node with that label.
        material = Material(
            id=self._MATERIAL_UUID,
            name="Unobtainium",
            is_active=True,
        )
        db_session.add(material)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._MATERIAL_UUID), "depth": 1},
        )
        assert resp.status_code == 404

    async def test_direct_kg_uuid_still_resolves_without_bridge(
        self, db_session: AsyncSession
    ) -> None:
        # Passing the KG UUID directly must continue to work (no regression).
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_A_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["focal"]["id"] == str(_A_UUID)


class TestServiceContract:
    """Locked contract #3: ``properties.__depth`` is injected by the service."""

    @pytest.mark.asyncio
    async def test_service_node_carries_depth_in_properties(self, db_session: AsyncSession) -> None:
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
    async def test_service_nodes_sorted_by_depth_then_label(self, db_session: AsyncSession) -> None:
        await _seed_linear_chain(db_session)
        focal = await resolve_focal_node(db_session, str(_A_UUID))
        assert focal is not None
        subgraph = await build_neighborhood_subgraph(db_session, focal, 2)

        for i in range(len(subgraph.nodes) - 1):
            a, b = subgraph.nodes[i], subgraph.nodes[i + 1]
            a_key = (a.properties["__depth"], a.label)
            b_key = (b.properties["__depth"], b.label)
            assert a_key <= b_key


# ===========================================================================
# NFM-4445 — KG-node → materials.id reverse bridge exposed in the response
# ===========================================================================


class TestMaterialsIdBridgeResponse:
    """NFM-4445 — every Material-typed node in the response carries a
    ``materials_id`` field with the canonical ``materials.id`` UUID, distinct
    from the KG-node ``id`` already in the payload.  Without a matching row
    the field is ``null``; ambiguous same-name cohorts stay null too.

    Concrete example: UO2's KG-node is ``496cf283-…`` but its materials row is
    ``068dc946-…``.  Clicking the Material node in the graph must navigate
    to ``/materials/{068dc946-…}``, never to ``/materials/{496cf283-…}``.
    """

    _MAT_UO2_UUID = uuid.UUID("068dc946-0000-0000-0000-000000000001")
    _MAT_SIC_UUID = uuid.UUID("068dc946-0000-0000-0000-000000000002")
    _MAT_DUP_UUID = uuid.UUID("068dc946-0000-0000-0000-000000000003")

    _KG_UO2_UUID = uuid.UUID("496cf283-0000-0000-0000-000000000001")
    _KG_SIC_UUID = uuid.UUID("496cf283-0000-0000-0000-000000000002")
    _KG_DUP_UUID = uuid.UUID("496cf283-0000-0000-0000-000000000003")

    async def _seed_materials_with_kg_nodes(self, db_session: AsyncSession) -> None:
        # Two clean bridges: UO2 and SiC.
        db_session.add(Material(id=self._MAT_UO2_UUID, name="UO2", is_active=True))
        db_session.add(Material(id=self._MAT_SIC_UUID, name="SiC", is_active=True))
        # NFM-4093 same-name cohort: 2x "Cr-doped UO2" materials.
        # Bridge intentionally ambiguous → both KG nodes must report null.
        db_session.add(Material(id=self._MAT_DUP_UUID, name="Cr-doped UO2", is_active=True))
        db_session.add(
            Material(
                id=uuid.UUID("068dc946-0000-0000-0000-000000000004"),
                name="Cr-doped UO2",
                is_active=True,
            )
        )

        db_session.add(_make_node(node_id=self._KG_UO2_UUID, label="UO2", node_type="Material"))
        db_session.add(_make_node(node_id=self._KG_SIC_UUID, label="SiC", node_type="Material"))
        db_session.add(
            _make_node(
                node_id=self._KG_DUP_UUID,
                label="Cr-doped UO2",
                node_type="Material",
            )
        )
        # Property node (no bridge) and an isolated material with no materials row.
        db_session.add(
            _make_node(
                node_id=uuid.UUID("496cf283-0000-0000-0000-0000000000ff"),
                label="Density",
                node_type="Property",
            )
        )
        db_session.add(
            _make_node(
                node_id=uuid.UUID("496cf283-0000-0000-0000-0000000000fe"),
                label="Obtainium",
                node_type="Material",
            )
        )
        # Edges so BFS traverses Material → neighbor.
        db_session.add(_make_edge(self._KG_UO2_UUID, self._KG_SIC_UUID, "relatedTo"))
        db_session.add(
            _make_edge(
                self._KG_UO2_UUID, uuid.UUID("496cf283-0000-0000-0000-0000000000ff"), "hasProperty"
            )
        )
        await db_session.flush()

    @pytest.mark.asyncio
    async def test_material_node_carries_materials_id_bridge(
        self, db_session: AsyncSession
    ) -> None:
        """Focal UO2 KG node returns ``materials_id=068dc946-…``."""
        await self._seed_materials_with_kg_nodes(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._KG_UO2_UUID), "depth": 2},
        )
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]

        by_id = {n["id"]: n for n in nodes}

        # UO2 → 068dc946-… (canonical materials row), NOT 496cf283-… (KG id).
        uo2 = by_id[str(self._KG_UO2_UUID)]
        assert uo2["materials_id"] == str(self._MAT_UO2_UUID)
        assert uo2["materials_id"] != uo2["id"]

        # SiC neighbor → its own materials row.
        sic = by_id[str(self._KG_SIC_UUID)]
        assert sic["materials_id"] == str(self._MAT_SIC_UUID)

    @pytest.mark.asyncio
    async def test_non_material_node_has_null_materials_id(self, db_session: AsyncSession) -> None:
        """Property-typed nodes leave ``materials_id`` null — no FK applies."""
        await self._seed_materials_with_kg_nodes(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._KG_UO2_UUID), "depth": 2},
        )
        assert resp.status_code == 200

        prop_node = next(
            n for n in resp.json()["nodes"] if n["id"] == "496cf283-0000-0000-0000-0000000000ff"
        )
        # /kg/graph/subgraph returns the raw KG ``node_type`` ("Property");
        # the frontend mapper (``kg-api.ts::toGraphNodeType``) lowercases
        # to the public "property" category.  Either way, the bridge must
        # be null for non-Material nodes.
        assert prop_node["type"] == "Property"
        assert prop_node["materials_id"] is None

    @pytest.mark.asyncio
    async def test_material_without_materials_row_has_null_materials_id(
        self, db_session: AsyncSession
    ) -> None:
        """A Material KG node whose label has no materials row → null bridge.

        This is the NFM-4093 partial-coverage case for non-duplicate labels.
        The UI must surface tooltip-only navigation (no /materials/{id}).
        """
        await self._seed_materials_with_kg_nodes(db_session)
        client = _make_client(lambda: db_session)

        # Use the focal = the Obtainium KG node (no materials row).
        resp = client.get(
            "/kg/graph/subgraph",
            params={
                "nodeId": str(uuid.UUID("496cf283-0000-0000-0000-0000000000fe")),
                "depth": 1,
            },
        )
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["materials_id"] is None

    @pytest.mark.asyncio
    async def test_same_name_duplicate_cohort_is_null(self, db_session: AsyncSession) -> None:
        """NFM-4093: a Material node sharing its name with >1 materials row
        is intentionally ambiguous — ``materials_id`` stays ``null`` so the
        UI never silently mis-routes to one of the duplicates.
        """
        await self._seed_materials_with_kg_nodes(db_session)
        client = _make_client(lambda: db_session)

        # Focal = the Cr-doped UO2 KG node (label shared with 2 materials).
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(self._KG_DUP_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        dup_node = next(n for n in nodes if n["id"] == str(self._KG_DUP_UUID))
        assert dup_node["materials_id"] is None

    @pytest.mark.asyncio
    async def test_response_schema_includes_materials_id_field(
        self, db_session: AsyncSession
    ) -> None:
        """NFM-4445 contract: every node in the response carries a
        ``materials_id`` field (even if ``null``) so the frontend can rely
        on its presence without per-shape narrowing.
        """
        await _seed_linear_chain(db_session)
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_A_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        for node in resp.json()["nodes"]:
            assert "materials_id" in node
            assert node["materials_id"] is None  # no materials seeded


class TestMaterialsIdLookupHelper:
    """NFM-4445 — service-layer ``lookup_materials_ids_by_labels`` batch
    resolver.  Skips ambiguous same-name cohorts and missing rows."""

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self, db_session: AsyncSession) -> None:
        from nfm_db.services.kg_graph import lookup_materials_ids_by_labels

        result = await lookup_materials_ids_by_labels(db_session, [])
        assert result == {}

    @pytest.mark.asyncio
    async def test_resolves_single_label(self, db_session: AsyncSession) -> None:
        from nfm_db.services.kg_graph import lookup_materials_ids_by_labels

        mat_uuid = uuid.UUID("068dc946-0000-0000-0000-0000000000aa")
        db_session.add(Material(id=mat_uuid, name="UO2", is_active=True))
        await db_session.flush()

        result = await lookup_materials_ids_by_labels(db_session, ["UO2"])
        assert result == {"UO2": str(mat_uuid)}

    @pytest.mark.asyncio
    async def test_missing_label_omitted(self, db_session: AsyncSession) -> None:
        from nfm_db.services.kg_graph import lookup_materials_ids_by_labels

        result = await lookup_materials_ids_by_labels(db_session, ["Nonexistent"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_ambiguous_same_name_label_omitted(self, db_session: AsyncSession) -> None:
        """NFM-4093 same-name cohort (e.g. 8x Cr-doped UO2) is intentionally
        ambiguous; the helper must omit it rather than silently returning the
        first match."""
        from nfm_db.services.kg_graph import lookup_materials_ids_by_labels

        db_session.add(
            Material(
                id=uuid.UUID("068dc946-0000-0000-0000-0000000000b1"),
                name="Cr-doped UO2",
                is_active=True,
            )
        )
        db_session.add(
            Material(
                id=uuid.UUID("068dc946-0000-0000-0000-0000000000b2"),
                name="Cr-doped UO2",
                is_active=True,
            )
        )
        # Single-match sibling still resolves.
        db_session.add(
            Material(
                id=uuid.UUID("068dc946-0000-0000-0000-0000000000b3"), name="SiC", is_active=True
            )
        )
        await db_session.flush()

        result = await lookup_materials_ids_by_labels(db_session, ["Cr-doped UO2", "SiC"])
        # Cr-doped UO2 omitted (ambiguous); SiC kept (single match).
        assert "Cr-doped UO2" not in result
        assert result["SiC"] == "068dc946-0000-0000-0000-0000000000b3"

    @pytest.mark.asyncio
    async def test_batch_dedupes_labels(self, db_session: AsyncSession) -> None:
        from nfm_db.services.kg_graph import lookup_materials_ids_by_labels

        mat_uuid = uuid.UUID("068dc946-0000-0000-0000-0000000000cc")
        db_session.add(Material(id=mat_uuid, name="UO2", is_active=True))
        await db_session.flush()

        result = await lookup_materials_ids_by_labels(db_session, ["UO2", "UO2", "UO2"])
        assert result == {"UO2": str(mat_uuid)}
