"""Integration tests for /api/v1/properties endpoints."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import (
    Dataset,
    Material,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)


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


async def _seed_category(db_session, **overrides):
    defaults = dict(name="Thermal", slug="thermal")
    defaults.update(overrides)
    cat = PropertyCategory(**defaults)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


async def _seed_property_type(db_session, category_id, **overrides):
    defaults = dict(
        category_id=category_id,
        name="Thermal Conductivity",
        slug="thermal-conductivity",
        value_type="scalar",
    )
    defaults.update(overrides)
    pt = PropertyType(**defaults)
    db_session.add(pt)
    await db_session.commit()
    await db_session.refresh(pt)
    return pt


async def _seed_dataset(db_session, material_id, **overrides):
    from nfm_db.models import DataSource

    defaults = dict(
        material_id=material_id,
        title="Test Dataset",
        is_verified=False,
    )
    defaults.update(overrides)
    # Ensure a source exists if source_id is not set
    if "source_id" not in defaults:
        src = DataSource(title="Test Paper", source_type="journal_article")
        db_session.add(src)
        await db_session.flush()
        defaults["source_id"] = src.id

    ds = Dataset(**defaults)
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)
    return ds


async def _seed_measurement(db_session, dataset_id, property_type_id, **overrides):
    defaults = dict(
        dataset_id=dataset_id,
        property_type_id=property_type_id,
        value_scalar=3.5,
        uncertainty=0.1,
    )
    defaults.update(overrides)
    pm = PropertyMeasurement(**defaults)
    db_session.add(pm)
    await db_session.commit()
    await db_session.refresh(pm)
    return pm


async def _seed_condition(db_session, measurement_id, **overrides):
    defaults = dict(
        measurement_id=measurement_id,
        temperature=298.0,
        pressure=0.1,
    )
    defaults.update(overrides)
    cond = MeasurementCondition(**defaults)
    db_session.add(cond)
    await db_session.commit()
    await db_session.refresh(cond)
    return cond


# ---------------------------------------------------------------------------
# GET /api/v1/properties — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_measurements_empty(async_client) -> None:
    response = await async_client.get("/api/v1/properties")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_measurements_paginated(
    async_client, db_session
) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    for i in range(3):
        await _seed_measurement(db_session, ds.id, pt.id, value_scalar=float(i + 1))

    response = await async_client.get("/api/v1/properties?per_page=2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_measurements_filter_by_material(
    async_client, db_session
) -> None:
    mat_a = await _seed_material(db_session, name="Mat-A")
    mat_b = await _seed_material(db_session, name="Mat-B")
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds_a = await _seed_dataset(db_session, mat_a.id)
    ds_b = await _seed_dataset(db_session, mat_b.id)
    await _seed_measurement(db_session, ds_a.id, pt.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds_b.id, pt.id, value_scalar=2.0)

    response = await async_client.get(
        f"/api/v1/properties?material_id={mat_a.id}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_measurements_filter_by_property_type(
    async_client, db_session
) -> None:
    mat = await _seed_material(db_session)
    cat1 = await _seed_category(db_session, name="Thermal", slug="thermal")
    cat2 = await _seed_category(db_session, name="Mechanical", slug="mechanical")
    pt1 = await _seed_property_type(db_session, cat1.id, slug="tc")
    pt2 = await _seed_property_type(db_session, cat2.id, slug="ym")
    ds = await _seed_dataset(db_session, mat.id)
    await _seed_measurement(db_session, ds.id, pt1.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds.id, pt2.id, value_scalar=2.0)

    response = await async_client.get(
        f"/api/v1/properties?property_type_id={pt1.id}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_measurements_pagination_edge_cases(
    async_client, db_session
) -> None:
    response = await async_client.get("/api/v1/properties?page=999")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []

    response = await async_client.get("/api/v1/properties?per_page=100")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/properties/{id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_measurement_detail(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    pm = await _seed_measurement(db_session, ds.id, pt.id, value_scalar=10.5)
    await _seed_condition(db_session, pm.id, temperature=500.0)

    response = await async_client.get(f"/api/v1/properties/{pm.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["value_scalar"] == 10.5
    assert len(data["conditions"]) == 1
    assert data["conditions"][0]["temperature"] == 500.0
    assert data["dataset"] is not None
    assert data["dataset"]["title"] == "Test Dataset"


@pytest.mark.asyncio
async def test_get_measurement_404(async_client) -> None:
    response = await async_client.get(
        f"/api/v1/properties/{uuid.uuid4()}"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/properties — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_measurement_scalar(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    from nfm_db.models import DataSource

    src = DataSource(title="Src", source_type="journal_article")
    db_session.add(src)
    await db_session.flush()
    ds = Dataset(material_id=mat.id, source_id=src.id, title="DS")
    db_session.add(ds)
    await db_session.flush()
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)

    payload = {
        "dataset_id": str(ds.id),
        "property_type_id": str(pt.id),
        "value_scalar": 42.0,
        "uncertainty": 0.5,
    }
    response = await async_client.post("/api/v1/properties", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["value_scalar"] == 42.0


@pytest.mark.asyncio
async def test_create_measurement_range(async_client) -> None:
    payload = {
        "dataset_id": str(uuid.uuid4()),
        "property_type_id": str(uuid.uuid4()),
        "value_min": 1.0,
        "value_max": 5.0,
    }
    # Will fail with FK constraint — but validates schema shape
    response = await async_client.post("/api/v1/properties", json=payload)
    # May 500 on FK, but should not be 422 (schema valid)
    assert response.status_code != 422


@pytest.mark.asyncio
async def test_create_measurement_invalid_no_value(async_client) -> None:
    payload = {
        "dataset_id": str(uuid.uuid4()),
        "property_type_id": str(uuid.uuid4()),
    }
    response = await async_client.post("/api/v1/properties", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/v1/properties/{id} — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_measurement(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    pm = await _seed_measurement(db_session, ds.id, pt.id, value_scalar=1.0)

    payload = {"value_scalar": 99.9, "notes": "Updated"}
    response = await async_client.patch(
        f"/api/v1/properties/{pm.id}", json=payload
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["value_scalar"] == 99.9
    assert data["notes"] == "Updated"


@pytest.mark.asyncio
async def test_update_measurement_404(async_client) -> None:
    payload = {"notes": "Ghost"}
    response = await async_client.patch(
        f"/api/v1/properties/{uuid.uuid4()}", json=payload
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/properties/stats — aggregate stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_empty(async_client) -> None:
    response = await async_client.get("/api/v1/properties/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_measurements"] == 0
    assert data["by_category"] == []
    assert data["by_material"] == []


@pytest.mark.asyncio
async def test_stats_with_data(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session, name="Thermal", slug="thermal")
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=2.0)

    response = await async_client.get("/api/v1/properties/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_measurements"] == 2
    assert len(data["by_category"]) == 1
    assert data["by_category"][0]["category"] == "Thermal"
    assert data["by_category"][0]["count"] == 2
    assert len(data["by_material"]) == 1
    assert data["by_material"][0]["material_name"] == "UO2"


@pytest.mark.asyncio
async def test_stats_shape_is_correct(async_client) -> None:
    """Verify the stats response has expected top-level keys."""
    response = await async_client.get("/api/v1/properties/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "total_measurements" in data
    assert "by_category" in data
    assert "by_material" in data
