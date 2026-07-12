"""Integration tests for /api/v1/seed endpoints (NFM-702)."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import (
    Dataset,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services.seed_service import _batch_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_batch_store():
    """Isolate the in-memory batch store between tests."""
    _batch_store.clear()
    yield
    _batch_store.clear()


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# POST /api/v1/seed/batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_start(async_client) -> None:
    payload = {"dois": ["10.1016/j.jnucmat.2020.01.001"]}
    response = await async_client.post("/api/v1/seed/batch", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "batch_id" in data
    assert data["total"] == 1
    assert data["message"] == "Batch started"


@pytest.mark.asyncio
async def test_batch_start_multiple_dois(async_client) -> None:
    dois = [f"10.1016/j.example.{i}" for i in range(5)]
    payload = {"dois": dois}
    response = await async_client.post("/api/v1/seed/batch", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_batch_start_empty_dois_422(async_client) -> None:
    payload = {"dois": []}
    response = await async_client.post("/api/v1/seed/batch", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_batch_start_missing_dois_422(async_client) -> None:
    payload = {}
    response = await async_client.post("/api/v1/seed/batch", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/seed/status/{batch_id}
#
# Note: start_batch runs to completion synchronously, so the batch
# store always reflects the final state (completed or failed).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_status_found(async_client) -> None:
    payload = {"dois": ["10.1016/j.test.001"]}
    batch_resp = await async_client.post("/api/v1/seed/batch", json=payload)
    batch_id = batch_resp.json()["data"]["batch_id"]

    response = await async_client.get(f"/api/v1/seed/status/{batch_id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["batch_id"] == batch_id
    assert data["total"] == 1
    # Batch ran to completion, so completed == total
    assert data["completed"] + data["failed"] == data["total"]


@pytest.mark.asyncio
async def test_batch_status_not_found_404(async_client) -> None:
    response = await async_client.get(f"/api/v1/seed/status/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_batch_status_shape(async_client) -> None:
    payload = {"dois": ["10.1016/j.test.001", "10.1016/j.test.002"]}
    batch_resp = await async_client.post("/api/v1/seed/batch", json=payload)
    batch_id = batch_resp.json()["data"]["batch_id"]

    response = await async_client.get(f"/api/v1/seed/status/{batch_id}")
    data = response.json()["data"]
    assert "batch_id" in data
    assert "total" in data
    assert "completed" in data
    assert "failed" in data
    assert "in_progress" in data
    assert "errors" in data
    assert isinstance(data["errors"], list)


# ---------------------------------------------------------------------------
# GET /api/v1/seed/quality
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_empty(async_client) -> None:
    response = await async_client.get("/api/v1/seed/quality")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total_extracted"] == 0
    assert data["total_measurements"] == 0
    assert data["by_category"] == []
    assert data["avg_confidence"] == 0.0


@pytest.mark.asyncio
async def test_quality_with_data(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session, name="Thermal", slug="thermal")
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds.id, pt.id, value_scalar=2.0)

    response = await async_client.get("/api/v1/seed/quality")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_measurements"] == 2
    assert data["total_extracted"] == 1  # 1 unique dataset
    assert len(data["by_category"]) == 1
    assert data["by_category"][0]["category"] == "Thermal"
    assert data["by_category"][0]["count"] == 2


@pytest.mark.asyncio
async def test_quality_multiple_categories(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat_thermal = await _seed_category(db_session, name="Thermal", slug="thermal")
    cat_mech = await _seed_category(db_session, name="Mechanical", slug="mechanical")
    pt_thermal = await _seed_property_type(db_session, cat_thermal.id, slug="tc")
    pt_mech = await _seed_property_type(db_session, cat_mech.id, slug="ym")
    ds = await _seed_dataset(db_session, mat.id)
    await _seed_measurement(db_session, ds.id, pt_thermal.id, value_scalar=1.0)
    await _seed_measurement(db_session, ds.id, pt_mech.id, value_scalar=2.0)
    await _seed_measurement(db_session, ds.id, pt_mech.id, value_scalar=3.0)

    response = await async_client.get("/api/v1/seed/quality")
    data = response.json()["data"]
    assert data["total_measurements"] == 3
    assert len(data["by_category"]) == 2
    cat_names = {c["category"] for c in data["by_category"]}
    assert "Thermal" in cat_names
    assert "Mechanical" in cat_names


@pytest.mark.asyncio
async def test_quality_shape_is_correct(async_client) -> None:
    response = await async_client.get("/api/v1/seed/quality")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "total_extracted" in data
    assert "total_measurements" in data
    assert "by_category" in data
    assert "avg_confidence" in data


# ---------------------------------------------------------------------------
# PATCH /api/v1/seed/review/{measurement_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_approve(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    pm = await _seed_measurement(db_session, ds.id, pt.id)

    payload = {"review_status": "approved", "reviewer_note": "Verified OK"}
    response = await async_client.patch(f"/api/v1/seed/review/{pm.id}", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["review_status"] == "approved"
    assert data["reviewer_note"] == "Verified OK"


@pytest.mark.asyncio
async def test_review_reject(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    pm = await _seed_measurement(db_session, ds.id, pt.id)

    payload = {"review_status": "rejected", "reviewer_note": "Value seems off"}
    response = await async_client.patch(f"/api/v1/seed/review/{pm.id}", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["review_status"] == "rejected"


@pytest.mark.asyncio
async def test_review_without_note(async_client, db_session) -> None:
    mat = await _seed_material(db_session)
    cat = await _seed_category(db_session)
    pt = await _seed_property_type(db_session, cat.id)
    ds = await _seed_dataset(db_session, mat.id)
    pm = await _seed_measurement(db_session, ds.id, pt.id)

    payload = {"review_status": "approved"}
    response = await async_client.patch(f"/api/v1/seed/review/{pm.id}", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["review_status"] == "approved"
    assert data["reviewer_note"] is None


@pytest.mark.asyncio
async def test_review_404(async_client) -> None:
    payload = {"review_status": "approved"}
    response = await async_client.patch(f"/api/v1/seed/review/{uuid.uuid4()}", json=payload)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_review_invalid_status_422(async_client) -> None:
    payload = {"review_status": "maybe"}
    response = await async_client.patch(f"/api/v1/seed/review/{uuid.uuid4()}", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_review_missing_status_422(async_client) -> None:
    payload = {"reviewer_note": "no status"}
    response = await async_client.patch(f"/api/v1/seed/review/{uuid.uuid4()}", json=payload)
    assert response.status_code == 422
