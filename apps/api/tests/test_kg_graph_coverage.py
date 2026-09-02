"""Tests for the NFM-4093 / NFM-4095 materials→kg_nodes coverage fix.

NFM-4093 baseline (2026-09-02 prod-db snapshot): 55/112 materials had a
working ``Material:<name>`` kg_nodes bridge.  Migration
``071_material_kg_bridge_coverage`` is additive — it inserts the
remaining 55 ``kg_nodes`` rows (plus 2 ``U-13at%Mo`` / ``U-16at%Mo``
status flips), so the post-migration expected coverage is 110/112 (the
two gaps that remain are same-name duplicate groups — 8× Cr-doped UO2
and 5× U-Mo — which are intentionally not given per-material kg_nodes
per CTO verdict ``9fdcc932``).

AC7 verifies that the bridge works for the post-migration universe:

* **NFM-4093-AC7-1** — a ``materials.id`` whose ``name`` matches a
  freshly-inserted ``kg_nodes`` row resolves to ``200`` (the 57 newly
  bridged materials).
* **NFM-4093-AC7-2** — the property-slice rows carry
  ``properties.dataset_slice = true`` so the NFM-4093-DATA-CLEANUP
  follow-up can collapse them.
* **NFM-4093-AC7-3** — the bridge is **exact-match only**: case
  mismatch, whitespace mismatch, or trailing punctuation produces
  ``404``.  This is the regression guard against the proposed fuzzy
  fallback (which the CTO verdict explicitly rejected).
* **NFM-4093-AC7-4** — same-name duplicate materials (e.g. two materials
  named ``Cr-doped UO2``) each resolve to the same canonical kg_nodes
  row; the bridge does NOT duplicate the kg_node row.

All tests run against an in-memory SQLite (the conftest ``db_session``
fixture); they do NOT depend on prod data.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.kg_graph import router
from nfm_db.database import get_db
from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.material import Material
from nfm_db.models.source import DataSource

# ---------------------------------------------------------------------------
# UUIDs & helpers
# ---------------------------------------------------------------------------

# Material UUIDs — one per AC7 test, all in the 068dc946-… prefix range
# so the test data is visually distinct from the pre-existing
# a0000001-… node UUIDs.
_MAT_U10MO = uuid.UUID("068dc946-0000-0000-0000-000000000010")
_MAT_URGENT_HEA = uuid.UUID("068dc946-0000-0000-0000-000000000020")
_MAT_BUCKET_D_NONSLICE = uuid.UUID("068dc946-0000-0000-0000-000000000030")
_MAT_BUCKET_D_SLICE = uuid.UUID("068dc946-0000-0000-0000-000000000040")
_MAT_REVIEW_FLIP = uuid.UUID("068dc946-0000-0000-0000-000000000050")
_MAT_UNKNOWN = uuid.UUID("068dc946-0000-0000-0000-000000000060")
_MAT_DUP_A = uuid.UUID("068dc946-0000-0000-0000-000000000070")
_MAT_DUP_B = uuid.UUID("068dc946-0000-0000-0000-000000000080")

# kg_nodes UUIDs — independent space (the frontend has to bridge).
_KG_U10MO = uuid.UUID("496cf283-0000-0000-0000-000000000010")
_KG_URGENT_HEA = uuid.UUID("496cf283-0000-0000-0000-000000000020")
_KG_BUCKET_D_NONSLICE = uuid.UUID("496cf283-0000-0000-0000-000000000030")
_KG_BUCKET_D_SLICE = uuid.UUID("496cf283-0000-0000-0000-000000000040")
_KG_REVIEW_FLIP = uuid.UUID("496cf283-0000-0000-0000-000000000050")
_KG_DUP = uuid.UUID("496cf283-0000-0000-0000-000000000070")

# Canonical Janney-2019 source_id (matches the value embedded in the
# migration ``_JANNEY_2019_SOURCE_ID`` constant).
_JANNEY_2019_SOURCE_ID = uuid.UUID("e49905d1-61c8-4114-a95c-906c1218b12d")


def _make_client(db_override=None) -> TestClient:
    """Build a TestClient against a fresh FastAPI app with the kg_graph router."""
    app = FastAPI()
    app.include_router(router)
    if db_override is not None:
        app.dependency_overrides[get_db] = db_override
    return TestClient(app)


def _make_kg_node(
    node_id: uuid.UUID,
    label: str,
    *,
    properties: dict[str, Any] | None = None,
    source_id: uuid.UUID | None = None,
    status: str = "active",
    confidence: float = 1.0,
) -> KGNode:
    return KGNode(
        id=node_id,
        node_type="Material",
        label=label,
        status=status,
        confidence=confidence,
        properties=properties or {},
        source_id=source_id,
    )


def _make_material(
    material_id: uuid.UUID,
    name: str,
    *,
    formula: str | None = None,
) -> Material:
    return Material(
        id=material_id,
        name=name,
        formula=formula,
        is_active=True,
    )


def _make_janney_source() -> DataSource:
    return DataSource(
        id=_JANNEY_2019_SOURCE_ID,
        title="Janney et al. 2019 INL/JOU-18-51622",
        doi="10.2172/XXXXXXX",
        source_type="technical_report",
        year=2019,
    )


# ---------------------------------------------------------------------------
# AC7-1 — post-migration 200 OK on the 57 newly bridged materials
# ---------------------------------------------------------------------------


class TestPostMigrationBridge:
    """NFM-4093 AC7 — the bridge covers the 57 newly created kg_nodes."""

    @pytest.mark.asyncio
    async def test_u10mo_returns_200(self, db_session: AsyncSession) -> None:
        """U-10Mo (the headline AC material) resolves to the OECD-NEA kg_node."""
        db_session.add(_make_material(_MAT_U10MO, "U-10Mo", formula="U-10Mo"))
        db_session.add(_make_kg_node(_KG_U10MO, "U-10Mo"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_U10MO), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Focal resolves to the kg_node UUID, NOT the material UUID.
        assert body["focal"]["id"] == str(_KG_U10MO)
        # Full node data is in the nodes array; locate the focal there.
        focal_nodes = [n for n in body["nodes"] if n["id"] == str(_KG_U10MO)]
        assert len(focal_nodes) == 1
        assert focal_nodes[0]["type"] == "Material"
        assert focal_nodes[0]["label"] == "U-10Mo"

    @pytest.mark.asyncio
    async def test_urgent_hea_material_returns_200(self, db_session: AsyncSession) -> None:
        """CoCrFeMnNi Cantor合金 — one of the 8 urgent inserts."""
        db_session.add(_make_material(_MAT_URGENT_HEA, "CoCrFeMnNi Cantor合金"))
        db_session.add(_make_kg_node(_KG_URGENT_HEA, "CoCrFeMnNi Cantor合金"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_URGENT_HEA), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        focal_nodes = [n for n in resp.json()["nodes"] if n["id"] == str(_KG_URGENT_HEA)]
        assert len(focal_nodes) == 1
        assert focal_nodes[0]["label"] == "CoCrFeMnNi Cantor合金"

    @pytest.mark.asyncio
    async def test_bucket_d_nonslice_returns_200(self, db_session: AsyncSession) -> None:
        """A Janney-2019 bucket-D non-slice row (no dataset_slice metadata)."""
        db_session.add(_make_janney_source())
        db_session.add(_make_material(_MAT_BUCKET_D_NONSLICE, "U_15Pu_10Zr_alloy"))
        db_session.add(
            _make_kg_node(
                _KG_BUCKET_D_NONSLICE,
                "U_15Pu_10Zr_alloy",
                source_id=_JANNEY_2019_SOURCE_ID,
                properties={},  # not a property slice
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_BUCKET_D_NONSLICE), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        focal_nodes = [n for n in resp.json()["nodes"] if n["id"] == str(_KG_BUCKET_D_NONSLICE)]
        # Non-slice rows have no dataset_slice metadata.
        assert focal_nodes[0]["properties"].get("dataset_slice") is not True

    @pytest.mark.asyncio
    async def test_review_queue_flip_returns_200(self, db_session: AsyncSession) -> None:
        """U-13at%Mo / U-16at%Mo — pre-existing pending_review flipped to active."""
        db_session.add(_make_material(_MAT_REVIEW_FLIP, "U-13at%Mo"))
        db_session.add(
            _make_kg_node(
                _KG_REVIEW_FLIP,
                "U-13at%Mo",
                status="active",  # flipped by migration step 3
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_REVIEW_FLIP), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        focal_nodes = [n for n in resp.json()["nodes"] if n["id"] == str(_KG_REVIEW_FLIP)]
        assert focal_nodes[0]["status"] == "active"


# ---------------------------------------------------------------------------
# AC7-2 — property-slice rows carry dataset_slice=True metadata
# ---------------------------------------------------------------------------


class TestSliceMetadata:
    """NFM-4093 — the 32 Janney-2019 property-slice rows are flagged."""

    @pytest.mark.asyncio
    async def test_property_slice_row_carries_slice_metadata(
        self, db_session: AsyncSession
    ) -> None:
        """A slice row exposes ``dataset_slice=true`` + ``slice_type`` in focal."""
        db_session.add(_make_janney_source())
        db_session.add(_make_material(_MAT_BUCKET_D_SLICE, "U_20Pu_10Zr_thermal_conductivity"))
        db_session.add(
            _make_kg_node(
                _KG_BUCKET_D_SLICE,
                "U_20Pu_10Zr_thermal_conductivity",
                source_id=_JANNEY_2019_SOURCE_ID,
                properties={
                    "dataset_slice": True,
                    "slice_type": "thermal_conductivity",
                },
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_BUCKET_D_SLICE), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        focal_nodes = [n for n in resp.json()["nodes"] if n["id"] == str(_KG_BUCKET_D_SLICE)]
        assert len(focal_nodes) == 1
        focal_props = focal_nodes[0]["properties"]
        assert focal_props["dataset_slice"] is True
        assert focal_props["slice_type"] == "thermal_conductivity"

    @pytest.mark.asyncio
    async def test_nonslice_row_has_no_slice_metadata(self, db_session: AsyncSession) -> None:
        """A non-slice row MUST NOT carry dataset_slice=True (regression guard)."""
        db_session.add(_make_janney_source())
        db_session.add(_make_material(_MAT_BUCKET_D_NONSLICE, "U_15Pu_10Zr_alloy"))
        db_session.add(
            _make_kg_node(
                _KG_BUCKET_D_NONSLICE,
                "U_15Pu_10Zr_alloy",
                source_id=_JANNEY_2019_SOURCE_ID,
                properties={},
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_BUCKET_D_NONSLICE), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        focal_nodes = [n for n in resp.json()["nodes"] if n["id"] == str(_KG_BUCKET_D_NONSLICE)]
        assert len(focal_nodes) == 1
        props = focal_nodes[0]["properties"]
        assert "dataset_slice" not in props
        assert "slice_type" not in props


# ---------------------------------------------------------------------------
# AC7-3 — exact-match only (regression guard against fuzzy fallback)
# ---------------------------------------------------------------------------


class TestExactMatchOnly:
    """NFM-4093 CTO verdict: NO fuzzy matching.  Only exact label equality."""

    @pytest.mark.asyncio
    async def test_case_mismatch_returns_404(self, db_session: AsyncSession) -> None:
        """``uo2`` (lowercase) MUST NOT match the ``UO2`` kg_node via the bridge.

        The bridge builds ``Material:<material.name>`` and resolves via
        step 2 of ``resolve_focal_node`` (exact ``type:label`` match).
        Case-insensitive fallback only applies to bare-label resolution
        (step 3), NOT to ``type:label`` queries.  A material row whose
        ``name`` differs in case from the canonical kg_node label must
        therefore produce a clean 404.
        """
        db_session.add(_make_material(_MAT_U10MO, "UO2", formula="UO2"))
        # kg_node label is "UO2" — case-sensitive canonical.
        db_session.add(_make_kg_node(_KG_U10MO, "UO2"))
        await db_session.flush()

        # Construct a Material row whose name differs only in case from
        # the kg_node label — the bridge should fail.
        lowercase_mat = uuid.UUID("068dc946-0000-0000-0000-000000000099")
        db_session.add(_make_material(lowercase_mat, "uo2", formula="UO2"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(lowercase_mat), "depth": 1},
        )
        # 404 — bridge exact match only.
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_typo_in_label_returns_404(self, db_session: AsyncSession) -> None:
        """A typo in ``Material:<label>`` MUST NOT match.

        Guards against accidental fuzzy / substring matching being
        introduced.  ``Material:U-10MM`` (extra M) must 404 even though
        ``Material:U-10Mo`` exists.
        """
        # Only the canonical kg_node exists.
        db_session.add(_make_kg_node(_KG_U10MO, "U-10Mo"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": "Material:U-10MM", "depth": 1},
        )
        assert resp.status_code == 404, resp.text
        # Error detail echoes the input.
        assert "U-10MM" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_material_returns_404(self, db_session: AsyncSession) -> None:
        """A material with no matching kg_node produces a clean 404 (no 500)."""
        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_UNKNOWN), "depth": 1},
        )
        assert resp.status_code == 404
        assert str(_MAT_UNKNOWN) in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_material_without_kg_node_returns_404(self, db_session: AsyncSession) -> None:
        """Material row exists but no kg_node row → 404."""
        db_session.add(_make_material(_MAT_UNKNOWN, "Unobtainium"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_UNKNOWN), "depth": 1},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC7-4 — same-name duplicate materials share the canonical kg_node
# ---------------------------------------------------------------------------


class TestSameNameDuplicates:
    """Cr-doped UO2 × 8, U-Mo × 5 — all resolve to ONE kg_node row.

    NFM-4093 disposition: NO per-material kg_node rows for these groups.
    All duplicate materials bridge through the canonical row.
    """

    @pytest.mark.asyncio
    async def test_two_materials_same_name_share_one_kg_node(
        self, db_session: AsyncSession
    ) -> None:
        # Two distinct Material rows with identical name.
        db_session.add(_make_material(_MAT_DUP_A, "Cr-doped UO2", formula="UO2-10at.%Cr"))
        db_session.add(_make_material(_MAT_DUP_B, "Cr-doped UO2", formula="UO2-50at.%Cr"))
        # ONE canonical kg_node row (case-sensitive, no fuzzy).
        db_session.add(_make_kg_node(_KG_DUP, "Cr-doped UO2"))
        await db_session.flush()

        client = _make_client(lambda: db_session)

        # Both material UUIDs must resolve to the SAME kg_node UUID.
        resp_a = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_DUP_A), "depth": 1},
        )
        resp_b = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_DUP_B), "depth": 1},
        )

        assert resp_a.status_code == 200, resp_a.text
        assert resp_b.status_code == 200, resp_b.text
        assert resp_a.json()["focal"]["id"] == str(_KG_DUP)
        assert resp_b.json()["focal"]["id"] == str(_KG_DUP)
        # Pull labels out of nodes[] to confirm both focal nodes share
        # the same canonical label "Cr-doped UO2".
        label_a = next(n for n in resp_a.json()["nodes"] if n["id"] == str(_KG_DUP))["label"]
        label_b = next(n for n in resp_b.json()["nodes"] if n["id"] == str(_KG_DUP))["label"]
        assert label_a == label_b == "Cr-doped UO2"


# ---------------------------------------------------------------------------
# AC7-5 — direct kg_node UUID resolution still works (no regression)
# ---------------------------------------------------------------------------


class TestDirectKgNodeRegression:
    """NFM-4083 regression guard — passing a kg_node UUID still works."""

    @pytest.mark.asyncio
    async def test_kg_node_uuid_returns_200(self, db_session: AsyncSession) -> None:
        db_session.add(_make_kg_node(_KG_U10MO, "U-10Mo"))
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_KG_U10MO), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["focal"]["id"] == str(_KG_U10MO)


# ---------------------------------------------------------------------------
# NFM-4185 — KG orphan fix: U-10Mo edges + U-3Si/PuO2 stub nodes
#
# Mirrors the post-migration-080 graph shape so the API + BFS service are
# pinned against the exact subgraphs AC1/AC2 require and the AC3
# no-regression invariant (edge-bearing materials keep n/e ±0).
# ---------------------------------------------------------------------------

# Fresh UUID range for the NFM-4185 fixtures (068dc946-… was the NFM-4095
# range; 068dc947-… keeps the two cohorts visually distinct).
_MAT_N4185_U3SI = uuid.UUID("068dc947-0000-0000-0000-000000000010")
_MAT_N4185_PUO2 = uuid.UUID("068dc947-0000-0000-0000-000000000020")
_MAT_N4185_UO2 = uuid.UUID("068dc947-0000-0000-0000-000000000030")

_KG_N4185_U3SI = uuid.UUID("496cf284-0000-0000-0000-000000000010")
_KG_N4185_PUO2 = uuid.UUID("496cf284-0000-0000-0000-000000000020")
_KG_N4185_ALPHA_U = uuid.UUID("496cf284-0000-0000-0000-000000000030")
_KG_N4185_DELTA_PU = uuid.UUID("496cf284-0000-0000-0000-000000000040")
_KG_N4185_UO2 = uuid.UUID("496cf284-0000-0000-0000-000000000050")
_KG_N4185_UO2_PROP = uuid.UUID("496cf284-0000-0000-0000-000000000051")

# The three U-10Mo datasets (prod: 075-restored "U-10Mo - Unknown Source"
# rows b1f71371 / 00a9e563 / 94a20c7e; test uses fresh UUIDs).
_N4185_DATASET_IDS = (
    uuid.UUID("496cf284-0000-0000-0000-000000000060"),
    uuid.UUID("496cf284-0000-0000-0000-000000000061"),
    uuid.UUID("496cf284-0000-0000-0000-000000000062"),
)
_N4185_DATASET_NODE_IDS = (
    uuid.UUID("496cf284-0000-0000-0000-000000000070"),
    uuid.UUID("496cf284-0000-0000-0000-000000000071"),
    uuid.UUID("496cf284-0000-0000-0000-000000000072"),
)


def _make_dataset_node(
    node_id: uuid.UUID,
    dataset_id: uuid.UUID,
    title: str = "U-10Mo - Unknown Source",
) -> KGNode:
    """Build the Measurement node migration 080 creates per dataset."""
    return KGNode(
        id=node_id,
        node_type="Measurement",
        label=title,
        status="active",
        confidence=1.0,
        properties={
            "dataset_id": str(dataset_id),
            "provenance": "NFM-4185:migration 080",
        },
        source_id=None,
    )


def _seed_u10mo_with_dataset_bridge(db_session: AsyncSession) -> None:
    """Seed the post-080 U-10Mo subgraph shape (AC1 baseline)."""
    from nfm_db.models.property import Dataset

    db_session.add(_make_material(_MAT_U10MO, "U-10Mo", formula="U-10Mo"))
    db_session.add(_make_kg_node(_KG_U10MO, "U-10Mo"))
    for dataset_id, node_id in zip(_N4185_DATASET_IDS, _N4185_DATASET_NODE_IDS, strict=True):
        db_session.add(
            Dataset(
                id=dataset_id,
                material_id=_MAT_U10MO,
                source_id=None,
                title="U-10Mo - Unknown Source",
            )
        )
        db_session.add(_make_dataset_node(node_id, dataset_id))
        db_session.add(
            KGEdge(
                source_node_id=_KG_U10MO,
                target_node_id=node_id,
                relation_type="containsData",
                properties={
                    "dataset_id": str(dataset_id),
                    "provenance": "NFM-4185:migration 080",
                },
                confidence=1.0,
            )
        )


class TestNFM4185U10MoDatasetBridge:
    """AC1 — U-10Mo depth-1 subgraph: self + 3 dataset nodes, ≥3 edges."""

    @pytest.mark.asyncio
    async def test_u10mo_depth1_has_4_nodes_3_edges(self, db_session: AsyncSession) -> None:
        _seed_u10mo_with_dataset_bridge(db_session)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_U10MO), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["focal"]["id"] == str(_KG_U10MO)
        # AC1: >=4 nodes (self + 3 dataset nodes) and >=3 edges.
        assert len(body["nodes"]) >= 4, body["nodes"]
        assert len(body["edges"]) >= 3, body["edges"]

    @pytest.mark.asyncio
    async def test_u10mo_edge_count_positive_orphan_regression_guard(
        self, db_session: AsyncSession
    ) -> None:
        """AC4 orphan-regression guard — U-10Mo edge count must be > 0.

        Pre-080 prod state was n=1/e=0 at every depth (the headline
        defect).  This test fails if the dataset bridge edges ever
        disappear again.
        """
        _seed_u10mo_with_dataset_bridge(db_session)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_U10MO), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["edges"]) > 0, (
            "NFM-4185 regression: U-10Mo focal resolved but carries zero "
            "edges — the Material→dataset containsData bridge is missing"
        )

    @pytest.mark.asyncio
    async def test_u10mo_dataset_nodes_are_measurement_with_dataset_id(
        self, db_session: AsyncSession
    ) -> None:
        """The 3 linked dataset nodes carry the durable dataset_id link."""
        _seed_u10mo_with_dataset_bridge(db_session)
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_U10MO), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        nodes = resp.json()["nodes"]
        dataset_nodes = [n for n in nodes if n["id"] in {str(x) for x in _N4185_DATASET_NODE_IDS}]
        assert len(dataset_nodes) == 3
        linked_dataset_ids = {n["properties"]["dataset_id"] for n in dataset_nodes}
        assert linked_dataset_ids == {str(x) for x in _N4185_DATASET_IDS}
        for node in dataset_nodes:
            assert node["type"] == "Measurement"
        edges = resp.json()["edges"]
        contains = [e for e in edges if e["type"] == "containsData"]
        assert len(contains) >= 3
        assert {e["source"] for e in contains} == {str(_KG_U10MO)}


class TestNFM4185StubMaterials:
    """AC2 — U-3Si and PuO2 resolve to 200 with ≥1 edge each."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("material_id", "kg_id", "name", "anchor_id", "anchor_label"),
        [
            (
                _MAT_N4185_U3SI,
                _KG_N4185_U3SI,
                "U-3Si",
                _KG_N4185_ALPHA_U,
                "alpha_U_solid_solution",
            ),
            (
                _MAT_N4185_PUO2,
                _KG_N4185_PUO2,
                "PuO2",
                _KG_N4185_DELTA_PU,
                "delta_Pu_solid_solution",
            ),
        ],
    )
    async def test_stub_material_returns_200_with_edge(
        self,
        db_session: AsyncSession,
        material_id: uuid.UUID,
        kg_id: uuid.UUID,
        name: str,
        anchor_id: uuid.UUID,
        anchor_label: str,
    ) -> None:
        db_session.add(_make_material(material_id, name))
        db_session.add(_make_kg_node(kg_id, name, source_id=None))
        db_session.add(_make_kg_node(anchor_id, anchor_label))
        db_session.add(
            KGEdge(
                source_node_id=kg_id,
                target_node_id=anchor_id,
                relation_type="relatedTo",
                properties={"provenance": "NFM-4185:migration 080"},
                confidence=1.0,
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(material_id), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["focal"]["id"] == str(kg_id)
        assert len(body["nodes"]) >= 2
        assert len(body["edges"]) >= 1, f"AC2: {name} must no longer be an orphan singleton"
        edge = body["edges"][0]
        assert {edge["source"], edge["target"]} == {str(kg_id), str(anchor_id)}


class TestNFM4185NoRegressionOnEdgeBearers:
    """AC3 — the new bridge must not leak into edge-bearing subgraphs."""

    @pytest.mark.asyncio
    async def test_uo2_subgraph_unchanged_by_orphan_bridge(self, db_session: AsyncSession) -> None:
        """UO2 (edge-bearing, n=2/e=1 baseline) keeps n/e exactly.

        The 080 bridge only attaches to orphan components (U-10Mo, the
        new Measurement dataset nodes, U-3Si/PuO2, and their orphan
        anchors).  An edge-bearing focal must see none of them at
        depth 1 — the literal AC3 "n/e within +/-0" invariant.
        """
        # Edge-bearing baseline: UO2 --hasProperty--> Property.
        db_session.add(_make_material(_MAT_N4185_UO2, "UO2", formula="UO2"))
        db_session.add(_make_kg_node(_KG_N4185_UO2, "UO2"))
        db_session.add(
            KGNode(
                id=_KG_N4185_UO2_PROP,
                node_type="Property",
                label="thermal conductivity",
                status="active",
                confidence=1.0,
                properties={},
            )
        )
        db_session.add(
            KGEdge(
                source_node_id=_KG_N4185_UO2,
                target_node_id=_KG_N4185_UO2_PROP,
                relation_type="hasProperty",
                confidence=1.0,
            )
        )
        # The full NFM-4185 bridge fixtures coexist in the same graph.
        _seed_u10mo_with_dataset_bridge(db_session)
        db_session.add(_make_kg_node(_KG_N4185_U3SI, "U-3Si"))
        db_session.add(_make_kg_node(_KG_N4185_ALPHA_U, "alpha_U_solid_solution"))
        db_session.add(
            KGEdge(
                source_node_id=_KG_N4185_U3SI,
                target_node_id=_KG_N4185_ALPHA_U,
                relation_type="relatedTo",
                confidence=1.0,
            )
        )
        await db_session.flush()

        client = _make_client(lambda: db_session)
        resp = client.get(
            "/kg/graph/subgraph",
            params={"nodeId": str(_MAT_N4185_UO2), "depth": 1},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["nodes"]) == 2, body["nodes"]
        assert len(body["edges"]) == 1, body["edges"]
        node_ids = {n["id"] for n in body["nodes"]}
        bridge_ids = {
            str(x)
            for x in (
                _KG_U10MO,
                *_N4185_DATASET_NODE_IDS,
                _KG_N4185_U3SI,
                _KG_N4185_PUO2,
            )
        }
        assert node_ids.isdisjoint(bridge_ids)
