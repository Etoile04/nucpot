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


class TestMaterialIdBridgeField:
    """NFM-4445: KG Material node response carries ``material_id`` so the
    frontend can navigate to ``/materials/<id>`` instead of using the
    KG UUID directly (which 404s because the two are independent UUID
    spaces — see NFM-4083). The bridge is computed by batch-resolving
    ``materials.name = kg_nodes.label`` in ``_to_response``.
    """

    _MATERIAL_UUID = uuid.UUID("068dc946-0000-0000-0000-000000000001")
    _OTHER_MATERIAL_UUID = uuid.UUID(
        "068dc946-0000-0000-0000-000000000002",
    )

    async def test_material_node_includes_material_id_bridge(
        self, db_session: AsyncSession
    ) -> None:
        # The frontend's UAT-3 bug: clicking a Material node 404'd because
        # the KG node UUID was used as materials.id. Verify the response
        # now exposes the real materials.id so the frontend can navigate.
        material = Material(
            id=self._MATERIAL_UUID,
            name="UO2",
            formula="UO2",
            is_active=True,
        )
        db_session.add(material)
        kg_node = _make_node(
            node_id=_A_UUID,
            label="UO2",
            node_type="Material",
        )
        db_session.add(kg_node)
        db_session.add(_make_node(
            node_id=_B_UUID,
            label="MeltingPoint",
            node_type="Property",
        ))
        db_session.add(_make_edge(_A_UUID, _B_UUID, "hasProperty"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_A_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        nodes = {n["id"]: n for n in resp.json()["nodes"]}

        # Material node carries the bridge to materials.id (NOT kg uuid).
        assert nodes[str(_A_UUID)]["material_id"] == str(self._MATERIAL_UUID)
        assert nodes[str(_A_UUID)]["material_id"] != str(_A_UUID)

        # Property nodes have no bridge (not Material-typed).
        assert nodes[str(_B_UUID)].get("material_id") is None

    async def test_material_node_without_matching_material_returns_null_bridge(
        self, db_session: AsyncSession
    ) -> None:
        # Coverage gap (NFM-4093): KG Material node exists, but no
        # materials.name row matches its label. The frontend must show
        # a tooltip instead of navigating — backend signals this by
        # leaving material_id = null.
        kg_node = _make_node(
            node_id=_A_UUID,
            label="OrphanMaterial",
            node_type="Material",
        )
        db_session.add(kg_node)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_A_UUID), "depth": 1},
        )
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["material_id"] is None

    async def test_bridge_is_batched_single_query(
        self, db_session: AsyncSession
    ) -> None:
        # Four Material nodes (depth=3 reaches focal + 3 hops), all bridged.
        # Verify the bridge field appears on every one — sanity check
        # that the batch lookup covers all Material labels in the
        # subgraph, not just the focal node.
        names = [f"Mat{i}" for i in range(4)]
        kg_uuids = [
            uuid.UUID(f"a0000001-0000-0000-0000-{i:012d}")
            for i in range(4)
        ]
        mat_uuids = [
            uuid.UUID(f"068dc946-0000-0000-0000-{i:012d}")
            for i in range(4)
        ]
        for i in range(4):
            db_session.add(Material(
                id=mat_uuids[i], name=names[i], is_active=True,
            ))
            db_session.add(_make_node(
                node_id=kg_uuids[i],
                label=names[i],
                node_type="Material",
            ))
        # Chain so depth=3 reaches Mat0..Mat3.
        for i in range(3):
            db_session.add(_make_edge(kg_uuids[i], kg_uuids[i + 1], "relatedTo"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(kg_uuids[0]), "depth": 3},
        )
        assert resp.status_code == 200
        node_bridges = {
            n["label"]: n["material_id"] for n in resp.json()["nodes"]
        }
        for i, name in enumerate(names):
            assert node_bridges[name] == str(mat_uuids[i]), (
                f"Material node {name!r} expected bridge "
                f"{mat_uuids[i]} got {node_bridges[name]}"
            )


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
