"""Tests for the materials REST API router (NFM-696).

Covers: GET /materials, GET /materials/{id}, POST /materials,
PATCH /materials/{id}, GET /materials/search.
Uses the async_client fixture from conftest.py (FastAPI test client + SQLite).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Material, MaterialAlias, MaterialCategory, MaterialComposition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_router_counter = 0


async def _seed_category(
    db: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
    **overrides,
) -> MaterialCategory:
    global _router_counter
    _router_counter += 1
    defaults = dict(
        name=name or f"category{_router_counter}",
        slug=slug or f"category{_router_counter}",
    )
    defaults.update(overrides)
    cat = MaterialCategory(**defaults)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def _seed_material(
    db: AsyncSession,
    *,
    name: str | None = None,
    formula: str | None = None,
    category: MaterialCategory | None = None,
    **overrides,
) -> Material:
    global _router_counter
    _router_counter += 1
    cat = category or await _seed_category(db)
    defaults = dict(
        name=name or f"Material{_router_counter}",
        formula=formula or f"Formula{_router_counter}",
        category_id=cat.id,
    )
    defaults.update(overrides)
    material = Material(**defaults)
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


async def _seed_alias(
    db: AsyncSession,
    *,
    material_id: uuid.UUID,
    alias_name: str | None = None,
    alias_type: str = "common_name",
    **overrides,
) -> MaterialAlias:
    defaults = dict(
        material_id=material_id,
        alias_name=alias_name or f"alias_{material_id.hex[:8]}",
        alias_type=alias_type,
    )
    defaults.update(overrides)
    a = MaterialAlias(**defaults)
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


async def _seed_composition(
    db: AsyncSession,
    *,
    material_id: uuid.UUID,
    element="U",
    fraction=0.88,
    **overrides,
) -> MaterialComposition:
    defaults = dict(
        material_id=material_id,
        element=element,
        fraction=fraction,
    )
    defaults.update(overrides)
    comp = MaterialComposition(**defaults)
    db.add(comp)
    await db.commit()
    await db.refresh(comp)
    return comp


# ============================================================
# GET /materials
# ============================================================


class TestListMaterialsEndpoint:
    """Tests for GET /materials."""

    @pytest.mark.asyncio
    async def test_list_returns_success_envelope(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _seed_material(db_session, name="UO2")

        resp = await async_client.get("/api/v1/materials")

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
        for i in range(5):
            await _seed_material(db_session, name=f"Material{i}")

        resp = await async_client.get("/api/v1/materials?page=1&per_page=2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 5
        assert body["data"]["pages"] == 3
        assert len(body["data"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_list_filters_by_category_id(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        cat_oxide = await _seed_category(db_session, name="oxide", slug="oxide")
        cat_metal = await _seed_category(db_session, name="metal", slug="metal")

        await _seed_material(db_session, name="UO2", category=cat_oxide)
        await _seed_material(db_session, name="U", category=cat_metal)

        resp = await async_client.get(f"/api/v1/materials?category_id={cat_oxide.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["name"] == "UO2"

    @pytest.mark.asyncio
    async def test_list_sort_and_order(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _seed_material(db_session, name="ZrO2")
        await _seed_material(db_session, name="UO2")

        resp = await async_client.get("/api/v1/materials?sort=name&order=asc")

        assert resp.status_code == 200
        body = resp.json()
        names = [item["name"] for item in body["data"]["items"]]
        assert names == ["UO2", "ZrO2"]

    @pytest.mark.asyncio
    async def test_list_empty_database(
        self,
        async_client: AsyncClient,
    ) -> None:
        resp = await async_client.get("/api/v1/materials")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []


# ============================================================
# GET /materials/{id}
# ============================================================


class TestGetMaterialEndpoint:
    """Tests for GET /materials/{id}."""

    @pytest.mark.asyncio
    async def test_get_returns_material_with_aliases_and_composition(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session, name="UO2")
        await _seed_alias(db_session, material_id=mat.id, alias_name="uranium dioxide")
        await _seed_composition(db_session, material_id=mat.id, element="U", fraction=0.88)
        await _seed_composition(db_session, material_id=mat.id, element="O", fraction=0.12)

        resp = await async_client.get(f"/api/v1/materials/{mat.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == str(mat.id)
        assert body["data"]["name"] == "UO2"
        assert "aliases" in body["data"]
        assert "composition" in body["data"]
        assert len(body["data"]["aliases"]) == 1
        assert len(body["data"]["composition"]) == 2

    @pytest.mark.asyncio
    async def test_get_returns_404_for_missing(
        self,
        async_client: AsyncClient,
    ) -> None:
        fake_id = uuid.uuid4()
        resp = await async_client.get(f"/api/v1/materials/{fake_id}")

        assert resp.status_code == 404


# ============================================================
# POST /materials
# ============================================================


class TestCreateMaterialEndpoint:
    """Tests for POST /materials."""

    @pytest.mark.asyncio
    async def test_create_returns_201_with_data(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        cat = await _seed_category(db_session)
        payload = {
            "name": "PuO2",
            "formula": "PuO2",
            "category_id": str(cat.id),
        }

        resp = await async_client.post("/api/v1/materials", json=payload)

        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "PuO2"
        assert body["data"]["formula"] == "PuO2"

    @pytest.mark.asyncio
    async def test_create_validates_required_fields(
        self,
        async_client: AsyncClient,
    ) -> None:
        # Missing required 'name' field
        payload = {"category_id": str(uuid.uuid4())}

        resp = await async_client.post("/api/v1/materials", json=payload)

        # Should get 422 validation error
        assert resp.status_code == 422


# ============================================================
# PATCH /materials/{id}
# ============================================================


class TestUpdateMaterialEndpoint:
    """Tests for PATCH /materials/{id}."""

    @pytest.mark.asyncio
    async def test_patch_modifies_existing_material(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session, name="UO2")

        payload = {"name": "Uranium Dioxide"}

        resp = await async_client.patch(f"/api/v1/materials/{mat.id}", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "Uranium Dioxide"

    @pytest.mark.asyncio
    async def test_patch_returns_404_for_missing(
        self,
        async_client: AsyncClient,
    ) -> None:
        fake_id = uuid.uuid4()
        payload = {"name": "Test"}

        resp = await async_client.patch(f"/api/v1/materials/{fake_id}", json=payload)

        assert resp.status_code == 404


# ============================================================
# GET /materials/search
# ============================================================


class TestSearchMaterialsEndpoint:
    """Tests for GET /materials/search."""

    @pytest.mark.asyncio
    async def test_search_returns_matching_materials(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        mat = await _seed_material(db_session, name="UO2")
        await _seed_alias(db_session, material_id=mat.id, alias_name="uranium dioxide")

        resp = await async_client.get("/api/v1/materials/search?q=uranium")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_paginates(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        for i in range(5):
            await _seed_material(db_session, name=f"Material{i}")

        resp = await async_client.get("/api/v1/materials/search?q=Material&page=1&per_page=2")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 5
        assert body["data"]["pages"] == 3
        assert len(body["data"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_search_empty_query(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _seed_material(db_session, name="UO2")

        resp = await async_client.get("/api/v1/materials/search?q=")

        # Empty query should return all materials
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] >= 1
