"""Integration tests for /api/v1/stats endpoint.

Regression coverage for NFM-4341 on the FastAPI side: the response payload
inside the ``ApiResponse`` envelope must contain ``elements: list[str]``,
sorted, deduplicated across all potentials, present (empty list) when the
DB has no potentials, and additive alongside the pre-existing top-level
fields. The Next BFF at ``/api/stats`` and the ``/browse``/``/search``
consumers are exercised by separate tests.
"""

from __future__ import annotations

import pytest

from nfm_db.models.potential import Potential

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_potential(db_session, *, name: str, type_: str, elements: list[str]) -> Potential:
    potential = Potential(name=name, type=type_, elements=elements)
    db_session.add(potential)
    await db_session.commit()
    await db_session.refresh(potential)
    return potential


# ---------------------------------------------------------------------------
# GET /api/v1/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_includes_elements_field_even_when_db_empty(async_client) -> None:
    """The contract: ``elements`` is always present, even when no potentials exist."""
    response = await async_client.get("/api/v1/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "elements" in data, (
        "stats response missing 'elements' — /browse chips will be empty (NFM-4341)"
    )
    assert isinstance(data["elements"], list)
    assert data["elements"] == []


@pytest.mark.asyncio
async def test_stats_elements_returns_sorted_distinct_union(async_client, db_session) -> None:
    """Elements across all potentials should be flattened, deduplicated, and sorted."""
    await _seed_potential(
        db_session,
        name="eam-u-mo",
        type_="EAM",
        elements=["U", "Mo"],
    )
    await _seed_potential(
        db_session,
        name="meam-fe-cr",
        type_="MEAM",
        elements=["Fe", "Cr", "Mo"],  # Mo overlaps with first row
    )
    await _seed_potential(
        db_session,
        name="lj-ar",
        type_="LJ",
        elements=["Ar"],
    )

    response = await async_client.get("/api/v1/stats")
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["elements"] == ["Ar", "Cr", "Fe", "Mo", "U"]
    # total_elements counts distinct values
    assert data["total_elements"] == 5
    assert data["total_potentials"] == 3
    # Two distinct types seeded
    assert data["total_types"] == 3


@pytest.mark.asyncio
async def test_stats_regression_existing_fields_unchanged(async_client, db_session) -> None:
    """Guard against the existing top-level fields being broken by the elements fix."""
    await _seed_potential(
        db_session,
        name="eam-ni",
        type_="EAM",
        elements=["Ni"],
    )

    response = await async_client.get("/api/v1/stats")
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["total_potentials"] == 1
    assert data["total_elements"] == 1
    assert isinstance(data["recent_potentials"], list)
    assert len(data["recent_potentials"]) == 1
    recent = data["recent_potentials"][0]
    assert recent["name"] == "eam-ni"
    assert recent["type"] == "EAM"
    assert recent["elements"] == ["Ni"]
    # New field present alongside legacy ones
    assert data["elements"] == ["Ni"]
