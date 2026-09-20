"""Integration tests for ``GET /api/v1/datasets`` (NFM-4991).

The list endpoint is paginated and supports ``material_id`` / ``source_id``
/ ``is_verified`` filters with optional ``expand=material,source`` joins.
Anonymous access is permitted (no auth dependency).
"""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Dataset, DataSource, Material
from nfm_db.services import attribution_flag


# NFM-4159 §5.2 — attribution flag is env-driven and module-cached.
# Reset on every test so the recast-restored ID list from a previous
# test can't leak into this one (mirrors the same fixture in
# test_datasets_get_by_id.py).
@pytest.fixture(autouse=True)
def _reset_attribution_flag_cache() -> None:
    attribution_flag.reset_attribution_flag_cache()
    old_ids = os.environ.pop(attribution_flag.ATTRIBUTION_LOST_CANONICAL_ENV, None)
    old_recast = os.environ.pop(attribution_flag.RECAST_RESTORED_DATASET_IDS_ENV, None)
    try:
        yield
    finally:
        attribution_flag.reset_attribution_flag_cache()
        if old_ids is not None:
            os.environ[attribution_flag.ATTRIBUTION_LOST_CANONICAL_ENV] = old_ids
        if old_recast is not None:
            os.environ[attribution_flag.RECAST_RESTORED_DATASET_IDS_ENV] = old_recast


async def _seed_dataset(
    db: AsyncSession,
    *,
    material_id,
    source_id=None,
    title: str = "Test dataset",
    is_verified: bool = False,
) -> Dataset:
    ds = Dataset(
        material_id=material_id,
        source_id=source_id,
        title=title,
        is_verified=is_verified,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def _seed_material_and_source(db: AsyncSession) -> tuple[Material, DataSource]:
    material = Material(
        name="DSListMat",
        formula="Dsl1",
    )
    db.add(material)
    await db.flush()
    src = DataSource(title="DSListSrc", source_type="article")
    db.add(src)
    await db.flush()
    return material, src


@pytest.mark.asyncio
async def test_list_datasets_empty_returns_envelope(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Empty DB → envelope with empty items, total=0, pages=0."""
    resp = await async_client.get("/api/v1/datasets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    page = body["data"]
    assert page["items"] == []
    assert page["total"] == 0
    assert page["page"] == 1
    assert page["limit"] == 20
    assert page["pages"] == 0
    assert page["truncated"] is False


@pytest.mark.asyncio
async def test_list_datasets_returns_one_per_row(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seeded datasets surface in the list with lightweight projection.

    Defaults: ``material_name`` / ``source_title`` are null because no
    ``expand=`` was supplied; the bare ``material_id`` / ``source_id``
    UUIDs are still present so the page can render monospace chips and
    link them out.
    """
    material, src = await _seed_material_and_source(db_session)
    ds1 = await _seed_dataset(db_session, material_id=material.id, source_id=src.id, title="A")
    ds2 = await _seed_dataset(
        db_session, material_id=material.id, source_id=None, title="B (no source)"
    )

    resp = await async_client.get("/api/v1/datasets")
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    ids = {it["id"] for it in items}
    assert str(ds1.id) in ids
    assert str(ds2.id) in ids
    assert resp.json()["data"]["total"] >= 2

    sample = next(it for it in items if it["id"] == str(ds1.id))
    assert sample["material_id"] == str(material.id)
    assert sample["source_id"] == str(src.id)
    assert sample["title"] == "A"
    assert sample["material_name"] is None  # no expand
    assert sample["source_title"] is None


@pytest.mark.asyncio
async def test_list_datasets_expand_material_returns_name(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """``expand=material`` joins Material.name so the list shows it inline."""
    material, _src = await _seed_material_and_source(db_session)
    ds = await _seed_dataset(db_session, material_id=material.id, title="Has material")

    resp = await async_client.get("/api/v1/datasets?expand=material")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    sample = next(it for it in items if it["id"] == str(ds.id))
    assert sample["material_name"] == "DSListMat"
    assert sample["source_title"] is None  # only material expanded


@pytest.mark.asyncio
async def test_list_datasets_filter_is_verified(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """``is_verified=true`` filter restricts the result set."""
    material, _src = await _seed_material_and_source(db_session)
    await _seed_dataset(db_session, material_id=material.id, title="unverified")
    ds_v = await _seed_dataset(
        db_session,
        material_id=material.id,
        title="verified",
        is_verified=True,
    )

    resp = await async_client.get("/api/v1/datasets?is_verified=true")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(ds_v.id)
    assert items[0]["title"] == "verified"


@pytest.mark.asyncio
async def test_list_datasets_pagination_bounds(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Per_page above the cap is clamped silently with truncated=true echo."""
    material, _src = await _seed_material_and_source(db_session)
    for i in range(3):
        await _seed_dataset(db_session, material_id=material.id, title=f"row-{i}")

    # Asking for 9999 should be clamped to 100 with truncated=true.
    resp = await async_client.get("/api/v1/datasets?per_page=9999")
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["limit"] == 100
    assert page["truncated"] is True


@pytest.mark.asyncio
async def test_list_datasets_400_on_invalid_filter(
    async_client: AsyncClient,
) -> None:
    """Bad UUID in the material_id filter is rejected (FastAPI validation)."""
    resp = await async_client.get("/api/v1/datasets?material_id=not-a-uuid")
    assert resp.status_code in (400, 422)
