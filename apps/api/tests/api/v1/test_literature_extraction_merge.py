"""Integration tests for the unified extraction data source (NFM-2210 / NFM-2224).

The ``GET /api/v1/literature/{id}`` endpoint must surface entries from BOTH the
legacy ``extraction_results`` table AND the OntoFuel ``kg_nodes`` /
``kg_edges`` tables under a single ``extraction_results`` array, each item
tagged with a ``source_type`` discriminator.

Hermes deployed a temporary dict-shaped hot-patch (commits ``db41ee03`` /
``6b7998fc``). This test pins the production-quality behavior so the schema
field is exercised end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, ExtractionResult, KGEdge, KGNode

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_source(db_session: AsyncSession, **overrides) -> DataSource:
    """Create a journal-article DataSource with sensible defaults."""
    defaults = dict(
        title="Thermal conductivity of UO2",
        doi=f"10.0000/test-{uuid.uuid4().hex[:8]}",
        journal="Journal of Nuclear Materials",
        year=2024,
        source_type="journal_article",
        abstract="OntoFuel pipeline output for NFM-2210.",
        parse_status="completed",
    )
    defaults.update(overrides)
    source = DataSource(**defaults)
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source


async def _seed_kg_node(
    db_session: AsyncSession,
    *,
    source_id: uuid.UUID,
    node_type: str = "Material",
    label: str = "Uranium Dioxide",
    confidence: float = 0.95,
    properties: dict | None = None,
) -> KGNode:
    """Insert a KGNode row linked to ``source_id``."""
    node = KGNode(
        node_type=node_type,
        label=label,
        confidence=confidence,
        source_id=source_id,
        properties=properties or {"value": 9.5, "unit": "W/(m·K)", "source_page": 3},
        status="active",
    )
    db_session.add(node)
    await db_session.commit()
    await db_session.refresh(node)
    return node


async def _seed_kg_edge(
    db_session: AsyncSession,
    *,
    source_id: uuid.UUID,
    source_node_id: uuid.UUID,
    target_node_id: uuid.UUID,
    relation_type: str = "hasProperty",
    confidence: float = 0.9,
) -> KGEdge:
    """Insert a KGEdge row linking two nodes, both linked to ``source_id``."""
    edge = KGEdge(
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        relation_type=relation_type,
        confidence=confidence,
        source_id=source_id,
        properties={},
    )
    db_session.add(edge)
    await db_session.commit()
    await db_session.refresh(edge)
    return edge


async def _seed_legacy_result(
    db_session: AsyncSession,
    *,
    source_id: uuid.UUID,
    property_name: str = "Manual entry",
) -> ExtractionResult:
    """Insert a manual ExtractionResult row."""
    er = ExtractionResult(
        source_id=source_id,
        property_name=property_name,
        item_type="manual_property",
        item_data={"origin": "human entry"},
        value="42",
        confidence=1.0,
        review_status="approved",
    )
    db_session.add(er)
    await db_session.commit()
    await db_session.refresh(er)
    return er


# ---------------------------------------------------------------------------
# NFM-2210: unified extraction data source
# ---------------------------------------------------------------------------


async def test_get_literature_detail_returns_kg_node_items_with_source_type(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """KGNode rows must surface under ``extraction_results`` with
    ``source_type == "kg_node"`` — even when no ExtractionResult rows exist."""
    source = await _seed_source(db_session)
    kg_node = await _seed_kg_node(
        db_session,
        source_id=source.id,
        node_type="Material",
        label="Uranium Dioxide",
        properties={"value": 9.5, "unit": "W/(m·K)", "source_page": 3},
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True

    items = payload["data"]["extraction_results"]
    assert items, "extraction_results must include KG nodes"

    kg_node_items = [item for item in items if item.get("source_type") == "kg_node"]
    assert len(kg_node_items) == 1
    item = kg_node_items[0]
    assert item["id"] == str(kg_node.id)
    assert item["property_name"] == "Uranium Dioxide"
    assert item["item_type"] == "Material"
    assert item["value"] == 9.5
    assert item["unit"] == "W/(m·K)"
    assert item["confidence"] == pytest.approx(0.95)
    assert item["source_page"] == 3


async def test_get_literature_detail_returns_kg_edge_items_with_source_type(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """KGEdge rows must surface under ``extraction_results`` with
    ``source_type == "kg_edge"``."""
    source = await _seed_source(db_session)
    node_a = await _seed_kg_node(db_session, source_id=source.id, label="UO2")
    node_b = await _seed_kg_node(
        db_session,
        source_id=source.id,
        node_type="Property",
        label="thermal conductivity",
    )
    edge = await _seed_kg_edge(
        db_session,
        source_id=source.id,
        source_node_id=node_a.id,
        target_node_id=node_b.id,
        relation_type="hasProperty",
        confidence=0.88,
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]

    edge_items = [item for item in items if item.get("source_type") == "kg_edge"]
    assert len(edge_items) == 1
    item = edge_items[0]
    assert item["id"] == str(edge.id)
    assert item["property_name"] == "hasProperty"
    assert item["item_type"] == "edge"
    assert item["confidence"] == pytest.approx(0.88)
    assert item["source_node_id"] == str(node_a.id)
    assert item["source_target_id"] == str(node_b.id)


async def test_get_literature_detail_merges_legacy_and_kg_results(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Items from extraction_results + kg_nodes + kg_edges all coexist, each
    tagged with its own ``source_type``."""
    source = await _seed_source(db_session)
    legacy = await _seed_legacy_result(
        db_session,
        source_id=source.id,
        property_name="density",
    )
    node = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="Uranium Dioxide",
        properties={"value": 10.97, "unit": "g/cm^3"},
    )
    target = await _seed_kg_node(
        db_session,
        source_id=source.id,
        node_type="Property",
        label="density",
    )
    edge = await _seed_kg_edge(
        db_session,
        source_id=source.id,
        source_node_id=node.id,
        target_node_id=target.id,
        relation_type="hasProperty",
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]

    types = {item["source_type"] for item in items}
    assert types == {"manual", "kg_node", "kg_edge"}

    by_type = {t: [i for i in items if i["source_type"] == t] for t in types}
    assert by_type["manual"][0]["id"] == str(legacy.id)
    assert by_type["kg_node"][0]["id"] == str(node.id)
    assert by_type["kg_edge"][0]["id"] == str(edge.id)


