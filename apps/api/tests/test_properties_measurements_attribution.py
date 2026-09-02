"""Integration tests for ``GET /api/v1/properties/{property_id}/measurements``.

Contract pinned per NFM-4134 §5.2 (locked, comment 8739f8f7) and
NFM-4159 AC. Every measurement row in the response must carry an
``attribution`` block with:

  * ``status`` ∈ {``"lost"``, ``"intact"``}
  * ``lostAt`` populated iff ``status == "lost"``
  * ``siblingPlaceholderCount`` when lost

Plus the AC backstops:
  * §7c — measurements whose dataset's source is NOT in the 4-canonical
    cohort never return ``status: "lost"``.
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
# Local seed helpers — minimal copies, mirroring test_properties_router.py.
# ---------------------------------------------------------------------------


_seed_counter = [0]


async def _seed_material(
    db: AsyncSession,
    *,
    name: str | None = None,
    formula: str | None = None,
    category: MaterialCategory | None = None,
) -> Material:
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter
    cat = category
    if cat is None:
        cat = MaterialCategory(
            name=f"attribution-cat-{counter}",
            slug=f"attribution-cat-{counter}",
        )
        db.add(cat)
        await db.flush()
    m = Material(
        name=name or f"AttributionMaterial-{counter}",
        formula=formula or f"At{counter}",
        category_id=cat.id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _seed_property_type(
    db: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
) -> PropertyType:
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter
    pc = PropertyCategory(
        name=f"AttributionCategory-{counter}",
        slug=f"attribution-category-{counter}",
    )
    db.add(pc)
    await db.flush()
    pt = PropertyType(
        category_id=pc.id,
        name=name or f"AttributionProperty-{counter}",
        slug=slug or f"attribution-property-{counter}",
        value_type="scalar",
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return pt


async def _seed_source(
    db: AsyncSession,
    *,
    title: str = "Test Source",
    source_type: str = "article",
) -> DataSource:
    src = DataSource(title=title, source_type=source_type)
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _seed_dataset(
    db: AsyncSession,
    *,
    material_id: uuid.UUID,
    source_id: uuid.UUID | None,
    title: str = "Test Dataset",
) -> Dataset:
    ds = Dataset(
        material_id=material_id,
        source_id=source_id,
        title=title,
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
async def test_endpoint_returns_attribution_block_per_row(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Every row carries an ``attribution`` block — even when status is intact."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    src = await _seed_source(db_session, title="Non-canonical source")
    ds = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=src.id,
    )
    pm = await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
    )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    items = body["data"]["items"]
    assert len(items) == 1
    row = items[0]
    assert row["id"] == str(pm.id)
    # Attribution block pinned per §5.2
    assert "attribution" in row
    assert row["attribution"]["status"] == "intact"
    # lostAt must NOT be present on intact rows (None is fine; omitted is better)
    assert row["attribution"].get("lostAt") is None
    # siblingPlaceholderCount optional on intact rows (None is fine)
    assert row["attribution"].get("siblingPlaceholderCount") is None


@pytest.mark.asyncio
async def test_endpoint_flags_lost_when_dataset_source_nulled(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """§5.1 trigger #1 — dataset.source_id NULL after migration 070 cascade."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    # Dataset whose source FK was NULL'd during migration 070's cascade.
    ds = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=None,
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
    )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    assert resp.status_code == 200, resp.text

    items = resp.json()["data"]["items"]
    assert len(items) == 1
    attribution = items[0]["attribution"]
    assert attribution["status"] == "lost"
    assert attribution["lostAt"] == "2026-09-02"
    assert attribution["siblingPlaceholderCount"] == 1


@pytest.mark.asyncio
async def test_endpoint_does_not_flag_post_cutoff_measurements(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Measurements created ON/AFTER 2026-09-02 are never flagged lost."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    ds = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=None,
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        # Exactly at the cutoff → exclusion (strict less-than per §5.1)
        created_at=datetime(2026, 9, 2, 0, 0, 0, tzinfo=UTC),
    )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    items = resp.json()["data"]["items"]
    assert items[0]["attribution"]["status"] == "intact"


@pytest.mark.asyncio
async def test_endpoint_backstop_noncanonical_source_never_lost(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """§7c — measurement whose dataset's source is NOT canonical never returns lost."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    # Seed two non-canonical sources; even after migration 070, the
    # dataset still points to a real source — these must NOT be flagged.
    src_a = await _seed_source(db_session, title="Non-canonical A")
    src_b = await _seed_source(db_session, title="Non-canonical B")
    ds_a = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=src_a.id,
    )
    ds_b = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=src_b.id,
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds_a.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds_b.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    items = resp.json()["data"]["items"]
    statuses = {row["attribution"]["status"] for row in items}
    assert statuses == {"intact"}


@pytest.mark.asyncio
async def test_endpoint_sibling_placeholder_count_is_correct(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When a lost dataset has multiple lost measurements, sibling count reflects all of them."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    ds = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=None,
    )
    for _ in range(3):
        await _seed_measurement(
            db_session,
            dataset_id=ds.id,
            property_type_id=pt.id,
            created_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC),
        )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    items = resp.json()["data"]["items"]
    assert len(items) == 3
    for row in items:
        assert row["attribution"]["status"] == "lost"
        assert row["attribution"]["siblingPlaceholderCount"] == 3


@pytest.mark.asyncio
async def test_endpoint_response_envelope_unchanged(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The new endpoint MUST keep the existing ``ApiResponse[PaginatedResponse]`` envelope."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)
    pt = await _seed_property_type(db_session)

    src = await _seed_source(db_session, title="Stable envelope")
    ds = await _seed_dataset(
        db_session,
        material_id=material.id,
        source_id=src.id,
    )
    await _seed_measurement(
        db_session,
        dataset_id=ds.id,
        property_type_id=pt.id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    data = body["data"]
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "limit" in data


@pytest.mark.asyncio
async def test_endpoint_empty_material_returns_empty_envelope(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No measurements → empty paginated response, still 200 OK."""
    attribution_flag.reset_attribution_flag_cache()

    material = await _seed_material(db_session)

    resp = await async_client.get(
        f"/api/v1/properties/{material.id}/measurements"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_endpoint_404_when_material_missing(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    attribution_flag.reset_attribution_flag_cache()

    bogus = uuid.uuid4()
    resp = await async_client.get(f"/api/v1/properties/{bogus}/measurements")
    assert resp.status_code == 404
