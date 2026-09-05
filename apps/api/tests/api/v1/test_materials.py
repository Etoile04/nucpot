"""Integration tests for /api/v1/materials endpoints."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import Material, MaterialAlias, MaterialCategory

# ---------------------------------------------------------------------------
# Helpers — each test creates its own data, no cross-test dependencies
# ---------------------------------------------------------------------------


async def _seed_category(db_session, **overrides):
    defaults = dict(
        name="Oxide Fuels",
        slug="oxide-fuels",
        description="Uranium and plutonium oxide fuels",
    )
    defaults.update(overrides)
    cat = MaterialCategory(**defaults)
    db_session.add(cat)
    await db_session.commit()
    await db_session.refresh(cat)
    return cat


async def _seed_material(db_session, **overrides):
    defaults = dict(
        name="UO2",
        formula="UO2",
        crystal_structure="Fluorite",
        is_active=True,
    )
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
    )
    defaults.update(overrides)
    alias = MaterialAlias(**defaults)
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)
    return alias


# ---------------------------------------------------------------------------
# GET /api/v1/materials — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_materials_empty(async_client) -> None:
    response = await async_client.get("/api/v1/materials")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_materials_returns_paginated(async_client, db_session) -> None:
    await _seed_material(db_session, name="Mat-A")
    await _seed_material(db_session, name="Mat-B")
    await _seed_material(db_session, name="Mat-C")

    response = await async_client.get("/api/v1/materials?per_page=2&page=1")
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["pages"] == 2


@pytest.mark.asyncio
async def test_list_materials_hides_inactive_merged_fragments(async_client, db_session) -> None:
    """NFM-4312 — retired duplicates leave the public list, stay reachable.

    The backfill retires merged duplicate fragments with
    ``is_active=False``; the list must hide them while direct UUID
    access (detail + properties) keeps working for curation/audit.
    """
    active = await _seed_material(db_session, name="UO2")
    retired = await _seed_material(
        db_session, name="amorphous UO2 (undoped and Cr-doped)", is_active=False
    )

    response = await async_client.get("/api/v1/materials?per_page=50")

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    names = [item["name"] for item in items]
    assert "UO2" in names
    assert all(item["name"] != retired.name for item in items)

    detail = await async_client.get(f"/api/v1/materials/{retired.id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == str(retired.id)
    assert active  # keep linters honest about both seeds


@pytest.mark.asyncio
async def test_list_materials_category_filter(async_client, db_session) -> None:
    cat = await _seed_category(db_session)
    await _seed_material(db_session, name="In-Cat", category_id=cat.id)
    await _seed_material(db_session, name="No-Cat")

    response = await async_client.get(f"/api/v1/materials?category_id={cat.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["name"] == "In-Cat"


@pytest.mark.asyncio
async def test_list_materials_sort_by_name_asc(async_client, db_session) -> None:
    await _seed_material(db_session, name="Zirconium")
    await _seed_material(db_session, name="Uranium")

    response = await async_client.get("/api/v1/materials?sort=name&order=asc")
    assert response.status_code == 200
    data = response.json()["data"]
    names = [item["name"] for item in data["items"]]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_list_materials_pagination_edge_cases(async_client, db_session) -> None:
    await _seed_material(db_session, name="Only")

    # page beyond range → empty
    response = await async_client.get("/api/v1/materials?page=999")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["total"] == 1

    # per_page=100 (max)
    response = await async_client.get("/api/v1/materials?per_page=100")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/materials/{id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_material_detail(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="UO2-Fresh")
    await _seed_alias(db_session, mat.id, alias_name="Urania")
    await _seed_alias(db_session, mat.id, alias_type="iupac_name", alias_name="UO2")

    response = await async_client.get(f"/api/v1/materials/{mat.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "UO2-Fresh"
    assert len(data["aliases"]) == 2


@pytest.mark.asyncio
async def test_get_material_detail_with_composition(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="UO2")
    # Composition is not seeded via helper; verify aliases empty shape
    response = await async_client.get(f"/api/v1/materials/{mat.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "aliases" in data
    assert "composition" in data


@pytest.mark.asyncio
async def test_get_material_404(async_client) -> None:
    response = await async_client.get(f"/api/v1/materials/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/materials — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_material_valid(async_client) -> None:
    payload = {
        "name": "UN",
        "formula": "UN",
        "crystal_structure": "NaCl",
        "is_active": True,
    }
    response = await async_client.post("/api/v1/materials", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["name"] == "UN"
    assert data["formula"] == "UN"
    assert data["id"] is not None


@pytest.mark.asyncio
async def test_create_material_invalid(async_client) -> None:
    # Empty name violates min_length=1
    payload = {"name": "", "formula": "X"}
    response = await async_client.post("/api/v1/materials", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_material_minimal(async_client) -> None:
    payload = {"name": "JustAName"}
    response = await async_client.post("/api/v1/materials", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["name"] == "JustAName"


# ---------------------------------------------------------------------------
# PATCH /api/v1/materials/{id} — update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_material_partial(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="Before")

    payload = {"name": "After", "description": "Updated description"}
    response = await async_client.patch(f"/api/v1/materials/{mat.id}", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "After"
    assert data["description"] == "Updated description"


@pytest.mark.asyncio
async def test_update_material_404(async_client) -> None:
    payload = {"name": "Ghost"}
    response = await async_client.patch(f"/api/v1/materials/{uuid.uuid4()}", json=payload)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/materials/search — search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_materials_by_name(async_client, db_session) -> None:
    await _seed_material(db_session, name="Uranium Dioxide")

    response = await async_client.get("/api/v1/materials/search?q=Uranium")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Uranium Dioxide"


@pytest.mark.asyncio
async def test_search_materials_by_formula(async_client, db_session) -> None:
    await _seed_material(db_session, name="SomeMaterial", formula="UO2")

    response = await async_client.get("/api/v1/materials/search?q=UO2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_search_materials_by_alias(async_client, db_session) -> None:
    mat = await _seed_material(db_session, name="WeirdName")
    await _seed_alias(db_session, mat.id, alias_name="Common Name Here")

    response = await async_client.get("/api/v1/materials/search?q=Common+Name+Here")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_search_materials_no_match(async_client, db_session) -> None:
    await _seed_material(db_session, name="UO2")

    response = await async_client.get("/api/v1/materials/search?q=NONEXISTENT")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_search_materials_paginated(async_client, db_session) -> None:
    for i in range(5):
        await _seed_material(db_session, name=f"Searchable-{i}")

    response = await async_client.get("/api/v1/materials/search?q=Searchable&per_page=2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# NFM-4057 — placeholder canonical row hidden from list/search
#
# The `Unknown Material (canonical)` row is the Phase 4 strategy B target of
# the NFM-3918 / NFM-4052 Unknown cleanup. It carries 96 measurements + 13
# datasets so it cannot be deleted, but its bad-word name must not leak
# onto the public `/materials` list or `/materials/search?q=…` results.
#
# Direct GET by UUID and the `/materials/{id}/properties` data path MUST
# stay open — curation workflows depend on them.
# ---------------------------------------------------------------------------

# Matches the literal name applied by NFM-3918 / NFM-4052 cleanup. Treated
# as a single-source-of-truth constant by the service-layer filter.
PLACEHOLDER_CANONICAL_NAME = "Unknown Material (canonical)"
# Materialized in prod 2026-09-01 11:08:05 UTC by NFM-4052.
PLACEHOLDER_CANONICAL_ID = uuid.UUID("021036bf-d7cc-434c-8f91-a08030027b4a")


@pytest.mark.asyncio
async def test_list_materials_excludes_canonical_placeholder(
    async_client, db_session
) -> None:
    """NFM-4057 AC: list endpoint never returns the canonical row."""
    await _seed_material(db_session, name="Real-A")
    await _seed_material(db_session, name="Real-B")
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )

    response = await async_client.get("/api/v1/materials?page=1&per_page=20")
    assert response.status_code == 200
    data = response.json()["data"]
    names = [item["name"] for item in data["items"]]
    assert "Unknown Material (canonical)" not in names


@pytest.mark.asyncio
async def test_list_materials_pagination_never_shows_canonical(
    async_client, db_session
) -> None:
    """Filter must apply to every page (not just page 1)."""
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )
    for i in range(25):
        await _seed_material(db_session, name=f"Real-{i:02d}")

    seen_names: set[str] = set()
    for page in range(1, 4):  # per_page=10 → 3 pages, 25 + 1 = 26 rows
        response = await async_client.get(
            f"/api/v1/materials?page={page}&per_page=10"
        )
        assert response.status_code == 200
        data = response.json()["data"]
        for item in data["items"]:
            seen_names.add(item["name"])

    assert "Unknown Material (canonical)" not in seen_names


@pytest.mark.asyncio
async def test_search_materials_excludes_canonical_placeholder(
    async_client, db_session
) -> None:
    """NFM-4057 AC: search results never contain the canonical row."""
    await _seed_material(db_session, name="Uranium Dioxide")
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )

    response = await async_client.get("/api/v1/materials/search?q=unknown")
    assert response.status_code == 200
    data = response.json()["data"]
    names = [item["name"] for item in data["items"]]
    assert "Unknown Material (canonical)" not in names


@pytest.mark.asyncio
async def test_get_material_canonical_uuid_still_returns_full_record(
    async_client, db_session
) -> None:
    """NFM-4057 AC: direct GET by canonical UUID still returns 200.

    Curation paths (NFM-3918 / NFM-4052 follow-ups) resolve the canonical by
    UUID to reattribute the 96 measurements and 13 datasets. A 404 here
    would break the entire curation backlog.
    """
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )

    response = await async_client.get(
        f"/api/v1/materials/{PLACEHOLDER_CANONICAL_ID}"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "Unknown Material (canonical)"
    assert data["id"] == str(PLACEHOLDER_CANONICAL_ID)


@pytest.mark.asyncio
async def test_list_materials_total_reflects_filtered_count(
    async_client, db_session
) -> None:
    """NFM-4057 AC: ``total`` is the post-filter count."""
    await _seed_material(db_session, name="Real-1")
    await _seed_material(db_session, name="Real-2")
    await _seed_material(db_session, name="Real-3")
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )

    response = await async_client.get("/api/v1/materials")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3  # excludes canonical


@pytest.mark.asyncio
async def test_list_material_properties_canonical_path_unblocked(
    async_client, db_session
) -> None:
    """NFM-4057 AC integration: /materials/{canonical}/properties still returns 200.

    This is the data path that surfaces the 96 measurements + 13 datasets
    that the canonical carries. The filter is scoped to list/search only;
    the per-material property endpoint must remain unblocked.
    """
    await _seed_material(
        db_session,
        id=PLACEHOLDER_CANONICAL_ID,
        name=PLACEHOLDER_CANONICAL_NAME,
    )

    response = await async_client.get(
        f"/api/v1/materials/{PLACEHOLDER_CANONICAL_ID}/properties"
    )
    assert response.status_code == 200
    body = response.json()
    # The /materials/{id}/properties endpoint uses the standard ApiResponse
    # envelope on top of MaterialPropertyListResponse — distinct from the
    # PaginatedResponse shape. The critical assertion is that the endpoint
    # is reachable by the canonical UUID (200, well-formed envelope) — the
    # 96 measurements + 13 datasets would populate ``body["data"]["data"]``
    # if seeded.
    assert body["success"] is True
    inner = body["data"]
    assert isinstance(inner.get("data"), list)
    assert "meta" in inner
    assert inner["meta"]["total"] == 0  # no datasets seeded
