"""Regression tests for GET /api/v1/stats element aggregation (NFM-4310).

BUG-29: the potential-function element filter on /browse and /search was
permanently stuck on 「无匹配元素」 because the stats endpoint returned only
a ``total_elements`` COUNT while the frontend expects the actual element
list. These tests pin the aggregation contract: ``data.elements`` must be
the sorted distinct union of every potential's ``elements`` array.
"""

from __future__ import annotations

import pytest

from nfm_db.models.potential import Potential

pytestmark = pytest.mark.asyncio


def _potential(name: str, potential_type: str, elements: list[str]) -> Potential:
    return Potential(name=name, type=potential_type, elements=elements)


class TestGetStatsElements:
    async def test_returns_sorted_distinct_element_list(self, async_client, db_session):
        """data.elements is the sorted distinct union across all potentials."""
        db_session.add_all(
            [
                _potential("stats-u-o", "EAM", ["U", "O"]),
                _potential("stats-w-ta", "EAM", ["W", "Ta"]),
                _potential("stats-u-mo", "MEAM", ["U", "Mo", "O"]),
            ]
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/stats")

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["elements"] == ["Mo", "O", "Ta", "U", "W"]
        assert payload["data"]["total_elements"] == 5

    async def test_elements_empty_when_library_empty(self, async_client, db_session):
        """An empty library yields an empty (not missing) elements list."""
        response = await async_client.get("/api/v1/stats")

        assert response.status_code == 200
        payload = response.json()
        assert payload["data"]["elements"] == []
        assert payload["data"]["total_elements"] == 0

    async def test_skips_potentials_without_elements(self, async_client, db_session):
        """Rows with null/empty elements arrays must not corrupt the union."""
        db_session.add_all(
            [
                _potential("stats-fe-ni", "EAM", ["Fe", "Ni"]),
                _potential("stats-no-elements", "EAM", []),
            ]
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/stats")

        assert response.status_code == 200
        assert response.json()["data"]["elements"] == ["Fe", "Ni"]