async def test_get_literature_detail_when_only_kg_data_exists(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """When extraction_results is empty but kg_nodes has rows, the response
    must still surface the KG data (this is the regression case from the
    Sprint Gap-2/3 E2E finding)."""
    source = await _seed_source(db_session)
    # NOTE: deliberately NO ExtractionResult insert.
    await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="Uranium Dioxide",
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]
    assert items, "KG data must show even when extraction_results is empty"
    assert all(item["source_type"] == "kg_node" for item in items)


# ---------------------------------------------------------------------------
# NFM-2224 productionization extras — dedup, ordering, OpenAPI schema
# ---------------------------------------------------------------------------


async def test_get_literature_detail_dedupes_manual_over_kg_node(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """When a manual entry and a KG node collide on (property_name, value),
    only the manual entry is returned (manual wins)."""
    from nfm_db.models import DataSource, ExtractionResult  # noqa: F401

    source = await _seed_source(db_session)
    # KG node: (Thermal conductivity, 2.5)
    await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="Thermal conductivity",
        node_type="Property",
        properties={"value": 2.5, "unit": "W/m·K", "source_page": 3},
    )
    # Manual entry: identical (property_name, value) — must win dedup.
    # We bypass _seed_legacy_result because its default value doesn't match
    # the KG node; we set value=2.5 explicitly here.
    manual_entry = ExtractionResult(
        source_id=source.id,
        property_name="Thermal conductivity",
        item_type="manual_property",
        item_data={"origin": "human entry"},
        value=2.5,  # match KG node value to trigger dedup
        confidence=1.0,
        review_status="approved",
    )
    db_session.add(manual_entry)
    await db_session.commit()
    await db_session.refresh(manual_entry)

    # Distinct KG node — must survive the dedup.
    distinct_node = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="Melting point",
        node_type="Property",
        properties={"value": 3120, "unit": "K"},
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]

    assert len(items) == 2, (
        "Expected exactly 2 items — manual deduplicates against the "
        "conflicting KG node, the distinct KG node survives"
    )
    by_label = {it["property_name"]: it for it in items}
    assert by_label["Thermal conductivity"]["source_type"] == "manual"
    assert by_label["Melting point"]["source_type"] == "kg_node"
    # Confirm we kept the manual entry, not the kg node (by id).
    assert by_label["Thermal conductivity"]["id"] == str(manual_entry.id)
    assert by_label["Melting point"]["id"] == str(distinct_node.id)


