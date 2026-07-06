"""Integration tests for /api/v1/materials endpoints."""

import uuid
from typing import Any

import pytest

from nfm_db.models import Material, MaterialAlias, MaterialCategory, MaterialComposition

_seed_counter = 0


async def _seed_category(db_session, **overrides):
    global _seed_counter
    _seed_counter += 1
    defaults: dict[str, Any] = dict(
        name=f"Category {_seed_counter}",
        slug=f"cat-{_seed_counter}",
        description="Test category",
        sort_order=0,
    )
    defaults.update(overrides)
    cat = MaterialCategory(**defaults)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


async def _seed_material(db_session, **overrides):
    defaults: dict[str, Any] = dict(
        name="UO2",
        formula="UO2",
        crystal_structure="Fluorite",
        description="Uranium dioxide fuel",
        is_active=True,
    )
    if "category_id" not in overrides:
        cat = await _seed_category(db_session)
        defaults["category_id"] = cat.id
    defaults.update(overrides)
    mat = Material(**defaults)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    return mat


async def _seed_alias(db_session, material_id, **overrides):
    defaults: dict[str, Any] = dict(
        material_id=material_id,
        alias_name="Uranium Dioxide",
        alias_type="common_name",
        source="IAEA",
    )
    defaults.update(overrides)
    alias = MaterialAlias(**defaults)
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)
    return alias


# ── GET /materials (list) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_paginated(async_client, db_session) -> None:
    await _seed_material(db_session)
    response = await async_client.get("/api/v1/materials")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    payload = data["data"]
    assert "items" in payload
    assert payload["page"] == 1
    assert payload["limit"] == 20


@pytest.mark.asyncio
async def test_list_pagination_params(async_client, db_session) -> None:
    for i in range(5):
        await _seed_material(db_session, name=f"Mat-{i}")
    response = await async_client.get("/api/v1/materials?per_page=2&page=2")
    assert response.status_code == 200
    data = response.json()
    payload = data["data"]
    assert payload["total"] == 5
    assert len(payload["items"]) == 2
    assert payload["pages"] == 3


@pytest.mark.asyncio
async def test_list_filter_by_category(async_client, db_session) -> None:
    cat1 = await _seed_category(db_session, name="Cat1", slug="cat1")
    cat2 = await _seed_category(db_session, name="Cat2", slug="cat2")
    await _seed_material(db_session, name="M1", category_id=cat1.id)
    await _seed_material(db_session, name="M2", category_id=cat2.id)

    response = await async_client.get(f"/api/v1/materials?category_id={cat1.id}")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "M1"


@pytest.mark.asyncio
async def test_list_sort_order(async_client, db_session) -> None:
    await _seed_material(db_session, name="Zirconium")
    await _seed_material(db_session, name="Aluminum")
    response = await async_client.get("/api/v1/materials?sort=name&order=asc")
    payload = response.json()["data"]
    assert payload["items"][0]["name"] == "Aluminum"


# ── GET /materials/{id} (detail) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detail_with_aliases_and_composition(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="UO2")
    await _seed_alias(db_session, mat.id, alias_name="Urania")

    comp = MaterialComposition(
        material_id=mat.id, element="U", fraction=0.88
    )
    db_session.add(comp)
    await db_session.commit()

    response = await async_client.get(f"/api/v1/materials/{mat.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    detail = data["data"]
    assert detail["name"] == "UO2"
    assert len(detail["aliases"]) >= 1
    assert any(a["alias_name"] == "Urania" for a in detail["aliases"])


@pytest.mark.asyncio
async def test_detail_404_for_missing(async_client) -> None:
    response = await async_client.get(f"/api/v1/materials/{uuid.uuid4()}")
    assert response.status_code == 404


# ── POST /materials (create) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_material(async_client, db_session) -> None:
    payload = {
        "name": "UN",
        "formula": "UN",
        "crystal_structure": "NaCl",
        "description": "Uranium nitride",
    }
    response = await async_client.post("/api/v1/materials", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["data"]["name"] == "UN"
    assert data["data"]["id"] is not None


@pytest.mark.asyncio
async def test_create_material_validation(async_client) -> None:
    response = await async_client.post("/api/v1/materials", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_material_with_category(async_client, db_session) -> None:
    cat = await _seed_category(db_session, name="Nitrides", slug="nitrides")
    payload = {"name": "UN", "formula": "UN", "category_id": str(cat.id)}
    response = await async_client.post("/api/v1/materials", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["category_id"] == str(cat.id)


# ── PATCH /materials/{id} (update) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_material(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="UO2")
    payload = {"description": "Updated description"}
    response = await async_client.patch(
        f"/api/v1/materials/{mat.id}", json=payload
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["description"] == "Updated description"
    assert data["data"]["name"] == "UO2"


@pytest.mark.asyncio
async def test_update_material_404(async_client) -> None:
    payload = {"name": "Ghost"}
    response = await async_client.patch(
        f"/api/v1/materials/{uuid.uuid4()}", json=payload
    )
    assert response.status_code == 404


# ── GET /materials/search ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_by_name(async_client, db_session) -> None:
    await _seed_material(db_session, name="Uranium Dioxide")
    await _seed_material(db_session, name="Plutonium Oxide")
    response = await async_client.get("/api/v1/materials/search?q=Uranium")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "Uranium Dioxide"


@pytest.mark.asyncio
async def test_search_by_formula(async_client, db_session) -> None:
    await _seed_material(db_session, name="M1", formula="ZrO2")
    await _seed_material(db_session, name="M2", formula="PuO2")
    response = await async_client.get("/api/v1/materials/search?q=ZrO2")
    payload = response.json()["data"]
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_search_by_alias(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="M1", formula="ABC")
    await _seed_alias(db_session, mat.id, alias_name="Urania")

    response = await async_client.get("/api/v1/materials/search?q=Urania")
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "M1"


@pytest.mark.asyncio
async def test_search_empty_returns_all(async_client, db_session) -> None:
    await _seed_material(db_session, name="M1")
    await _seed_material(db_session, name="M2")
    response = await async_client.get("/api/v1/materials/search?q=")
    payload = response.json()["data"]
    assert payload["total"] == 2


@pytest.mark.asyncio
async def test_search_pagination(async_client, db_session) -> None:
    for i in range(5):
        await _seed_material(db_session, name=f"Zr-{i}", formula=f"Zr-{i}")
    response = await async_client.get("/api/v1/materials/search?q=Zr&per_page=2")
    payload = response.json()["data"]
    assert payload["total"] == 5
    assert len(payload["items"]) == 2
    assert payload["pages"] == 3


# ── Response envelope consistency ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_envelope_shape(async_client, db_session) -> None:
    """All endpoints return { success, data, ... } envelope."""
    await _seed_material(db_session, name="EnvTest")

    # list
    r = await async_client.get("/api/v1/materials")
    assert "success" in r.json() and "data" in r.json()

    # detail
    mat = await _seed_material(db_session)
    r = await async_client.get(f"/api/v1/materials/{mat.id}")
    assert r.json()["success"] is True

    # search
    r = await async_client.get("/api/v1/materials/search?q=EnvTest")
    assert r.json()["success"] is True

    # create
    r = await async_client.post("/api/v1/materials", json={"name": "X"})
    assert r.status_code == 201
    assert r.json()["success"] is True

    # update
    r = await async_client.patch(f"/api/v1/materials/{mat.id}", json={"name": "X2"})
    assert r.json()["success"] is True
