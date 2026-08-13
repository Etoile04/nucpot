"""Tests for the material service layer."""

import uuid
from typing import Any

import pytest

from nfm_db.models import Material, MaterialAlias, MaterialCategory, MaterialComposition
from nfm_db.services.material_service import (
    create_material,
    get_material,
    list_materials,
    search_materials,
    update_material,
)

_seed_counter = 0


async def _seed_category(db_session, **overrides):
    global _seed_counter
    _seed_counter += 1
    defaults = dict(
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
    defaults = dict(
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


async def _seed_composition(db_session, material_id, **overrides):
    defaults = dict(
        material_id=material_id,
        element="U",
        fraction=0.88,
    )
    defaults.update(overrides)
    comp = MaterialComposition(**defaults)
    db_session.add(comp)
    await db_session.commit()
    await db_session.refresh(comp)
    return comp


# ── list_materials ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_materials(db_session) -> None:
    await _seed_material(db_session, name="UO2")
    result = await list_materials(db_session, page=1, limit=20)
    assert result.total == 1
    assert len(result.items) == 1
    assert result.items[0].name == "UO2"
    assert result.page == 1


@pytest.mark.asyncio
async def test_list_pagination(db_session) -> None:
    for i in range(5):
        await _seed_material(db_session, name=f"Material_{i}")
    page1 = await list_materials(db_session, page=1, limit=2)
    assert page1.total == 5
    assert len(page1.items) == 2
    assert page1.pages == 3

    page3 = await list_materials(db_session, page=3, limit=2)
    assert page3.total == 5
    assert len(page3.items) == 1


@pytest.mark.asyncio
async def test_list_filters_by_category_id(db_session) -> None:
    cat1 = await _seed_category(db_session, name="Cat1", slug="cat1")
    cat2 = await _seed_category(db_session, name="Cat2", slug="cat2")
    await _seed_material(db_session, name="Mat1", category_id=cat1.id)
    await _seed_material(db_session, name="Mat2", category_id=cat2.id)

    result = await list_materials(db_session, page=1, limit=20, category_id=cat1.id)
    assert result.total == 1
    assert result.items[0].name == "Mat1"


@pytest.mark.asyncio
async def test_list_sort_by_name_asc(db_session) -> None:
    await _seed_material(db_session, name="Zirconium")
    await _seed_material(db_session, name="Aluminum")
    result = await list_materials(db_session, page=1, limit=20, sort="name", order="asc")
    assert result.items[0].name == "Aluminum"
    assert result.items[1].name == "Zirconium"


@pytest.mark.asyncio
async def test_list_sort_by_name_desc(db_session) -> None:
    await _seed_material(db_session, name="First")
    await _seed_material(db_session, name="Second")
    result = await list_materials(db_session, page=1, limit=20, sort="name", order="desc")
    assert result.items[0].name == "Second"
    assert result.items[1].name == "First"


# ── get_material ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_material_with_aliases_and_composition(db_session) -> None:
    mat = await _seed_material(db_session, name="UO2")
    await _seed_alias(db_session, mat.id, alias_name="Urania")
    await _seed_composition(db_session, mat.id, element="U", fraction=0.88)
    await _seed_composition(db_session, mat.id, element="O", fraction=0.12)

    result = await get_material(db_session, mat.id)
    assert result is not None
    assert result.name == "UO2"
    assert len(result.aliases) == 1
    assert result.aliases[0].alias_name == "Urania"
    assert len(result.composition) == 2


@pytest.mark.asyncio
async def test_get_material_returns_none_for_missing(db_session) -> None:
    assert await get_material(db_session, uuid.uuid4()) is None


# ── create_material ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_material(db_session) -> None:
    from nfm_db.schemas.material import MaterialCreate

    data = MaterialCreate(
        name="UN",
        formula="UN",
        crystal_structure="NaCl",
        description="Uranium nitride",
    )
    result = await create_material(db_session, data)
    assert result.name == "UN"
    assert result.formula == "UN"
    assert result.id is not None


@pytest.mark.asyncio
async def test_create_material_with_category(db_session) -> None:
    from nfm_db.schemas.material import MaterialCreate

    cat = await _seed_category(db_session, name="Nitrides", slug="nitrides")
    data = MaterialCreate(name="UN", formula="UN", category_id=cat.id)
    result = await create_material(db_session, data)
    assert result.category_id == cat.id


# ── update_material ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_material(db_session) -> None:
    from nfm_db.schemas.material import MaterialUpdate

    mat = await _seed_material(db_session, name="UO2")
    data = MaterialUpdate(description="Updated description")
    result = await update_material(db_session, mat.id, data)
    assert result.description == "Updated description"
    assert result.name == "UO2"


@pytest.mark.asyncio
async def test_update_material_not_found(db_session) -> None:
    from nfm_db.schemas.material import MaterialUpdate

    data = MaterialUpdate(name="Ghost")
    result = await update_material(db_session, uuid.uuid4(), data)
    assert result is None


# ── search_materials ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_by_name(db_session) -> None:
    await _seed_material(db_session, name="Uranium Dioxide")
    await _seed_material(db_session, name="Plutonium Oxide")
    result = await search_materials(db_session, query="Uranium", page=1, limit=20)
    assert result.total == 1
    assert result.items[0].name == "Uranium Dioxide"


@pytest.mark.asyncio
async def test_search_by_formula(db_session) -> None:
    await _seed_material(db_session, name="Mat1", formula="ZrO2")
    await _seed_material(db_session, name="Mat2", formula="PuO2")
    result = await search_materials(db_session, query="ZrO2", page=1, limit=20)
    assert result.total == 1
    assert result.items[0].formula == "ZrO2"


@pytest.mark.asyncio
async def test_search_by_alias(db_session) -> None:
    mat = await _seed_material(db_session, name="Mat1", formula="ABC")
    await _seed_alias(db_session, mat.id, alias_name="Urania")

    result = await search_materials(db_session, query="Urania", page=1, limit=20)
    assert result.total == 1
    assert result.items[0].name == "Mat1"


@pytest.mark.asyncio
async def test_search_empty_query_returns_all(db_session) -> None:
    await _seed_material(db_session, name="Mat1")
    await _seed_material(db_session, name="Mat2")
    result = await search_materials(db_session, query="", page=1, limit=20)
    assert result.total == 2


@pytest.mark.asyncio
async def test_search_pagination(db_session) -> None:
    for i in range(5):
        await _seed_material(db_session, name=f"UO2-{i}", formula=f"UO2-{i}")
    result = await search_materials(db_session, query="UO2", page=1, limit=2)
    assert result.total == 5
    assert len(result.items) == 2
    assert result.pages == 3