async def test_get_literature_detail_orders_by_created_at_desc(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The merged array is sorted by ``created_at`` desc — newest first.

    Three manual entries are inserted with strictly-decreasing
    ``created_at`` timestamps so the response ordering is deterministic
    regardless of ``func.now()`` clock resolution on the test back-end.
    """
    from nfm_db.models import ExtractionResult

    source = await _seed_source(db_session)
    base = datetime.now(tz=UTC)

    # Insert in chronological order so the response should reverse it.
    rows: list[ExtractionResult] = []
    for delta_hours, label in (
        (3, "Oldest"),
        (2, "Middle"),
        (1, "Newest"),
    ):
        er = ExtractionResult(
            source_id=source.id,
            property_name=label,
            item_type="manual_property",
            item_data={"i": delta_hours},
            value=float(delta_hours),
            confidence=1.0,
            review_status="approved",
            created_at=base - timedelta(hours=delta_hours),
        )
        db_session.add(er)
        await db_session.flush()
        rows.append(er)
    await db_session.commit()

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]
    assert len(items) == 3

    labels_in_order = [it["property_name"] for it in items]
    assert labels_in_order[0] == "Newest", labels_in_order
    assert labels_in_order[-1] == "Oldest", labels_in_order


async def test_get_literature_detail_with_no_extraction_data_returns_empty(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Baseline: a freshly-created literature returns an empty array, not null."""
    source = await _seed_source(db_session)
    await db_session.commit()

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    assert response.json()["data"]["extraction_results"] == []


async def test_openapi_schema_exposes_source_type_on_extraction_results(
    async_client: AsyncClient,
) -> None:
    """AC-2: the OpenAPI schema must advertise ``source_type`` on the
    ``extraction_results`` items."""
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    detail_schema = schema["components"]["schemas"]["LiteratureDetailResponse"]
    items_ref = detail_schema["properties"]["extraction_results"]["items"]

    # FastAPI nests the $ref under {"$ref": "..."}; resolve it once.
    if "$ref" in items_ref:
        ref_name = items_ref["$ref"].rsplit("/", 1)[-1]
        items_schema = schema["components"]["schemas"][ref_name]
    else:
        items_schema = items_ref

    # The discriminator must be present in the response-item schema.
    assert "source_type" in items_schema.get("properties", {}), (
        f"OpenAPI schema for {items_schema.get('title', '<unnamed>')} is "
        "missing the source_type discriminator — AC-2 not satisfied."
    )

    enum = items_schema["properties"]["source_type"].get("enum")
    assert enum is not None, "source_type must be a Literal / Enum"
    assert set(enum) == {"manual", "kg_node", "kg_edge"}


async def asyncio_sleep_zero() -> None:
    """Yield to the loop so consecutive inserts get strictly later
    ``created_at`` timestamps on platforms with sub-second clock resolution."""
    import asyncio

    await asyncio.sleep(0)


async def test_get_literature_detail_does_not_dedup_distinct_kg_edges(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression: distinct KG edges must NOT collapse on dedup key.

    Previously ``_dedupe_and_sort`` keyed on ``(property_name, value)``, but
    ``_kg_edge_to_item`` always set ``value=None`` — so every edge sharing
    a ``relation_type`` (e.g. ``UO2 --hasProperty--> density`` and
    ``UO2 --hasProperty--> melting_point``) collapsed into a single row,
    silently dropping real relations. This test pins that three distinct
    ``hasProperty`` edges from the same source node all surface.
    """
    source = await _seed_source(db_session)
    # Single source node.
    source_node = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="UO2",
        node_type="Material",
    )
    # Three distinct target nodes, each linked via the same relation_type.
    target_density = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="density",
        node_type="Property",
    )
    target_melting = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="melting_point",
        node_type="Property",
    )
    target_thermal = await _seed_kg_node(
        db_session,
        source_id=source.id,
        label="thermal_conductivity",
        node_type="Property",
    )
    edge_density = await _seed_kg_edge(
        db_session,
        source_id=source.id,
        source_node_id=source_node.id,
        target_node_id=target_density.id,
        relation_type="hasProperty",
    )
    edge_melting = await _seed_kg_edge(
        db_session,
        source_id=source.id,
        source_node_id=source_node.id,
        target_node_id=target_melting.id,
        relation_type="hasProperty",
    )
    edge_thermal = await _seed_kg_edge(
        db_session,
        source_id=source.id,
        source_node_id=source_node.id,
        target_node_id=target_thermal.id,
        relation_type="hasProperty",
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    items = response.json()["data"]["extraction_results"]

    edge_items = [it for it in items if it.get("source_type") == "kg_edge"]
    expected_ids = {str(edge_density.id), str(edge_melting.id), str(edge_thermal.id)}
    actual_ids = {it["id"] for it in edge_items}
    assert actual_ids == expected_ids, (
        "Distinct kg_edges collapsed on the dedup key — "
        f"expected {expected_ids}, got {actual_ids}"
    )
    assert len(edge_items) == 3
