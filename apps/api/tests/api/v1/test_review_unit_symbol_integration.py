"""NFM-4560 — end-to-end contract test for the 单位 column.

WHY THIS FILE EXISTS (E2E QA, NFM-4560):

The NFM-4560 fix has two halves, and the shipped test suite only covered
one of them:

1. ``_row_to_review_item(..., unit_symbols={...})`` — the *mapper*. Covered
   by the SimpleNamespace duck-row tests in ``test_review.py`` (NFM-4560
   section). Those tests hand the mapper a dict that is already correctly
   keyed, so they can only prove the mapper reads a dict.

2. The batched ``select(Unit.id, Unit.symbol).where(Unit.id.in_(unit_ids))``
   lookup inside ``get_pending_reviews`` that *builds* that dict — and the
   assumption that its keys (``Unit.id``) compare equal to ``row.unit_id``.
   **No test executed this query.** The Playwright spec stubs the HTTP
   response with ``unit_symbol`` pre-baked, so it starts downstream of the
   query too.

The two existing layers therefore sit on either side of the only code that
can actually fail, and a silent key-type mismatch (or a wrong column in the
SELECT) would fall back to the legacy short-id prefix — reproducing exactly
the bug NFM-4560 was filed to fix, with a fully green suite.

These tests close that gap by seeding real ``Unit`` + ``PropertyMeasurement``
rows and asserting on the real HTTP JSON.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
    Unit,
)
from nfm_db.models import DataSource as DataSourceModel
from nfm_db.models import Material as MaterialModel

_seed_counter = [0]


async def _seed_measurement_with_unit(
    db: AsyncSession,
    *,
    symbol: str | None,
    value_scalar: float,
    property_name: str,
) -> PropertyMeasurement:
    """Seed the full FK chain and return a pending PropertyMeasurement.

    ``symbol=None`` seeds a measurement with **no** unit row at all, which
    is the legacy path the UI must fall back on.
    """
    _seed_counter[0] += 1
    n = _seed_counter[0]

    material = MaterialModel(name=f"UO2-{n}", formula="UO2")
    source = DataSourceModel(
        title=f"Source {n}", source_type="journal_article", year=2020
    )
    db.add_all([material, source])
    await db.commit()
    await db.refresh(material)
    await db.refresh(source)

    dataset = Dataset(
        material_id=material.id, source_id=source.id, title=f"Dataset {n}"
    )
    category = PropertyCategory(name=f"Category{n}", slug=f"category{n}")
    db.add_all([dataset, category])
    await db.commit()
    await db.refresh(dataset)
    await db.refresh(category)

    prop_type = PropertyType(
        category_id=category.id,
        name=property_name,
        slug=f"property{n}",
        value_type="scalar",
    )
    db.add(prop_type)
    await db.commit()
    await db.refresh(prop_type)

    unit_id: uuid.UUID | None = None
    if symbol is not None:
        unit = Unit(
            name=f"unit-name-{n}",
            symbol=symbol,
            dimension="thermal_conductivity",
        )
        db.add(unit)
        await db.commit()
        await db.refresh(unit)
        unit_id = unit.id

    measurement = PropertyMeasurement(
        dataset_id=dataset.id,
        property_type_id=prop_type.id,
        unit_id=unit_id,
        value_scalar=value_scalar,
        review_status="pending",
        method="experiment",
    )
    db.add(measurement)
    await db.commit()
    await db.refresh(measurement)
    return measurement


async def _fetch_measurement_items(
    async_client: AsyncClient,
) -> list[dict[str, Any]]:
    response = await async_client.get(
        "/api/v1/review/pending?item_type=measurement"
    )
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["data"]["items"]
    return items


@pytest.mark.asyncio
async def test_pending_reviews_resolves_unit_symbol_from_real_db(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The real Unit.id → Unit.symbol query populates item_data.unit_symbol.

    This is the assertion the duck-row tests structurally cannot make: it
    proves the batched SELECT runs AND that its keys match ``row.unit_id``.
    """
    await _seed_measurement_with_unit(
        db_session,
        symbol="W/(m·K)",
        value_scalar=0.34,
        property_name="thermal conductivity",
    )

    items = await _fetch_measurement_items(async_client)

    assert len(items) == 1
    item_data = items[0]["item_data"]
    assert item_data["unit_symbol"] == "W/(m·K)"
    # The round-2 属性 column contract rides the same code path and was
    # likewise only duck-tested — assert it here too.
    assert item_data["property_type_name"] == "thermal conductivity"


@pytest.mark.asyncio
async def test_pending_reviews_unit_symbol_is_null_without_a_unit(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A measurement with no unit_id yields unit_symbol=None, not a crash.

    This is the legacy row shape the UI's short-id fallback exists for.
    """
    await _seed_measurement_with_unit(
        db_session,
        symbol=None,
        value_scalar=1200.0,
        property_name="melting temperature",
    )

    items = await _fetch_measurement_items(async_client)

    assert len(items) == 1
    assert items[0]["item_data"]["unit_symbol"] is None


@pytest.mark.asyncio
async def test_pending_reviews_maps_each_row_to_its_own_symbol(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """With several units in play, no row borrows another row's symbol.

    A single-row test would pass even if the lookup returned an arbitrary
    entry; this pins the per-row correspondence that the queue's 单位
    column actually depends on.
    """
    expected = {
        "thermal conductivity": "W/(m·K)",
        "lattice parameter a": "Å",
        "cohesive energy": "eV",
        "theoretical density": "g/cm³",
    }
    for property_name, symbol in expected.items():
        await _seed_measurement_with_unit(
            db_session,
            symbol=symbol,
            value_scalar=1.0,
            property_name=property_name,
        )

    items = await _fetch_measurement_items(async_client)

    assert len(items) == len(expected)
    actual = {
        item["item_data"]["property_type_name"]: item["item_data"]["unit_symbol"]
        for item in items
    }
    assert actual == expected


@pytest.mark.asyncio
async def test_pending_reviews_never_leaks_a_uuid_prefix_as_a_unit(
    async_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression guard for the original NFM-4560 defect.

    The bug rendered ``unit_id.slice(0, 8)``. Assert the payload carries a
    real symbol and that it is not merely a prefix of the unit UUID.
    """
    measurement = await _seed_measurement_with_unit(
        db_session,
        symbol="Å",
        value_scalar=4.95,
        property_name="lattice parameter a",
    )

    items = await _fetch_measurement_items(async_client)
    item_data = items[0]["item_data"]

    assert item_data["unit_symbol"] == "Å"
    unit_id = str(measurement.unit_id)
    assert item_data["unit_symbol"] != unit_id[:8]
    assert not unit_id.startswith(item_data["unit_symbol"])
