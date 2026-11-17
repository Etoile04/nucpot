"""Integration tests for ``GET /api/v1/datasets/{id}``.

Per NFM-4134 §5.2 option (a) and NFM-4159 AC: a NEW endpoint that
returns a single dataset with an ``attribution.status`` field.

  * ``"placeholder"`` for the 10 recast-restored datasets (NFM-4136).
    Detected server-side by matching against a list of restored IDs
    read from the same attribution-flag module as the measurement view.
  * ``"intact"`` otherwise.

The placeholder title itself is the disclosure (§4.2 / CEO directive),
so this endpoint exists primarily so the frontend can assert the
negative in its tests (§7c backstop).
"""

from __future__ import annotations

import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MaterialCategory,
)
from nfm_db.services import attribution_flag

# ---------------------------------------------------------------------------
# Fixtures — local minimal copy (no conftest coupling).
# ---------------------------------------------------------------------------


_seed_counter = [0]


async def _seed_material(db: AsyncSession) -> Material:
    counter = _seed_counter[0]
    counter += 1
    _seed_counter[0] = counter
    cat = MaterialCategory(
        name=f"ds-test-cat-{counter}",
        slug=f"ds-test-cat-{counter}",
    )
    db.add(cat)
    await db.flush()
    m = Material(name=f"DsTestMaterial-{counter}", formula=f"Dt{counter}", category_id=cat.id)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


@pytest.fixture(autouse=True)
def _reset_attribution_flag_cache() -> None:
    from nfm_db.services import attribution_flag as _af

    _af.reset_attribution_flag_cache()
    old_ids = os.environ.pop(_af.ATTRIBUTION_LOST_CANONICAL_ENV, None)
    old_recast = os.environ.pop(_af.RECAST_RESTORED_DATASET_IDS_ENV, None)
    try:
        yield
    finally:
        _af.reset_attribution_flag_cache()
        if old_ids is not None:
            os.environ[_af.ATTRIBUTION_LOST_CANONICAL_ENV] = old_ids
        if old_recast is not None:
            os.environ[_af.RECAST_RESTORED_DATASET_IDS_ENV] = old_recast


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dataset_returns_attribution_intact_by_default(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Default behaviour (no recast IDs configured) returns ``status: 'intact'``.

    This is the safe default per CEO §4.2 directive: the placeholder title
    on the recast datasets is the disclosure, not an annotation field.
    """
    material = await _seed_material(db_session)
    src = DataSource(title="Intact source", source_type="article")
    db_session.add(src)
    await db_session.flush()

    ds = Dataset(material_id=material.id, source_id=src.id, title="Intact dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(f"/api/v1/datasets/{ds.id}")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    dataset = body["data"]
    # Standard dataset fields present
    assert dataset["id"] == str(ds.id)
    assert dataset["material_id"] == str(material.id)
    assert dataset["source_id"] == str(src.id)
    # Attribution block pinned per §5.2
    assert "attribution" in dataset
    assert dataset["attribution"] == {"status": "intact"}


@pytest.mark.asyncio
async def test_get_dataset_flags_placeholder_when_recast_id_matches(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When recast-restored ID list is configured, matching dataset returns 'placeholder'."""
    material = await _seed_material(db_session)
    src = DataSource(title="Placeholder source", source_type="article")
    db_session.add(src)
    await db_session.flush()

    restored_id = uuid.uuid4()
    ds = Dataset(
        id=restored_id,
        material_id=material.id,
        source_id=src.id,
        title="Unattributed source (no DOI)",
    )
    db_session.add(ds)
    await db_session.commit()

    # Configure the recast-restored ID list.
    attribution_flag.reset_attribution_flag_cache()
    os.environ[
        attribution_flag.RECAST_RESTORED_DATASET_IDS_ENV
    ] = str(restored_id)
    attribution_flag.reset_attribution_flag_cache()

    resp = await async_client.get(f"/api/v1/datasets/{restored_id}")
    assert resp.status_code == 200
    dataset = resp.json()["data"]
    assert dataset["attribution"] == {"status": "placeholder"}


@pytest.mark.asyncio
async def test_get_dataset_404_when_missing(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    resp = await async_client.get(f"/api/v1/datasets/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_dataset_400_on_invalid_uuid(
    async_client: AsyncClient,
) -> None:
    resp = await async_client.get("/api/v1/datasets/not-a-uuid")
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_get_dataset_response_envelope_contract(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Pinned envelope: ``ApiResponse[DatasetWithAttributionResponse]``."""
    material = await _seed_material(db_session)
    src = DataSource(title="Envelope source", source_type="article")
    db_session.add(src)
    await db_session.flush()
    ds = Dataset(material_id=material.id, source_id=src.id, title="Envelope dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(f"/api/v1/datasets/{ds.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"success", "data"}
    if "error" in body:
        assert body["error"] is None or body["error"] == ""


# ---------------------------------------------------------------------------
# NFM-5321 (D2) — ``expand=material,source`` on the detail endpoint.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dataset_expand_returns_material_name_and_source_title(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """``?expand=material,source`` resolves human-readable names (NFM-5321 D2)."""
    material = await _seed_material(db_session)
    src = DataSource(title="Tokar et al., 1973", source_type="article")
    db_session.add(src)
    await db_session.flush()
    ds = Dataset(material_id=material.id, source_id=src.id, title="Expand dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(
        f"/api/v1/datasets/{ds.id}?expand=material,source"
    )
    assert resp.status_code == 200, resp.text

    dataset = resp.json()["data"]
    assert dataset["material_name"] == material.name
    assert dataset["source_title"] == "Tokar et al., 1973"
    # The §5.2 attribution block is untouched by expand.
    assert dataset["attribution"] == {"status": "intact"}


@pytest.mark.asyncio
async def test_get_dataset_without_expand_keeps_names_null(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Default detail response keeps expand columns null (single-table query)."""
    material = await _seed_material(db_session)
    src = DataSource(title="Unexpanded source", source_type="article")
    db_session.add(src)
    await db_session.flush()
    ds = Dataset(material_id=material.id, source_id=src.id, title="Plain dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(f"/api/v1/datasets/{ds.id}")
    assert resp.status_code == 200

    dataset = resp.json()["data"]
    assert dataset["material_name"] is None
    assert dataset["source_title"] is None


@pytest.mark.asyncio
async def test_get_dataset_expand_partial_returns_only_requested_join(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """``?expand=material`` populates the name but leaves source_title null."""
    material = await _seed_material(db_session)
    src = DataSource(title="Partial source", source_type="article")
    db_session.add(src)
    await db_session.flush()
    ds = Dataset(material_id=material.id, source_id=src.id, title="Partial dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(f"/api/v1/datasets/{ds.id}?expand=material")
    assert resp.status_code == 200

    dataset = resp.json()["data"]
    assert dataset["material_name"] == material.name
    assert dataset["source_title"] is None


@pytest.mark.asyncio
async def test_get_dataset_expand_source_without_source_row(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """``expand=source`` on a dataset with no source yields a null title, not 500."""
    material = await _seed_material(db_session)
    ds = Dataset(material_id=material.id, source_id=None, title="Orphan dataset")
    db_session.add(ds)
    await db_session.commit()
    await db_session.refresh(ds)

    resp = await async_client.get(f"/api/v1/datasets/{ds.id}?expand=source")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["source_title"] is None
