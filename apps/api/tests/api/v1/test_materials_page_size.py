"""NFM-4308 ③ — page_size contract for GET /api/v1/materials.

Contract (documented on the endpoint + ``PaginationParams``):

* ``page_size`` is an accepted alias for ``per_page``; an explicit
  ``per_page`` wins over the alias.
* Values above the server cap (100) are clamped to 100 instead of
  rejected — the response echoes the effective page size in
  ``data.limit`` and sets ``data.truncated: true`` so callers never
  silently lose rows.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_materials_router import _seed_material

pytestmark = pytest.mark.asyncio


async def _seed_three(db: AsyncSession) -> None:
    """Seed three visible materials (the placeholder row is filtered out)."""
    await _seed_material(db, name="UO2")
    await _seed_material(db, name="ZrO2")
    await _seed_material(db, name="SiC")


class TestPageSizeAlias:
    """page_size=… must behave exactly like per_page=…."""

    async def test_page_size_alias_controls_page_length(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials?page_size=2")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["items"]) == 2
        assert data["limit"] == 2
        assert data["truncated"] is False

    async def test_page_size_alias_on_search_endpoint(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials/search?q=&page_size=1")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["items"]) == 1
        assert data["limit"] == 1
        assert data["truncated"] is False

    async def test_explicit_per_page_wins_over_page_size_alias(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials?page_size=3&per_page=1")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["items"]) == 1
        assert data["limit"] == 1


class TestOverLimitClamp:
    """page_size / per_page above the cap clamp to 100 with truncated=true."""

    async def test_page_size_above_cap_clamps_and_flags(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        # The reported BUG-36 shape: caller asks page_size=1000.
        resp = await async_client.get("/api/v1/materials?page_size=1000")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["limit"] == 100
        assert data["truncated"] is True

    async def test_per_page_above_cap_clamps_and_flags(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials?per_page=101")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["limit"] == 100
        assert data["truncated"] is True

    async def test_at_cap_is_not_truncated(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials?page_size=100")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["limit"] == 100
        assert data["truncated"] is False

    async def test_default_page_size_still_twenty(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_three(db_session)

        resp = await async_client.get("/api/v1/materials")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["limit"] == 20
        assert data["truncated"] is False
