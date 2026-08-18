"""Corpus index endpoint tests (NFM-3303).

``GET /api/v1/ontology/corpora`` enumerates corpora that actually have
``_ref_gap_fill_staging`` rows, so the frontend corpus dropdown can never
advertise a corpus whose graph request would 404.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.ontology_seed import seed_corpus


@pytest.mark.asyncio
async def test_list_corpora_empty_db_returns_empty_list(async_client) -> None:
    """No staging rows anywhere → 200 with empty corpora list (not 404)."""
    response = await async_client.get("/api/v1/ontology/corpora")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data == {"corpora": []}


@pytest.mark.asyncio
async def test_list_corpora_returns_seeded_corpora(
    async_client,
    db_session: AsyncSession,
) -> None:
    """Seeded corpora are listed with row counts, largest first."""
    await seed_corpus(
        db_session,
        source="smirnov2014",
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
            {
                "element_system": "UO2",
                "property_name": "density",
                "value": 10.97,
                "unit": "g/cm3",
                "method": "experiment",
            },
        ],
    )
    await seed_corpus(
        db_session,
        source="wang2016",
        rows=[
            {
                "element_system": "U-Mo",
                "property_name": "thermal_conductivity",
                "value": 20.0,
                "unit": "W/mK",
                "method": "experiment",
            },
        ],
    )

    response = await async_client.get("/api/v1/ontology/corpora")
    assert response.status_code == 200, response.text
    corpora = response.json()["corpora"]
    assert [c["corpus_id"] for c in corpora] == ["smirnov2014", "wang2016"]
    by_id = {c["corpus_id"]: c for c in corpora}
    assert by_id["smirnov2014"]["row_count"] == 2
    assert by_id["wang2016"]["row_count"] == 1
    # last_updated is populated (TimestampMixin column).
    assert by_id["smirnov2014"]["last_updated"] is not None


@pytest.mark.asyncio
async def test_list_corpora_excludes_blank_source_rows(
    async_client,
    db_session: AsyncSession,
) -> None:
    """Legacy rows with source='' must not surface as a bogus corpus (NFM-3303).

    Production (2026-08-17) had 105 rows with blank source — they are not a
    valid slug and their graph request can never resolve, so the index must
    not advertise them.
    """
    await seed_corpus(
        db_session,
        source="",
        rows=[
            {
                "element_system": "U",
                "property_name": "density",
                "value": 19.1,
                "unit": "g/cm3",
            },
        ],
    )

    response = await async_client.get("/api/v1/ontology/corpora")
    assert response.status_code == 200, response.text
    corpora = response.json()["corpora"]
    assert corpora == []


@pytest.mark.asyncio
async def test_list_corpora_excludes_non_slug_sources(
    async_client,
    db_session: AsyncSession,
) -> None:
    """Sources that fail CORPUS_ID_PATTERN must not be advertised (NFM-3303).

    Production staging carries DOI-shaped sources like
    ``10.1016/j.jnucmat.2023.154543`` containing ``/``. The graph endpoint's
    path validator would 422 on them, so listing them would recreate the
    exact drift bug this endpoint exists to prevent (index advertising a
    corpus whose graph request cannot succeed).
    """
    await seed_corpus(
        db_session,
        source="10.1016/j.jnucmat.2023.154543",
        rows=[
            {
                "element_system": "UO2",
                "property_name": "density",
                "value": 10.97,
                "unit": "g/cm3",
            },
        ],
    )
    await seed_corpus(
        db_session,
        source="valid-slug",
        rows=[
            {
                "element_system": "UO2",
                "property_name": "density",
                "value": 10.97,
                "unit": "g/cm3",
            },
        ],
    )

    response = await async_client.get("/api/v1/ontology/corpora")
    assert response.status_code == 200, response.text
    corpora = response.json()["corpora"]
    assert [c["corpus_id"] for c in corpora] == ["valid-slug"]


@pytest.mark.asyncio
async def test_list_corpora_graph_never_404s_for_listed_corpora(
    async_client,
    db_session: AsyncSession,
) -> None:
    """Invariant: every corpus listed here returns 200 from the graph endpoint."""
    await seed_corpus(
        db_session,
        source="roundtrip",
        rows=[
            {
                "element_system": "UO2",
                "property_name": "density",
                "value": 10.97,
                "unit": "g/cm3",
            },
        ],
    )

    listed = (await async_client.get("/api/v1/ontology/corpora")).json()["corpora"]
    assert [c["corpus_id"] for c in listed] == ["roundtrip"]
    graph = await async_client.get("/api/v1/ontology/corpora/roundtrip/graph")
    assert graph.status_code == 200, graph.text


@pytest.mark.asyncio
async def test_list_corpora_cache_headers(async_client) -> None:
    """Derived reference data: public short cache + ETag present."""
    response = await async_client.get("/api/v1/ontology/corpora")
    assert response.status_code == 200
    assert response.headers.get("Cache-Control") == "public, max-age=60"
    assert response.headers.get("ETag"), "ETag header missing"
