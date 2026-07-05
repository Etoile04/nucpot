"""Tests for the sources REST API router (NFM-698).

Covers: GET /sources, GET /sources/{id}, POST /sources.
Uses the async_client fixture from conftest.py (FastAPI test client + SQLite).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Author, DataSource, DataSourceAuthor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_router_counter = 0


async def _seed_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    **overrides,
) -> DataSource:
    global _router_counter
    _router_counter += 1
    if doi is None:
        doi = f"10.1000/router-test-{_router_counter}"
    defaults = dict(
        doi=doi,
        title="Paper A",
        source_type="journal_article",
        year=2020,
        journal="J. Nucl. Mater.",
    )
    defaults.update(overrides)
    source = DataSource(**defaults)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def _seed_author(
    db: AsyncSession, full_name="Alice", last_name="Alice", **overrides
) -> Author:
    defaults = dict(full_name=full_name, last_name=last_name)
    defaults.update(overrides)
    author = Author(**defaults)
    db.add(author)
    await db.commit()
    await db.refresh(author)
    return author


async def _link_author(
    db: AsyncSession,
    source_id: uuid.UUID,
    author_id: uuid.UUID,
    author_order: int = 1,
) -> DataSourceAuthor:
    link = DataSourceAuthor(
        data_source_id=source_id,
        author_id=author_id,
        author_order=author_order,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


# ============================================================
# GET /sources
# ============================================================


class TestListSourcesEndpoint:
    """Tests for GET /sources."""

    @pytest.mark.asyncio
    async def test_list_returns_success_envelope(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        await _seed_source(db_session)

        resp = await async_client.get("/api/v1/sources")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "data" in body
        assert "items" in body["data"]
        assert "total" in body["data"]

    @pytest.mark.asyncio
    async def test_list_filters_by_year(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        await _seed_source(db_session, title="Old", year=2019)
        await _seed_source(db_session, title="New", year=2021)

        resp = await async_client.get("/api/v1/sources?year=2021")

        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["title"] == "New"

    @pytest.mark.asyncio
    async def test_list_filters_by_source_type(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        await _seed_source(db_session, title="Journal", source_type="journal_article")
        await _seed_source(db_session, title="Book", source_type="book")

        resp = await async_client.get("/api/v1/sources?source_type=book")

        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["source_type"] == "book"

    @pytest.mark.asyncio
    async def test_list_paginates(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        for i in range(5):
            await _seed_source(db_session, title=f"Paper {i}")

        resp = await async_client.get("/api/v1/sources?page=1&per_page=2")

        body = resp.json()
        assert body["data"]["total"] == 5
        assert body["data"]["pages"] == 3
        assert len(body["data"]["items"]) == 2

    @pytest.mark.asyncio
    async def test_list_sort_order(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        await _seed_source(db_session, title="Alpha")
        await _seed_source(db_session, title="Bravo")

        resp = await async_client.get(
            "/api/v1/sources?sort=title&order=desc"
        )

        body = resp.json()
        titles = [item["title"] for item in body["data"]["items"]]
        assert titles == ["Bravo", "Alpha"]

    @pytest.mark.asyncio
    async def test_list_empty(
        self, async_client: AsyncClient,
    ) -> None:
        resp = await async_client.get("/api/v1/sources")

        body = resp.json()
        assert body["success"] is True
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []


# ============================================================
# GET /sources/{id}
# ============================================================


class TestGetSourceEndpoint:
    """Tests for GET /sources/{id}."""

    @pytest.mark.asyncio
    async def test_get_returns_source(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        source = await _seed_source(db_session, title="Found Paper")

        resp = await async_client.get(f"/api/v1/sources/{source.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["title"] == "Found Paper"
        assert body["data"]["doi"] == source.doi

    @pytest.mark.asyncio
    async def test_get_includes_authors(
        self, async_client: AsyncClient, db_session: AsyncSession,
    ) -> None:
        source = await _seed_source(db_session, title="Authored")
        author = await _seed_author(db_session, full_name="Alice A", last_name="A")
        await _link_author(db_session, source.id, author.id, author_order=1)

        resp = await async_client.get(f"/api/v1/sources/{source.id}")

        body = resp.json()
        assert body["success"] is True
        assert "authors" in body["data"]
        assert len(body["data"]["authors"]) == 1
        assert body["data"]["authors"][0]["author_order"] == 1
        assert body["data"]["authors"][0]["author"]["last_name"] == "A"

    @pytest.mark.asyncio
    async def test_get_404_for_missing(
        self, async_client: AsyncClient,
    ) -> None:
        resp = await async_client.get(f"/api/v1/sources/{uuid.uuid4()}")

        assert resp.status_code == 404


# ============================================================
# POST /sources
# ============================================================


class TestCreateSourceEndpoint:
    """Tests for POST /sources."""

    @pytest.mark.asyncio
    async def test_create_returns_201(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "doi": "10.1016/j.jnucmat.2020.152300",
            "title": "New Paper",
            "source_type": "journal_article",
            "year": 2020,
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        assert resp.status_code == 201
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["title"] == "New Paper"
        assert body["data"]["doi"] == "10.1016/j.jnucmat.2020.152300"

    @pytest.mark.asyncio
    async def test_create_without_doi(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "title": "Internal Report",
            "source_type": "report",
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["doi"] is None

    @pytest.mark.asyncio
    async def test_create_validates_doi_format(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "doi": "not-a-doi",
            "title": "Bad DOI Paper",
            "source_type": "journal_article",
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_validates_source_type(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "title": "Bad Type Paper",
            "source_type": "invalid_type",
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_validates_title_required(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "source_type": "journal_article",
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_returns_response_envelope(
        self, async_client: AsyncClient,
    ) -> None:
        payload = {
            "title": "Envelope Test",
            "source_type": "thesis",
        }

        resp = await async_client.post("/api/v1/sources", json=payload)

        body = resp.json()
        assert "success" in body
        assert "data" in body
        assert body["success"] is True
