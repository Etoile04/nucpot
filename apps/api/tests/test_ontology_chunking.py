"""Large-graph chunking tests (T6: RED phase).

Each page is a coherent material ego-subgraph bounded by ``max_nodes``; cursor
walks the whole corpus; no single response exceeds the requested ceiling; every
page passes contract conformance (referential integrity holds).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.nvl_conformance import assert_nvl_contract
from tests.ontology_seed import seed_corpus

_CORPUS = "smirnov2014"
# 3 materials x (mat + prop + method) + 1 shared source = 9 nodes total.
_MAX_NODES = 6


@pytest.fixture
async def seeded_corpus(db_session: AsyncSession) -> None:
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
            {
                "element_system": "U",
                "property_name": "bulk_modulus",
                "value": 200.0,
                "unit": "GPa",
                "method": "EXP",
            },
            {
                "element_system": "Zr",
                "property_name": "density",
                "value": 6.52,
                "unit": "g/cm3",
                "method": "DFT",
            },
        ],
    )


@pytest.mark.asyncio
async def test_first_page_respects_ceiling_and_paginates(
    async_client,
    seeded_corpus: None,
) -> None:
    """Page 1 has <= max_nodes nodes and announces more via next_cursor."""
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{_CORPUS}/graph",
        params={"max_nodes": _MAX_NODES},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["nodes"]) <= _MAX_NODES
    assert data["pagination"] is not None
    assert data["pagination"]["next_cursor"]
    assert data["pagination"]["total"] == 9
    assert_nvl_contract(data, corpus_id=_CORPUS)


@pytest.mark.asyncio
async def test_cursor_walks_entire_corpus(
    async_client,
    seeded_corpus: None,
) -> None:
    """Following next_cursor covers every node without exceeding the ceiling."""
    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        params = {"max_nodes": _MAX_NODES}
        if cursor:
            params["cursor"] = cursor
        response = await async_client.get(
            f"/api/v1/ontology/corpora/{_CORPUS}/graph",
            params=params,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data["nodes"]) <= _MAX_NODES, "page exceeded ceiling"
        assert_nvl_contract(data, corpus_id=_CORPUS)
        seen.update(n["id"] for n in data["nodes"])
        pages += 1
        cursor = data["pagination"]["next_cursor"] if data["pagination"] else None
        if not cursor:
            break
        assert pages < 20, "cursor walk did not terminate"

    # Every corpus node appears in at least one page (src repeats across pages).
    assert seen == {
        "mat:UO2",
        "mat:U",
        "mat:Zr",
        "prop:lattice_constant",
        "prop:bulk_modulus",
        "prop:density",
        "method:DFT",
        "method:EXP",
        f"src:{_CORPUS}",
    }


@pytest.mark.asyncio
async def test_small_corpus_returns_full_graph_unpaginated(
    async_client,
    seeded_corpus: None,
) -> None:
    """Corpus under the ceiling returns the full graph with no pagination."""
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{_CORPUS}/graph",
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["pagination"] is None
    assert len(data["nodes"]) == 9


@pytest.mark.asyncio
async def test_max_nodes_zero_rejected(async_client) -> None:
    """max_nodes must be >= 1 (422)."""
    response = await async_client.get(
        f"/api/v1/ontology/corpora/{_CORPUS}/graph",
        params={"max_nodes": 0},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_single_ego_never_exceeds_ceiling(
    async_client,
    db_session: AsyncSession,
) -> None:
    """A single material whose ego subgraph exceeds max_nodes is still capped.

    The T6 invariant is unconditional: no single response may exceed the
    ceiling, even when one material alone is larger than max_nodes. (Realistic
    Phase 1 corpora never hit this -- single materials carry << HARD_MAX_NODES
    properties -- but the ceiling must hold regardless.)
    """
    await seed_corpus(
        db_session,
        source="bigcorpus",
        rows=[
            {
                "element_system": "BIG",
                "property_name": f"p{i}",
                "value": float(i),
                "unit": "unit",
                "method": "DFT",
            }
            for i in range(5)
        ],
    )
    # ego(mat:BIG) = mat + 5 props + method + src = 8 nodes; ceiling 3.
    response = await async_client.get(
        "/api/v1/ontology/corpora/bigcorpus/graph",
        params={"max_nodes": 3},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["nodes"]) <= 3, "response exceeded the max_nodes ceiling"
    assert_nvl_contract(data, corpus_id="bigcorpus")
