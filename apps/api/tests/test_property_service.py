"""Tests for the property service layer (NFM-697).

Covers: list_measurements, get_measurement, create_measurement,
update_measurement, get_measurement_stats.
Uses the db_session fixture from conftest.py (SQLite in-memory).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.property import (
    PropertyMeasurementCreate,
    PropertyMeasurementUpdate,
)
from nfm_db.services.property_service import (
    create_measurement,
    get_measurement,
    get_measurement_stats,
    list_measurements,
    update_measurement,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"{_counter}"


async def _seed_category(
    db: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
    **overrides,
) -> PropertyCategory:
    uid = _next_id()
    defaults = dict(name=name or f"cat-{uid}", slug=slug or f"cat-{uid}")
    defaults.update(overrides)
    cat = PropertyCategory(**defaults)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def _seed_type(
    db: AsyncSession,
    *,
    category: PropertyCategory | None = None,
    name: str | None = None,
    slug: str | None = None,
    value_type="scalar",
    **overrides,
) -> PropertyType:
    cat = category or await _seed_category(db)
    uid = _next_id()
    defaults = dict(
        category_id=cat.id,
        name=name or f"ptype-{uid}",
        slug=slug or f"ptype-{uid}",
        value_type=value_type,
    )
    defaults.update(overrides)
    ptype = PropertyType(**defaults)
    db.add(ptype)
    await db.commit()
    await db.refresh(ptype)
    return ptype


async def _seed_material(
    db: AsyncSession,
    *,
    name: str | None = None,
    formula: str | None = None,
    **overrides,
) -> Material:
    uid = _next_id()
    defaults = dict(name=name or f"mat-{uid}", formula=formula or f"mat-{uid}")
    defaults.update(overrides)
    mat = Material(**defaults)
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return mat


async def _seed_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    title: str | None = None,
    source_type="journal_article",
    year=2024,
    **overrides,
) -> DataSource:
    uid = _next_id()
    defaults = dict(
        doi=doi or f"10.1000/test-{uid}",
        title=title or f"Paper {uid}",
        source_type=source_type,
        year=year,
    )
    defaults.update(overrides)
    src = DataSource(**defaults)
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _seed_dataset(
    db: AsyncSession,
    *,
    material: Material | None = None,
    source: DataSource | None = None,
    title: str | None = None,
    **overrides,
) -> Dataset:
    mat = material or await _seed_material(db)
    src = source or await _seed_source(db)
    uid = _next_id()
    defaults = dict(
        material_id=mat.id,
        source_id=src.id,
        title=title or f"Dataset {uid}",
    )
    defaults.update(overrides)
    ds = Dataset(**defaults)
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def _seed_measurement(
    db: AsyncSession,
    *,
    dataset: Dataset | None = None,
    property_type: PropertyType | None = None,
    value_scalar: float | None = 3135.0,
    **overrides,
) -> PropertyMeasurement:
    ds = dataset or await _seed_dataset(db)
    ptype = property_type or await _seed_type(db)
    defaults = dict(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_scalar=value_scalar,
    )
    defaults.update(overrides)
    meas = PropertyMeasurement(**defaults)
    db.add(meas)
    await db.commit()
    await db.refresh(meas)
    return meas


async def _seed_condition(
    db: AsyncSession,
    *,
    measurement: PropertyMeasurement,
    temperature: float | None = 298.15,
    pressure: float | None = None,
    **overrides,
) -> MeasurementCondition:
    defaults = dict(
        measurement_id=measurement.id,
        temperature=temperature,
        pressure=pressure,
    )
    defaults.update(overrides)
    cond = MeasurementCondition(**defaults)
    db.add(cond)
    await db.commit()
    await db.refresh(cond)
    return cond


# ---------------------------------------------------------------------------
# list_measurements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_measurements_returns_paginated_results(db_session: AsyncSession):
    """list_measurements returns PaginatedResponse with correct pagination."""
    for _ in range(3):
        await _seed_measurement(db_session)

    result = await list_measurements(db_session, page=1, per_page=2)

    assert result.total == 3
    assert result.page == 1
    assert result.limit == 2
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_list_measurements_page_2(db_session: AsyncSession):
    """Second page returns remaining items."""
    for _ in range(3):
        await _seed_measurement(db_session)

    result = await list_measurements(db_session, page=2, per_page=2)

    assert result.total == 3
    assert result.page == 2
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_list_measurements_empty(db_session: AsyncSession):
    """Empty database returns zero results."""
    result = await list_measurements(db_session)

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_measurements_filter_by_material_id(db_session: AsyncSession):
    """Filtering by material_id returns only matching measurements."""
    mat_a = await _seed_material(db_session)
    mat_b = await _seed_material(db_session)
    ds_a = await _seed_dataset(db_session, material=mat_a)
    ds_b = await _seed_dataset(db_session, material=mat_b)
    ptype = await _seed_type(db_session)

    await _seed_measurement(db_session, dataset=ds_a, property_type=ptype, value_scalar=100.0)
    await _seed_measurement(db_session, dataset=ds_b, property_type=ptype, value_scalar=200.0)

    result = await list_measurements(db_session, material_id=mat_a.id)

    assert result.total == 1
    assert result.items[0].value_scalar == 100.0


@pytest.mark.asyncio
async def test_list_measurements_filter_by_property_type_id(db_session: AsyncSession):
    """Filtering by property_type_id returns only matching measurements."""
    ptype_a = await _seed_type(db_session)
    ptype_b = await _seed_type(db_session)
    ds = await _seed_dataset(db_session)

    await _seed_measurement(db_session, dataset=ds, property_type=ptype_a, value_scalar=3.0)
    await _seed_measurement(db_session, dataset=ds, property_type=ptype_b, value_scalar=10.0)

    result = await list_measurements(db_session, property_type_id=ptype_a.id)

    assert result.total == 1
    assert result.items[0].property_type_id == ptype_a.id


@pytest.mark.asyncio
async def test_list_measurements_sort_and_order(db_session: AsyncSession):
    """Sort parameter is accepted and returns correct total."""
    await _seed_measurement(db_session, value_scalar=10.0)
    await _seed_measurement(db_session, value_scalar=20.0)

    # desc
    desc_result = await list_measurements(db_session, sort="created_at", order="desc")
    assert desc_result.total == 2
    assert len(desc_result.items) == 2

    # asc
    asc_result = await list_measurements(db_session, sort="created_at", order="asc")
    assert asc_result.total == 2
    assert len(asc_result.items) == 2


# ---------------------------------------------------------------------------
# get_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_measurement_with_conditions(db_session: AsyncSession):
    """get_measurement returns measurement with conditions and dataset."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    meas = await _seed_measurement(db_session, dataset=ds, property_type=ptype, value_scalar=42.0)
    await _seed_condition(db_session, measurement=meas, temperature=298.15)
    await _seed_condition(db_session, measurement=meas, temperature=573.15)

    result = await get_measurement(db_session, meas.id)

    assert result is not None
    assert result.id == meas.id
    assert result.value_scalar == 42.0
    assert result.dataset is not None
    assert result.dataset.id == ds.id
    assert len(result.conditions) == 2


