"""Integration tests for the ``attribution`` block on
``GET /api/v1/materials/{material_id}/properties``.

NFM-4146 integration slice: the React property table (§3.3 primary
surface) is fed by this endpoint — NOT by ``/properties/{id}/measurements``
— so every row here must carry the same §5.2 attribution block the
dedicated endpoint emits, or the frontend ``<DataLossNotice>`` wiring
never fires.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MaterialCategory,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services import attribution_flag

# ---------------------------------------------------------------------------
# Local seed helpers — minimal copies, mirroring
# test_properties_measurements_attribution.py.
# ---------------------------------------------------------------------------


_seed_counter = [0]


async def _seed_material(db: AsyncSession) -> Material:
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter
    cat = MaterialCategory(
        name=f"materials-attribution-cat-{counter}",
        slug=f"materials-attribution-cat-{counter}",
    )
    db.add(cat)
    await db.flush()
    m = Material(
        name=f"MaterialsAttributionMaterial-{counter}",
        formula=f"Ma{counter}",
        category_id=cat.id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _seed_property_type(db: AsyncSession) -> PropertyType:
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter
    pc = PropertyCategory(
        name=f"MaterialsAttributionCategory-{counter}",
        slug=f"materials-attribution-category-{counter}",
    )
    db.add(pc)
    await db.flush()
    pt = PropertyType(
        category_id=pc.id,
        name=f"MaterialsAttributionProperty-{counter}",
        slug=f"materials-attribution-property-{counter}",
        value_type="scalar",
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return pt


async def _seed_source(db: AsyncSession) -> DataSource:
    src = DataSource(title="Non-canonical source", source_type="article")
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _seed_dataset(
    db: AsyncSession,
    *,
    material_id: uuid.UUID,
    source_id: uuid.UUID | None,
) -> Dataset:
    ds = Dataset(
        material_id=material_id,
        source_id=source_id,
        title="Materials attribution test dataset",
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def _seed_measurement(
    db: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    property_type_id: uuid.UUID,
    created_at: datetime | None = None,
) -> PropertyMeasurement:
    pm = PropertyMeasurement(
        dataset_id=dataset_id,
        property_type_id=property_type_id,
        value_scalar=1.0,
        method="test",
    )
    if created_at is not None:
        pm.created_at = created_at
    db.add(pm)
    await db.commit()
    await db.refresh(pm)
    return pm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_material_properties_rows_carry_attribution(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Every row carries an ``attribution`` block — intact when sourced."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    src = await _seed_source(db_session)
    ds = await _seed_dataset(
        db_session, material_id=material.id, source_id=src.id
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
    )

    resp = await async_client.get(f"/api/v1/materials/{material.id}/properties")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    rows = body["data"]["data"]
    assert len(rows) == 1
    row = rows[0]
    assert "attribution" in row, (
        "§3.3 primary surface rows must carry the §5.2 attribution block"
    )
    assert row["attribution"]["status"] == "intact"
    assert row["attribution"].get("lostAt") is None


@pytest.mark.asyncio
async def test_material_properties_flags_lost_when_dataset_source_nulled(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """§5.1 trigger #1 — dataset.source_id NULL + created_at before cutoff."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    # Dataset whose source FK was NULL'd during migration 070's cascade.
    ds = await _seed_dataset(
        db_session, material_id=material.id, source_id=None
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
    )

    resp = await async_client.get(f"/api/v1/materials/{material.id}/properties")
    assert resp.status_code == 200, resp.text

    rows = resp.json()["data"]["data"]
    assert len(rows) == 1
    attribution = rows[0]["attribution"]
    assert attribution["status"] == "lost"
    assert attribution["lostAt"] == "2026-09-02"
    assert attribution["siblingPlaceholderCount"] >= 1
