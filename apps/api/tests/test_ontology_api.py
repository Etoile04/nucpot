"""Ontology endpoint tests (T4: RED phase).

``GET /api/v1/ontology/corpora/{corpus_id}/graph`` emits the versioned NVL
contract. ``corpus_id`` is path-validated (slug → 422 on malformation), and an
empty/unknown corpus returns 404.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.ontology_seed import seed_corpus

_CORPUS = "smirnov2014"


@pytest.mark.asyncio
async def test_get_corpus_graph_returns_versioned_contract(
    async_client,
    db_session: AsyncSession,
) -> None:
    """Seeded corpus returns 200 with the versioned NVL envelope."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{_CORPUS}/graph",
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["schema_version"] == "1.1"
    assert data["corpus_id"] == _CORPUS
    # Relationship endpoints serialize as `from` (viewer-compatible alias).
    assert "from" in data["relationships"][0]
    assert "from_" not in data["relationships"][0]


@pytest.mark.asyncio
async def test_get_corpus_graph_404_for_unknown_corpus(async_client) -> None:
    """A corpus with no staging rows returns 404, not an empty graph."""
    response = await async_client.get(
        "/api/v1/ontology/corpora/no-such-corpus/graph",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_corpus_id",
    [
        "bad slug!",  # invalid characters
        "a" * 65,  # exceeds 64-char ceiling
        ".hidden",  # leading dot/symbol
    ],
)
async def test_get_corpus_graph_422_for_malformed_corpus_id(
    async_client,
    bad_corpus_id: str,
) -> None:
    """Malformed corpus_id (not a safe slug) is rejected with 422."""
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{bad_corpus_id}/graph",
    )
    assert response.status_code == 422, (bad_corpus_id, response.text)
