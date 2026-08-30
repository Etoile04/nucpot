"""Regression tests for NFM-3825: /api/v1/kg/graph source_id filter + offset pagination.

Reproduction (2026-08-30, production + local prod stack):

1. ``GET /api/v1/kg/graph?source_id=<UUID>`` returned the global first 200 nodes
   instead of nodes scoped to that source — ``source_id`` was silently dropped.
2. ``GET /api/v1/kg/graph?limit=200&offset=200`` returned the same content as
   ``offset=0`` — ``offset`` was not honoured by the route.
3. ``limit=1000`` returned ``VALIDATION_ERROR`` (the route hard-caps at 500,
   which is the only thing the original endpoint honoured).

This file locks the contract:

- ``?source_id=<UUID>`` filters nodes to that source only (verified against
  ``SELECT COUNT(*) FROM kg_nodes WHERE source_id = <UUID>``).
- ``?limit=X&offset=Y`` paginates the filtered node set deterministically.
- The ``total_nodes`` reported in the response reflects the source_id filter.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, KGNode

_S1 = uuid.UUID("b0000001-0000-0000-0000-000000000001")
_S2 = uuid.UUID("b0000002-0000-0000-0000-000000000002")
_S3 = uuid.UUID("b0000003-0000-0000-0000-000000000003")


def _make_node(idx: int, source_id: uuid.UUID, *, label: str | None = None) -> KGNode:
    """Return a KGNode with a deterministic label for ordering assertions."""
    return KGNode(
        # node IDs are unique across all rows so other tests do not collide
        id=uuid.UUID(f"c0000000-0000-0000-0000-{idx:012d}"),
        node_type="Material",
        label=label or f"Node-{idx:04d}",
        status="active",
        confidence=0.95,
        properties={"formula": f"M{idx:04d}"},
        source_id=source_id,
    )


def _make_source(source_id: uuid.UUID, title: str) -> DataSource:
    """Return a DataSource with explicit id (so FK matches ``kg_nodes.source_id``)."""
    return DataSource(
        id=source_id,
        title=title,
        source_type="journal",
    )


@pytest.fixture
async def seeded_source_nodes(db_session: AsyncSession):
    """Seed ``data_sources`` (FK parent) + ``kg_nodes`` for three sources.

    Counts: S1 -> 3 nodes, S2 -> 2 nodes, S3 -> 1 node.

    Yields ``{"s1": 3, "s2": 2, "s3": 1}`` so tests can assert the expected
    ``total_nodes`` per source.
    """
    # FK parent rows — required because kg_nodes.source_id has a FK to data_sources.id.
    db_session.add_all([
        _make_source(_S1, "Source One Paper"),
        _make_source(_S2, "Source Two Paper"),
        _make_source(_S3, "Source Three Paper"),
    ])
    # S1 -> 3 nodes
    db_session.add_all(_make_node(i, _S1) for i in range(1, 4))
    # S2 -> 2 nodes
    db_session.add_all(_make_node(i, _S2) for i in range(100, 102))
    # S3 -> 1 node
    db_session.add(_make_node(200, _S3))
    await db_session.commit()
    return {"s1": 3, "s2": 2, "s3": 1}


# ---------------------------------------------------------------------------
# source_id filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kg_graph_filters_by_source_id(
    async_client, seeded_source_nodes
) -> None:
    """``?source_id=X`` must return only nodes with that source_id.

    Regression for NFM-3825 reproduction step 1.
    """
    expected = seeded_source_nodes["s1"]

    response = await async_client.get(
        "/api/v1/kg/graph",
        params={"source_id": str(_S1), "limit": 50},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("success") is True, body
    payload = body["data"]

    # total_nodes reflects the FILTERED count, not global.
    assert payload["total_nodes"] == expected, (
        f"total_nodes should equal SELECT COUNT(*) WHERE source_id={_S1} "
        f"({expected}), got {payload['total_nodes']}"
    )
    # And no node is returned for another source.
    for node in payload["nodes"]:
        assert node["source_id"] == str(_S1), (
            f"leaked node from another source: {node['source_id']} ({node['label']})"
        )
        assert len(payload["nodes"]) <= expected


@pytest.mark.asyncio
async def test_kg_graph_without_source_id_returns_global(
    async_client, seeded_source_nodes
) -> None:
    """Omitting ``source_id`` keeps backward-compatible global behaviour."""
    total_expected = sum(seeded_source_nodes.values())  # 3 + 2 + 1 = 6

    response = await async_client.get(
        "/api/v1/kg/graph",
        params={"limit": 50},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["total_nodes"] == total_expected


@pytest.mark.asyncio
async def test_kg_graph_rejects_invalid_source_id(async_client) -> None:
    """A non-UUID ``source_id`` is rejected with 422 (not silently dropped)."""
    response = await async_client.get(
        "/api/v1/kg/graph",
        params={"source_id": "not-a-uuid"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# offset pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kg_graph_offset_paginates_filtered_nodes(
    async_client, seeded_source_nodes
) -> None:
    """``?limit=2&offset=N`` walks through the filtered node set.

    Regression for NFM-3825 reproduction step 2.
    """
    expected = seeded_source_nodes["s1"]  # 3 nodes, ordered by label ascending
    expected_labels = sorted(
        _make_node(i, _S1).label for i in (1, 2, 3)
    )

    # Page 1: first 2 nodes
    r1 = await async_client.get(
        "/api/v1/kg/graph",
        params={"source_id": str(_S1), "limit": 2, "offset": 0},
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()["data"]
    assert p1["total_nodes"] == expected
    page1_labels = [n["label"] for n in p1["nodes"]]
    assert page1_labels == expected_labels[:2]

    # Page 2: remaining 1 node
    r2 = await async_client.get(
        "/api/v1/kg/graph",
        params={"source_id": str(_S1), "limit": 2, "offset": 2},
    )
    assert r2.status_code == 200, r2.text
    p2 = r2.json()["data"]
    page2_labels = [n["label"] for n in p2["nodes"]]
    assert page2_labels == expected_labels[2:]

    # Page 3: empty
    r3 = await async_client.get(
        "/api/v1/kg/graph",
        params={"source_id": str(_S1), "limit": 2, "offset": 4},
    )
    assert r3.status_code == 200, r3.text
    p3 = r3.json()["data"]
    assert p3["nodes"] == []

    # No overlap between pages.
    assert set(page1_labels) & set(page2_labels) == set()


@pytest.mark.asyncio
async def test_kg_graph_rejects_negative_offset(async_client) -> None:
    """``offset`` must be ``>= 0``."""
    response = await async_client.get(
        "/api/v1/kg/graph",
        params={"offset": -1},
    )
    assert response.status_code == 422
