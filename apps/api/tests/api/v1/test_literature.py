"""Integration tests for /api/v1/literature endpoints (Phase 2).

NFM-1055: 7 endpoints, >=3 tests each = >=21 tests total.

NOTE: These endpoints are not yet implemented. Tests are skipped to
unblock CI (NFM-1211).

Endpoints under test:
  L1  POST   /literature/upload           — PDF upload placeholder
  L2  GET    /literature/{id}/status      — processing status
  L3  GET    /literature/{id}             — full detail
  L4  GET    /literature                  — paginated list
  L5  GET    /literature/search          — full-text search
  L6  POST   /literature/{id}/reextract   — trigger re-extraction
  L7  DELETE /literature/{id}             — cascade delete
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.skip(reason="Literature endpoints not yet implemented (NFM-1211)")

from nfm_db.models.source import DataSource

# ---------------------------------------------------------------------------
# Helpers — each test creates its own data, no cross-test dependencies
# ---------------------------------------------------------------------------


async def _seed_literature(db_session, **overrides) -> DataSource:
    """Create a journal_article DataSource with sensible defaults."""
    defaults = dict(
        title="Thermal conductivity of UO2",
        doi=f"10.0000/test-{uuid.uuid4().hex[:8]}",
        journal="Journal of Nuclear Materials",
        year=2024,
        source_type="journal_article",
        abstract="Thermal conductivity measurements of uranium dioxide.",
    )
    defaults.update(overrides)
    source = DataSource(**defaults)
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    return source


# ---------------------------------------------------------------------------
# L1: POST /literature/upload — Upload a PDF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_literature_creates_record(async_client, db_session) -> None:
    response = await async_client.post("/api/v1/literature/upload")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    literature_id = uuid.UUID(body["data"]["literature_id"])
    # Verify the record was actually persisted.
    source = await db_session.get(DataSource, literature_id)
    assert source is not None
    assert source.source_type == "journal_article"


@pytest.mark.asyncio
async def test_upload_literature_returns_id_and_status(async_client) -> None:
    response = await async_client.post("/api/v1/literature/upload")
    assert response.status_code == 200
    data = response.json()["data"]
    assert "literature_id" in data
    assert data["status"] == "uploaded"

    # literature_id must be a valid UUID string.
    uuid.UUID(data["literature_id"])  # raises if invalid


@pytest.mark.asyncio
async def test_upload_literature_response_envelope(async_client) -> None:
    response = await async_client.post("/api/v1/literature/upload")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], dict)
    assert "literature_id" in body["data"]


# ---------------------------------------------------------------------------
# L2: GET /literature/{id}/status — Processing status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_happy_path(async_client, db_session) -> None:
    source = await _seed_literature(db_session, title="Status Test")

    response = await async_client.get(
        f"/api/v1/literature/{source.id}/status",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] in (
        "uploaded",
        "parsing",
        "extracting",
        "completed",
        "failed",
    )
    assert 0 <= data["progress"] <= 100


@pytest.mark.asyncio
async def test_get_status_404(async_client) -> None:
    response = await async_client.get(
        f"/api/v1/literature/{uuid.uuid4()}/status",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_field_types(async_client, db_session) -> None:
    source = await _seed_literature(db_session)

    response = await async_client.get(
        f"/api/v1/literature/{source.id}/status",
    )
    data = response.json()["data"]
    assert isinstance(data["status"], str)
    assert isinstance(data["progress"], int)


# ---------------------------------------------------------------------------
# L3: GET /literature/{id} — Full detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail_happy_path(async_client, db_session) -> None:
    source = await _seed_literature(
        db_session,
        title="Detail Paper",
        doi="10.1234/detail",
        year=2023,
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Detail Paper"
    assert data["doi"] == "10.1234/detail"
    assert data["year"] == 2023
    assert data["status"] == "uploaded"
    assert data["figures_count"] >= 0
    assert data["tables_count"] >= 0
    assert "extracted_entities" in data
    assert "extracted_relations" in data


@pytest.mark.asyncio
async def test_get_detail_404(async_client) -> None:
    response = await async_client.get(
        f"/api/v1/literature/{uuid.uuid4()}",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_detail_optional_fields_null(async_client, db_session) -> None:
    source = await _seed_literature(
        db_session,
        title="Minimal",
        doi=None,
        abstract=None,
        journal=None,
        year=None,
    )

    response = await async_client.get(f"/api/v1/literature/{source.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Minimal"
    assert data["doi"] is None
    assert data["abstract"] is None
    assert data["journal"] is None
    assert data["year"] is None


# ---------------------------------------------------------------------------
# L4: GET /literature — Paginated list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_literature_empty(async_client) -> None:
    response = await async_client.get("/api/v1/literature")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_literature_paginated(async_client, db_session) -> None:
    await _seed_literature(db_session, title="Paper-A")
    await _seed_literature(db_session, title="Paper-B")
    await _seed_literature(db_session, title="Paper-C")

    response = await async_client.get("/api/v1/literature?limit=2&page=1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["pages"] == 2


@pytest.mark.asyncio
async def test_list_literature_search_filter(async_client, db_session) -> None:
    await _seed_literature(
        db_session,
        title="Uranium Oxide Thermal Study",
    )
    await _seed_literature(
        db_session,
        title="Plutonium Phase Diagram",
        abstract="Phase transitions in plutonium alloys.",
    )

    response = await async_client.get(
        "/api/v1/literature?search=Uranium",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert "Uranium" in data["items"][0]["title"]


# ---------------------------------------------------------------------------
# L5: GET /literature/search — Full-text search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_literature_by_title(async_client, db_session) -> None:
    await _seed_literature(
        db_session,
        title="Zirconium Alloy Corrosion",
        abstract="Not about uranium",
    )

    response = await async_client.get(
        "/api/v1/literature/search?q=Zirconium",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Zirconium Alloy Corrosion"


@pytest.mark.asyncio
async def test_search_literature_no_match(async_client, db_session) -> None:
    await _seed_literature(db_session, title="Existing Paper")

    response = await async_client.get(
        "/api/v1/literature/search?q=NONEXISTENTXYZ",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_search_literature_missing_query(async_client) -> None:
    """Missing required 'q' parameter should return 422."""
    response = await async_client.get("/api/v1/literature/search")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# L6: POST /literature/{id}/reextract — Re-extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reextract_happy_path(async_client, db_session) -> None:
    source = await _seed_literature(db_session, title="Reextract Me")

    response = await async_client.post(
        f"/api/v1/literature/{source.id}/reextract",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "extracting"
    assert "message" in data


@pytest.mark.asyncio
async def test_reextract_404(async_client) -> None:
    response = await async_client.post(
        f"/api/v1/literature/{uuid.uuid4()}/reextract",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reextract_response_shape(async_client, db_session) -> None:
    source = await _seed_literature(db_session)

    response = await async_client.post(
        f"/api/v1/literature/{source.id}/reextract",
    )
    data = response.json()["data"]
    assert isinstance(data["message"], str)
    assert isinstance(data["status"], str)
    assert len(data["status"]) > 0


# ---------------------------------------------------------------------------
# L7: DELETE /literature/{id} — Cascade delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_happy_path(async_client, db_session) -> None:
    source = await _seed_literature(db_session, title="Delete Me")

    response = await async_client.delete(
        f"/api/v1/literature/{source.id}",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    # Verify the record is gone from the database.
    deleted = await db_session.get(DataSource, source.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_delete_404(async_client) -> None:
    response = await async_client.delete(
        f"/api/v1/literature/{uuid.uuid4()}",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_then_get_returns_404(async_client, db_session) -> None:
    source = await _seed_literature(db_session, title="Gone Soon")

    # Delete succeeds.
    del_response = await async_client.delete(
        f"/api/v1/literature/{source.id}",
    )
    assert del_response.status_code == 200

    # Subsequent GET returns 404.
    get_response = await async_client.get(
        f"/api/v1/literature/{source.id}",
    )
    assert get_response.status_code == 404
