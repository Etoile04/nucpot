"""Integration tests for reference-gaps API endpoints.

Tests all 4 endpoints per NFM-64 acceptance criteria:
- GET /api/v1/reference-gaps (list)
- GET /api/v1/reference-gaps/summary (coverage statistics)
- POST /api/v1/reference-gaps/fill (trigger fill operation)
- POST /api/v1/reference-gaps/scan (manual gap scan)

Uses SQLite in-memory DB with dependency override to avoid requiring PostgreSQL.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.database import get_db
from nfm_db.main import app


def _create_test_engine():
    """Create a SQLite in-memory async engine."""
    return create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)


def _override_get_db(engine, session_factory):
    """Create a dependency override that yields a test session."""

    async def test_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return test_get_db


@pytest.fixture
async def client():
    """Create an async test client with SQLite in-memory DB."""
    engine = _create_test_engine()

    from nfm_db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    app.dependency_overrides[get_db] = _override_get_db(engine, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.mark.asyncio
async def test_list_gaps_returns_paginated_results(client: AsyncClient) -> None:
    """GET /api/v1/reference-gaps returns paginated gap list with correct shape."""
    response = await client.get("/api/v1/reference-gaps")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert "data" in data
    assert "gaps" in data["data"]
    assert "total" in data["data"]
    assert "page" in data["data"]
    assert "per_page" in data["data"]
    assert data["data"]["page"] == 1
    assert data["data"]["per_page"] == 20


@pytest.mark.asyncio
async def test_list_gaps_filters_by_element_system(client: AsyncClient) -> None:
    """GET /api/v1/reference-gaps?element_system=U filters to U only."""
    response = await client.get("/api/v1/reference-gaps?element_system=U")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    for gap in data["data"]["gaps"]:
        assert gap["element_system"] == "U"


@pytest.mark.asyncio
async def test_list_gaps_filters_by_property(client: AsyncClient) -> None:
    """GET /api/v1/reference-gaps?property=bulk_modulus filters by property."""
    response = await client.get("/api/v1/reference-gaps?property=bulk_modulus")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    for gap in data["data"]["gaps"]:
        assert gap["property_name"] == "bulk_modulus"


@pytest.mark.asyncio
async def test_list_gaps_has_priority_field(client: AsyncClient) -> None:
    """Gap items include a numeric priority field."""
    response = await client.get("/api/v1/reference-gaps")

    assert response.status_code == 200
    data = response.json()

    for gap in data["data"]["gaps"]:
        assert "priority" in gap
        assert isinstance(gap["priority"], int)


@pytest.mark.asyncio
async def test_summary_returns_coverage_stats(client: AsyncClient) -> None:
    """GET /api/v1/reference-gaps/summary returns coverage statistics."""
    response = await client.get("/api/v1/reference-gaps/summary")

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    summary = data["data"]
    assert "total_target_tuples" in summary
    assert "covered" in summary
    assert "gaps" in summary
    assert "coverage_percent" in summary
    assert "by_system" in summary
    assert "staging_pending" in summary
    assert "staging_approved" in summary


@pytest.mark.asyncio
async def test_summary_coverage_percent_is_float(client: AsyncClient) -> None:
    """Coverage percent is a numeric value between 0 and 100."""
    response = await client.get("/api/v1/reference-gaps/summary")

    assert response.status_code == 200
    summary = response.json()["data"]

    assert isinstance(summary["coverage_percent"], (int, float))
    assert 0 <= summary["coverage_percent"] <= 100


@pytest.mark.asyncio
async def test_summary_total_equals_covered_plus_gaps(client: AsyncClient) -> None:
    """Summary invariant: total = covered + gaps."""
    response = await client.get("/api/v1/reference-gaps/summary")

    summary = response.json()["data"]

    assert summary["total_target_tuples"] == summary["covered"] + summary["gaps"]


@pytest.mark.asyncio
async def test_fill_returns_batch_result(client: AsyncClient) -> None:
    """POST /api/v1/reference-gaps/fill returns fill result with correct shape."""
    response = await client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "cache_levels": ["L1"],
            "dry_run": True,
        },
    )

    assert response.status_code == 202
    data = response.json()

    assert data["success"] is True
    fill = data["data"]
    assert "batch_id" in fill
    assert "gaps_targeted" in fill
    assert "values_found" in fill
    assert "staged" in fill
    assert "duplicates" in fill
    assert "results" in fill
    assert fill["gaps_targeted"] == 1


@pytest.mark.asyncio
async def test_fill_dry_run_has_no_batch_id(client: AsyncClient) -> None:
    """Dry run fill returns batch_id as null."""
    response = await client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "bulk_modulus",
            "dry_run": True,
        },
    )

    fill = response.json()["data"]
    assert fill["batch_id"] is None
    assert fill["staged"] == 0


@pytest.mark.asyncio
async def test_scan_returns_gap_summary(client: AsyncClient) -> None:
    """POST /api/v1/reference-gaps/scan returns scan results."""
    response = await client.post(
        "/api/v1/reference-gaps/scan",
        json={},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    scan = data["data"]
    assert "total_gaps_found" in scan
    assert "systems_scanned" in scan
    assert "results" in scan
    assert isinstance(scan["results"], list)


@pytest.mark.asyncio
async def test_scan_filter_by_element_systems(client: AsyncClient) -> None:
    """POST /api/v1/reference-gaps/scan with element_systems filter."""
    response = await client.post(
        "/api/v1/reference-gaps/scan",
        json={"element_systems": ["U"]},
    )

    assert response.status_code == 200
    scan = response.json()["data"]

    for item in scan["results"]:
        assert item["element_system"] == "U"


@pytest.mark.asyncio
async def test_fill_invalid_property_returns_empty(client: AsyncClient) -> None:
    """Fill for an unknown property returns no values found."""
    response = await client.post(
        "/api/v1/reference-gaps/fill",
        json={
            "element_system": "U",
            "phase": "BCC",
            "property_name": "nonexistent_property_xyz",
            "dry_run": True,
        },
    )

    assert response.status_code == 202
    fill = response.json()["data"]
    assert fill["values_found"] == 0
    assert fill["results"] == []
