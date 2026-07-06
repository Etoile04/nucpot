"""Tests for the properties REST API router (NFM-697).

Covers: GET /properties, GET /properties/{id}, POST /properties,
PATCH /properties/{id}, GET /properties/stats.
Uses the async_client fixture from conftest.py (FastAPI test client + SQLite).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.models import DataSource as DataSourceModel
from nfm_db.models import Material as MaterialModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_seed_counter = [0]


async def _seed_material(db: AsyncSession, *, name="UO2", formula="UO2") -> MaterialModel:
    mat = MaterialModel(name=name, formula=formula)
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


async def _seed_source(db: AsyncSession, *, title="Test Source") -> DataSourceModel:
    source = DataSourceModel(title=title, source_type="journal_article", year=2020)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def _seed_dataset(
    db: AsyncSession, *, material_id: uuid.UUID, source_id: uuid.UUID
) -> Dataset:
    dataset = Dataset(
        material_id=material_id,
        source_id=source_id,
        title="Test Dataset",
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return dataset


async def _seed_property_type(
    db: AsyncSession, *, name: str | None = None, slug: str | None = None
) -> PropertyType:
    # Create unique category and property type
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter

    category = PropertyCategory(name=f"Category{counter}", slug=f"category{counter}")
    db.add(category)
    await db.commit()
    await db.refresh(category)

    prop_type = PropertyType(
        category_id=category.id,
        name=name or f"Property{counter}",
        slug=slug or f"property{counter}",
        value_type="scalar",
    )
    db.add(prop_type)
    await db.commit()
    await db.refresh(prop_type)
    return prop_type


async def _seed_measurement(
    db: AsyncSession, *, dataset_id: uuid.UUID, property_type_id: uuid.UUID, **overrides
) -> PropertyMeasurement:
    defaults = dict(
        dataset_id=dataset_id,
        property_type_id=property_type_id,
        value_scalar=100.0,
    )
    defaults.update(overrides)
    measurement = PropertyMeasurement(**defaults)
    db.add(measurement)
    await db.commit()
    await db.refresh(measurement)
    return measurement


# ============================================================
# GET /properties
# ============================================================


class TestListPropertiesEndpoint:
    """Tests for GET /properties."""

    @pytest.mark.asyncio
    async def test_list_returns_success_envelope(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)
        await _seed_measurement(db_session, dataset_id=dataset.id, property_type_id=prop_type.id)

        resp = await async_client.get("/api/v1/properties")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert "items" in body["data"]
        assert "total" in body["data"]

    @pytest.mark.asyncio
    async def test_list_paginates_correctly(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)

        # Create 5 measurements
        for _ in range(5):
            await _seed_measurement(
                db_session, dataset_id=dataset.id, property_type_id=prop_type.id
            )

        # Page 1 with per_page=2
        resp = await async_client.get("/api/v1/properties?page=1&per_page=2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 5
        assert body["data"]["pages"] == 3  # ceil(5/2)
        assert len(body["data"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_list_filters_by_material_id(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat1 = await _seed_material(db_session, name="Material1")
        mat2 = await _seed_material(db_session, name="Material2")
        src = await _seed_source(db_session)
        dataset1 = await _seed_dataset(db_session, material_id=mat1.id, source_id=src.id)
        dataset2 = await _seed_dataset(db_session, material_id=mat2.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)

        await _seed_measurement(db_session, dataset_id=dataset1.id, property_type_id=prop_type.id)
        await _seed_measurement(db_session, dataset_id=dataset2.id, property_type_id=prop_type.id)

        resp = await async_client.get(f"/api/v1/properties?material_id={mat1.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_filters_by_property_type_id(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type1 = await _seed_property_type(db_session, name="Type1", slug="type1")
        prop_type2 = await _seed_property_type(db_session, name="Type2", slug="type2")

        await _seed_measurement(db_session, dataset_id=dataset.id, property_type_id=prop_type1.id)
        await _seed_measurement(db_session, dataset_id=dataset.id, property_type_id=prop_type2.id)

        resp = await async_client.get(f"/api/v1/properties?property_type_id={prop_type1.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_empty_database(
        self,
        async_client: AsyncClient,
    ) -> None:
        resp = await async_client.get("/api/v1/properties")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []


# ============================================================
# GET /properties/{id}
# ============================================================


class TestGetPropertyEndpoint:
    """Tests for GET /properties/{id}."""

    @pytest.mark.asyncio
    async def test_get_returns_measurement_detail(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)
        measurement = await _seed_measurement(
            db_session, dataset_id=dataset.id, property_type_id=prop_type.id
        )

        # Add a condition
        condition = MeasurementCondition(
            measurement_id=measurement.id,
            temperature=300.0,
        )
        db_session.add(condition)
        await db_session.commit()

        resp = await async_client.get(f"/api/v1/properties/{measurement.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == str(measurement.id)
        assert "conditions" in body["data"]
        assert "dataset" in body["data"]
        assert len(body["data"]["conditions"]) == 1
        assert body["data"]["dataset"]["id"] == str(dataset.id)

    @pytest.mark.asyncio
    async def test_get_returns_404_for_missing(
        self,
        async_client: AsyncClient,
    ) -> None:
        fake_id = uuid.uuid4()
        resp = await async_client.get(f"/api/v1/properties/{fake_id}")

        assert resp.status_code == 404


# ============================================================
# POST /properties
# ============================================================


class TestCreatePropertyEndpoint:
    """Tests for POST /properties."""

    @pytest.mark.asyncio
    async def test_create_returns_201_with_data(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)

        payload = {
            "dataset_id": str(dataset.id),
            "property_type_id": str(prop_type.id),
            "value_scalar": 150.0,
        }

        resp = await async_client.post("/api/v1/properties", json=payload)

        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["dataset_id"] == str(dataset.id)
        assert body["data"]["value_scalar"] == 150.0

    @pytest.mark.asyncio
    async def test_create_validates_required_fields(
        self,
        async_client: AsyncClient,
    ) -> None:
        # Missing required fields
        payload = {"dataset_id": str(uuid.uuid4())}

        resp = await async_client.post("/api/v1/properties", json=payload)

        # Should get 422 validation error or 422 from Pydantic
        assert resp.status_code == 422


# ============================================================
# PATCH /properties/{id}
# ============================================================


class TestUpdatePropertyEndpoint:
    """Tests for PATCH /properties/{id}."""

    @pytest.mark.asyncio
    async def test_patch_modifies_existing_measurement(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session)
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)
        prop_type = await _seed_property_type(db_session)
        measurement = await _seed_measurement(
            db_session, dataset_id=dataset.id, property_type_id=prop_type.id, value_scalar=100.0
        )

        payload = {"value_scalar": 999.9}

        resp = await async_client.patch(f"/api/v1/properties/{measurement.id}", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["value_scalar"] == 999.9

    @pytest.mark.asyncio
    async def test_patch_returns_404_for_missing(
        self,
        async_client: AsyncClient,
    ) -> None:
        fake_id = uuid.uuid4()
        payload = {"value_scalar": 1.0}

        resp = await async_client.patch(f"/api/v1/properties/{fake_id}", json=payload)

        assert resp.status_code == 404


# ============================================================
# GET /properties/stats
# ============================================================


class TestPropertiesStatsEndpoint:
    """Tests for GET /properties/stats."""

    @pytest.mark.asyncio
    async def test_stats_returns_aggregated_data(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session, name="UO2")
        src = await _seed_source(db_session)
        dataset = await _seed_dataset(db_session, material_id=mat.id, source_id=src.id)

        # Create a category and property type
        cat = PropertyCategory(name="thermal", slug="thermal")
        db_session.add(cat)
        await db_session.commit()
        await db_session.refresh(cat)

        prop_type = PropertyType(
            category_id=cat.id,
            name="conductivity",
            slug="conductivity",
            value_type="scalar",
        )
        db_session.add(prop_type)
        await db_session.commit()
        await db_session.refresh(prop_type)

        # Create 3 measurements
        for i in range(3):
            meas = PropertyMeasurement(
                dataset_id=dataset.id,
                property_type_id=prop_type.id,
                value_scalar=100.0 + i,
            )
            db_session.add(meas)
        await db_session.commit()

        resp = await async_client.get("/api/v1/properties/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert body["data"]["total_measurements"] == 3
        assert "by_category" in body["data"]
        assert "by_material" in body["data"]

    @pytest.mark.asyncio
    async def test_stats_empty_database(
        self,
        async_client: AsyncClient,
    ) -> None:
        resp = await async_client.get("/api/v1/properties/stats")

        # Stats endpoint should return 200 even with empty data
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total_measurements"] == 0
        assert body["data"]["by_category"] == []
        assert body["data"]["by_material"] == []
