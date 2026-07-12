"""Integration tests for /api/v1/sources endpoints."""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import Author, DataSource, DataSourceAuthor

_doi_counter = 0


def _unique_doi() -> str:
    global _doi_counter
    _doi_counter += 1
    return f"10.1000/test.{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_source(db_session, **overrides):
    defaults = dict(
        title="Test Paper Title",
        year=2024,
        source_type="journal_article",
        doi=_unique_doi() if "doi" not in overrides else overrides["doi"],
    )
    defaults.update(overrides)
    src = DataSource(**defaults)
    db_session.add(src)
    await db_session.commit()
    await db_session.refresh(src)
    return src


async def _seed_author(db_session, **overrides):
    defaults = dict(
        full_name="Smith, J.",
        last_name="Smith",
        first_name="J.",
        orcid=uuid.uuid4().hex[:16] if "orcid" not in overrides else overrides["orcid"],
        affiliation="Test University",
    )
    defaults.update(overrides)
    auth = Author(**defaults)
    db_session.add(auth)
    await db_session.commit()
    await db_session.refresh(auth)
    return auth


async def _seed_source_author(
    db_session, source_id, author_id, **overrides
):
    defaults = dict(
        data_source_id=source_id,
        author_id=author_id,
        author_order=1,
        is_corresponding=True,
    )
    defaults.update(overrides)
    link = DataSourceAuthor(**defaults)
    db_session.add(link)
    await db_session.commit()
    await db_session.refresh(link)
    return link


# ---------------------------------------------------------------------------
# GET /api/v1/sources — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sources_empty(async_client) -> None:
    response = await async_client.get("/api/v1/sources")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_sources_paginated(async_client, db_session) -> None:
    for i in range(5):
        await _seed_source(db_session, title=f"Paper-{i}")

    response = await async_client.get("/api/v1/sources?per_page=2&page=1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_list_sources_year_filter(async_client, db_session) -> None:
    await _seed_source(db_session, title="Old", year=2020)
    await _seed_source(db_session, title="New", year=2024)

    response = await async_client.get("/api/v1/sources?year=2024")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "New"


@pytest.mark.asyncio
async def test_list_sources_type_filter(async_client, db_session) -> None:
    await _seed_source(db_session, title="Journal", source_type="journal_article")
    await _seed_source(db_session, title="Book", source_type="book")

    response = await async_client.get(
        "/api/v1/sources?source_type=journal_article"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_sources_sort_by_year(async_client, db_session) -> None:
    await _seed_source(db_session, title="Recent", year=2024)
    await _seed_source(db_session, title="Old", year=2019)

    response = await async_client.get("/api/v1/sources?sort=year&order=asc")
    assert response.status_code == 200
    data = response.json()["data"]
    years = [item["year"] for item in data["items"]]
    assert years == sorted(years)


@pytest.mark.asyncio
async def test_list_sources_pagination_edge_cases(async_client, db_session) -> None:
    await _seed_source(db_session, title="Only")

    # page beyond range
    response = await async_client.get("/api/v1/sources?page=999")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []

    # per_page at max
    response = await async_client.get("/api/v1/sources?per_page=100")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/sources/{id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_source_detail(async_client, db_session) -> None:
    src = await _seed_source(db_session, title="Detailed Paper")
    auth1 = await _seed_author(
        db_session, full_name="First, A.", last_name="First", first_name="A."
    )
    auth2 = await _seed_author(
        db_session, full_name="Second, B.", last_name="Second", first_name="B."
    )
    await _seed_source_author(
        db_session, src.id, auth1.id, author_order=2
    )
    await _seed_source_author(
        db_session, src.id, auth2.id, author_order=1
    )

    response = await async_client.get(f"/api/v1/sources/{src.id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "Detailed Paper"
    assert len(data["authors"]) == 2
    # Authors should be ordered by author_order
    assert data["authors"][0]["author"]["full_name"] == "Second, B."
    assert data["authors"][1]["author"]["full_name"] == "First, A."


@pytest.mark.asyncio
async def test_get_source_404(async_client) -> None:
    response = await async_client.get(f"/api/v1/sources/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/sources — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_source_valid(async_client) -> None:
    payload = {
        "title": "New Paper",
        "year": 2026,
        "source_type": "journal_article",
        "doi": "10.1234/new.001",
    }
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["title"] == "New Paper"
    assert data["year"] == 2026
    assert data["doi"] == "10.1234/new.001"


@pytest.mark.asyncio
async def test_create_source_with_doi(async_client) -> None:
    payload = {
        "title": "DOI Paper",
        "year": 2025,
        "source_type": "journal_article",
        "doi": "10.1000/doi-test",
    }
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["doi"] == "10.1000/doi-test"


@pytest.mark.asyncio
async def test_create_source_invalid_type(async_client) -> None:
    """source_type is validated against a whitelist — unknown values are rejected."""
    payload = {
        "title": "Odd Source",
        "year": 2024,
        "source_type": "unknown_type",
    }
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_source_invalid_doi(async_client) -> None:
    """DOI must match 10.XXXX/XXXX format."""
    payload = {
        "title": "Bad DOI",
        "source_type": "journal_article",
        "doi": "not-a-doi",
    }
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_source_minimal(async_client) -> None:
    """Only title and source_type are required."""
    payload = {"title": "Minimal", "source_type": "other"}
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["source_type"] == "other"