@pytest.mark.asyncio
async def test_get_measurement_not_found(db_session: AsyncSession):
    """get_measurement returns None for missing UUID."""
    result = await get_measurement(db_session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# create_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_measurement_with_scalar(db_session: AsyncSession):
    """create_measurement persists a scalar value measurement."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    data = PropertyMeasurementCreate(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_scalar=42.5,
    )

    result = await create_measurement(db_session, data)

    assert result.id is not None
    assert result.value_scalar == 42.5
    assert result.dataset_id == ds.id


@pytest.mark.asyncio
async def test_create_measurement_with_range(db_session: AsyncSession):
    """create_measurement persists a range value measurement."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)
    data = PropertyMeasurementCreate(
        dataset_id=ds.id,
        property_type_id=ptype.id,
        value_min=100.0,
        value_max=200.0,
    )

    result = await create_measurement(db_session, data)

    assert result.value_min == 100.0
    assert result.value_max == 200.0


@pytest.mark.asyncio
async def test_create_measurement_validates_at_least_one_value(db_session: AsyncSession):
    """Pydantic validates at least one value_* field at construction."""
    ds = await _seed_dataset(db_session)
    ptype = await _seed_type(db_session)

    with pytest.raises(ValidationError):
        PropertyMeasurementCreate(
            dataset_id=ds.id,
            property_type_id=ptype.id,
        )


# ---------------------------------------------------------------------------
# update_measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_measurement_scalar(db_session: AsyncSession):
    """update_measurement changes scalar value."""
    meas = await _seed_measurement(db_session, value_scalar=100.0)
    data = PropertyMeasurementUpdate(value_scalar=999.9)

    result = await update_measurement(db_session, meas.id, data)

    assert result.value_scalar == 999.9


@pytest.mark.asyncio
async def test_update_measurement_notes(db_session: AsyncSession):
    """update_measurement can set notes."""
    meas = await _seed_measurement(db_session, value_scalar=1.0)
    data = PropertyMeasurementUpdate(notes="Updated note")

    result = await update_measurement(db_session, meas.id, data)

    assert result.notes == "Updated note"


@pytest.mark.asyncio
async def test_update_measurement_not_found(db_session: AsyncSession):
    """update_measurement returns None for missing measurement."""
    data = PropertyMeasurementUpdate(value_scalar=1.0)

    result = await update_measurement(db_session, uuid.uuid4(), data)

    assert result is None


# ---------------------------------------------------------------------------
# get_measurement_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_measurement_stats_returns_aggregated_counts(db_session: AsyncSession):
    """get_measurement_stats returns total, by_category, by_material."""
    cat_thermal = await _seed_category(db_session)
    cat_mech = await _seed_category(db_session)
    ptype_thermal = await _seed_type(db_session, category=cat_thermal)
    ptype_mech = await _seed_type(db_session, category=cat_mech)
    mat_uo2 = await _seed_material(db_session)
    mat_mox = await _seed_material(db_session)

    # 2 thermal measurements for UO2, 1 mechanical for MOX
    for _ in range(2):
        ds = await _seed_dataset(db_session, material=mat_uo2)
        await _seed_measurement(db_session, dataset=ds, property_type=ptype_thermal)

    ds_mox = await _seed_dataset(db_session, material=mat_mox)
    await _seed_measurement(db_session, dataset=ds_mox, property_type=ptype_mech)

    stats = await get_measurement_stats(db_session)

    assert stats.total_measurements == 3
    assert len(stats.by_category) == 2
    assert len(stats.by_material) == 2

    cat_names = {c.category for c in stats.by_category}
    assert cat_names == {cat_thermal.name, cat_mech.name}

    mat_names = {m.material_name for m in stats.by_material}
    assert mat_names == {mat_uo2.name, mat_mox.name}


@pytest.mark.asyncio
async def test_get_measurement_stats_empty(db_session: AsyncSession):
    """get_measurement_stats returns zeros when empty."""
    stats = await get_measurement_stats(db_session)

    assert stats.total_measurements == 0
    assert stats.by_category == []
    assert stats.by_material == []
