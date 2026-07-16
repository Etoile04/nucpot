"""Integration tests for `GET /api/v1/materials/{material_id}/properties`.

Endpoint contract (NFM-1066 §1, NFM-1067):

  GET /api/v1/materials/{material_id}/properties?page=1&limit=50&sort=name&order=asc&filter=...

  Response (ApiResponse envelope):
    {
      "success": true,
      "data": {
        "data": [
          {
            "id":        "<measurement uuid>",
            "name":      "<property type name>",
            "value":     "<formatted scalar/range/expression/list/text>",
            "unit":      "<unit symbol>" | null,
            "source":    "<data source title>",
            "confidence": 0..1
          }, ...
        ],
        "meta": { "total": <int>, "page": <int>, "limit": <int> }
      }
    }
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
    Unit,
)

API = "/api/v1/materials/{material_id}/properties"


# ---------------------------------------------------------------------------
# Helpers — isolated per-test seeding
# ---------------------------------------------------------------------------


async def _seed_material(db_session, **overrides):
    defaults = dict(name="UO2", formula="UO2")
    defaults.update(overrides)
    mat = Material(**defaults)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    return mat


async def _seed_source(db_session, **overrides) -> DataSource:
    defaults = dict(title="J. Nucl. Mater. 2020", source_type="journal_article")
    defaults.update(overrides)
    src = DataSource(**defaults)
    db_session.add(src)
    await db_session.commit()
    await db_session.refresh(src)
    return src


async def _seed_category(db_session, **overrides) -> PropertyCategory:
    defaults = dict(name="Thermal", slug="thermal")
    defaults.update(overrides)
    cat = PropertyCategory(**defaults)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


async def _seed_unit(db_session, **overrides) -> Unit:
    defaults = dict(name="Watt per meter Kelvin", symbol="W/(m·K)", dimension="power/(length*temperature)")
    defaults.update(overrides)
    u = Unit(**defaults)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _seed_property_type(db_session, category_id, unit_id=None, **overrides) -> PropertyType:
    defaults = dict(
        category_id=category_id,
        name="Thermal Conductivity",
        slug="thermal-conductivity",
        value_type="scalar",
        unit_id=unit_id,
    )
    defaults.update(overrides)
    pt = PropertyType(**defaults)
    db_session.add(pt)
    await db_session.commit()
    await db_session.refresh(pt)
    return pt


async def _seed_dataset(db_session, material_id, source_id, **overrides) -> Dataset:
    defaults = dict(
        material_id=material_id,
        source_id=source_id,
        title="Test Dataset",
        is_verified=False,
    )
    defaults.update(overrides)
    ds = Dataset(**defaults)
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


async def _seed_measurement(
    db_session, dataset_id, property_type_id, unit_id=None, **overrides
) -> PropertyMeasurement:
    defaults = dict(
        dataset_id=dataset_id,
        property_type_id=property_type_id,
        value_scalar=3.5,
        uncertainty=0.1,
        unit_id=unit_id,
        review_status="approved",
    )
    defaults.update(overrides)
    pm = PropertyMeasurement(**defaults)
    db_session.add(pm)
    await db_session.commit()
    await db_session.refresh(pm)
    return pm


# ---------------------------------------------------------------------------
# Empty / 404 / default page-size
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_empty_returns_200(async_client, db_session) -> None:
    """A material with no measurements must return 200 + empty list (not 404)."""
    mat = await _seed_material(db_session)

    response = await async_client.get(API.format(material_id=mat.id))

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    inner = body["data"]
    assert inner["data"] == []
    assert inner["meta"] == {"total": 0, "page": 1, "limit": 50}


@pytest.mark.asyncio
async def test_list_material_properties_404_for_unknown_material(async_client) -> None:
    """A non-existent material_id must return 404 (not 200 with empty list)."""
    response = await async_client.get(API.format(material_id=uuid.uuid4()))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Response shape — `data: [...], meta: {...}`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_response_shape(async_client, db_session) -> None:
    """Returned rows must match the frontend MaterialProperty interface exactly."""
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session, title="ASM Handbook Vol. 2")
    cat = await _seed_category(db_session)
    unit = await _seed_unit(db_session)
    pt = await _seed_property_type(db_session, cat.id, unit_id=unit.id, name="密度", slug="density")
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt.id, unit_id=unit.id, value_scalar=5.68)

    response = await async_client.get(API.format(material_id=mat.id))

    assert response.status_code == 200
    inner = response.json()["data"]
    assert inner["meta"] == {"total": 1, "page": 1, "limit": 50}
    assert len(inner["data"]) == 1

    row = inner["data"][0]
    assert set(row.keys()) == {"id", "name", "value", "unit", "source", "confidence"}
    assert row["name"] == "密度"
    assert row["value"] == "5.68"
    assert row["unit"] == "W/(m·K)"  # unit symbol
    assert row["source"] == "ASM Handbook Vol. 2"
    assert isinstance(row["confidence"], (int, float))
    assert 0.0 <= row["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Isolation between materials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_isolates_by_material(async_client, db_session) -> None:
    mat_a = await _seed_material(db_session, name="Mat-A")
    mat_b = await _seed_material(db_session, name="Mat-B")
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds_a = await _seed_dataset(db_session, mat_a.id, src.id)
    ds_b = await _seed_dataset(db_session, mat_b.id, src.id)
    await _seed_measurement(db_session, ds_a.id, pt.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds_b.id, pt.id, value_scalar=2.0)
    await _seed_measurement(db_session, ds_b.id, pt.id, value_scalar=3.0)

    response = await async_client.get(API.format(material_id=mat_a.id))

    assert response.status_code == 200
    inner = response.json()["data"]
    assert inner["meta"]["total"] == 1
    assert len(inner["data"]) == 1
    assert inner["data"][0]["value"] == "1.0"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_pagination(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id, src.id)
    for i in range(5):
        await _seed_measurement(db_session, ds.id, pt.id, value_scalar=float(i))

    page_1 = await async_client.get(API.format(material_id=mat.id) + "?page=1&limit=2")
    page_2 = await async_client.get(API.format(material_id=mat.id) + "?page=2&limit=2")
    page_3 = await async_client.get(API.format(material_id=mat.id) + "?page=3&limit=2")

    assert page_1.status_code == 200
    assert page_2.status_code == 200
    assert page_3.status_code == 200

    p1 = page_1.json()["data"]
    p2 = page_2.json()["data"]
    p3 = page_3.json()["data"]

    assert p1["meta"] == {"total": 5, "page": 1, "limit": 2}
    assert p2["meta"] == {"page": 2, "limit": 2, "total": 5}
    assert p3["meta"] == {"page": 3, "limit": 2, "total": 5}

    assert len(p1["data"]) == 2
    assert len(p2["data"]) == 2
    assert len(p3["data"]) == 1

    # Verify no row appears on two pages
    seen_ids = {row["id"] for row in p1["data"]} | {row["id"] for row in p2["data"]} | {row["id"] for row in p3["data"]}
    assert len(seen_ids) == 5


@pytest.mark.asyncio
async def test_list_material_properties_page_beyond_total_returns_empty(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=1.0)

    response = await async_client.get(API.format(material_id=mat.id) + "?page=99&limit=10")

    assert response.status_code == 200
    inner = response.json()["data"]
    assert inner["data"] == []
    assert inner["meta"]["total"] == 1


# ---------------------------------------------------------------------------
# Filter — name contains (case-insensitive)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_filter_by_name(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt_density = await _seed_property_type(db_session, cat.id, name="密度", slug="density")
    pt_melting = await _seed_property_type(db_session, cat.id, name="熔点", slug="melting")
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt_density.id, value_scalar=5.68)
    await _seed_measurement(db_session, ds.id, pt_melting.id, value_scalar=2700.0)

    response = await async_client.get(API.format(material_id=mat.id) + "?filter=密度")

    assert response.status_code == 200
    inner = response.json()["data"]
    assert inner["meta"]["total"] == 1
    assert inner["data"][0]["name"] == "密度"


# ---------------------------------------------------------------------------
# Sort — by name, value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_sort_by_name_asc(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt_a = await _seed_property_type(db_session, cat.id, name="Alpha", slug="alpha")
    pt_b = await _seed_property_type(db_session, cat.id, name="Bravo", slug="bravo")
    pt_c = await _seed_property_type(db_session, cat.id, name="Charlie", slug="charlie")
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt_c.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds.id, pt_a.id, value_scalar=2.0)
    await _seed_measurement(db_session, ds.id, pt_b.id, value_scalar=3.0)

    response = await async_client.get(API.format(material_id=mat.id) + "?sort=name&order=asc")

    assert response.status_code == 200
    names = [row["name"] for row in response.json()["data"]["data"]]
    assert names == ["Alpha", "Bravo", "Charlie"]


@pytest.mark.asyncio
async def test_list_material_properties_sort_by_value_desc(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt_a = await _seed_property_type(db_session, cat.id, name="Alpha", slug="alpha")
    pt_b = await _seed_property_type(db_session, cat.id, name="Bravo", slug="bravo")
    pt_c = await _seed_property_type(db_session, cat.id, name="Charlie", slug="charlie")
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt_a.id, value_scalar=2.0)
    await _seed_measurement(db_session, ds.id, pt_b.id, value_scalar=10.0)
    await _seed_measurement(db_session, ds.id, pt_c.id, value_scalar=5.0)

    response = await async_client.get(API.format(material_id=mat.id) + "?sort=value&order=desc")

    assert response.status_code == 200
    values = [row["value"] for row in response.json()["data"]["data"]]
    assert values == ["10.0", "5.0", "2.0"]


# ---------------------------------------------------------------------------
# Value formatting — non-scalar types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_material_properties_range_value_formatted(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id, value_type="range")
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(
        db_session,
        ds.id,
        pt.id,
        value_scalar=None,
        value_min=4.0,
        value_max=6.0,
    )

    response = await async_client.get(API.format(material_id=mat.id))

    assert response.status_code == 200
    row = response.json()["data"]["data"][0]
    assert row["value"] == "4.0–6.0"


@pytest.mark.asyncio
async def test_list_material_properties_unit_null_when_measurement_has_no_unit(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    src = await _seed_source(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id, unit_id=None)
    ds = await _seed_dataset(db_session, mat.id, src.id)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=1.0, unit_id=None)

    response = await async_client.get(API.format(material_id=mat.id))

    assert response.status_code == 200
    row = response.json()["data"]["data"][0]
    assert row["unit"] is None
