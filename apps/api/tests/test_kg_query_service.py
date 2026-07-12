"""Service-level tests for KG query (NFM-858).

Tests for the three query modes (property, relation, path), plus the
Code-Reviewer-requested fixes:

* H1 — Cypher injection guard: unknown ``relation_types`` raises
  ``ValueError``; the graph name is hard-coded.
* M1 — ``max_depth`` is hard-capped at 3.
* M2 — ``direction`` is constrained to one of three values.
* M3 — BFS does not load the full ``kg_edges`` table.
* M4 — AGE Cypher path falls back to relational BFS (the supported path).
* L1 — combined ``property_key`` + ``property_value`` filter (this test).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services import kg_query_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_node(
    session: AsyncSession,
    *,
    node_type: str = "Material",
    label: str = "UO2",
    properties: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
    status: str = "active",
) -> KGNode:
    node = KGNode(
        node_type=node_type,
        label=label,
        aliases=json.dumps(aliases or []),
        properties=properties or {},
        confidence=0.95,
        status=status,
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return node


async def _make_edge(
    session: AsyncSession,
    source: KGNode,
    target: KGNode,
    relation_type: str = "hasProperty",
    properties: dict[str, Any] | None = None,
) -> KGEdge:
    edge = KGEdge(
        source_node_id=source.id,
        target_node_id=target.id,
        relation_type=relation_type,
        properties=properties or {},
        confidence=0.9,
    )
    session.add(edge)
    await session.commit()
    await session.refresh(edge)
    return edge


# ---------------------------------------------------------------------------
# property_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_property_query_combined_property_key_and_value(
    db_session: AsyncSession,
) -> None:
    """L1: combined ``property_key`` + ``property_value`` returns only matching nodes."""
    # (L1) — Two nodes both have a ``density`` key, only the one whose
    # value matches ``10.97`` should be returned.
    uo2 = await _make_node(
        db_session, label="UO2", properties={"density": 10.97, "phase": "fluorite"},
    )
    await _make_node(
        db_session, label="MOX", properties={"density": 11.0, "phase": "fluorite"},
    )

    response = await kg_query_service.property_query(
        db_session,
        property_key="density",
        property_value="10.97",
    )

    assert response.total == 1
    assert [n.label for n in response.nodes] == ["UO2"]
    assert response.nodes[0].id == uo2.id


@pytest.mark.asyncio
async def test_property_query_combined_returns_zero_when_no_match(
    db_session: AsyncSession,
) -> None:
    """L1: combining key + value with no match yields an empty result."""
    await _make_node(db_session, label="UO2", properties={"density": 10.97})

    response = await kg_query_service.property_query(
        db_session,
        property_key="density",
        property_value="99.0",
    )

    assert response.total == 0
    assert response.nodes == []


@pytest.mark.asyncio
async def test_property_query_by_node_type(
    db_session: AsyncSession,
) -> None:
    """Filtering by a valid ``node_type`` restricts results to that type."""
    await _make_node(db_session, node_type="Material", label="UO2")
    await _make_node(db_session, node_type="Property", label="density")

    response = await kg_query_service.property_query(
        db_session, node_type="Material",
    )

    assert response.total == 1
    assert response.nodes[0].node_type == "Material"


@pytest.mark.asyncio
async def test_property_query_invalid_node_type_returns_empty(
    db_session: AsyncSession,
) -> None:
    """Invalid ``node_type`` returns an empty response (no nodes match)."""
    await _make_node(db_session, node_type="Material", label="UO2")

    response = await kg_query_service.property_query(
        db_session, node_type="NotAType",
    )

    assert response.total == 0
    assert response.nodes == []


@pytest.mark.asyncio
async def test_property_query_fuzzy_label(
    db_session: AsyncSession,
) -> None:
    """Fuzzy label search matches substrings (ILIKE)."""
    await _make_node(db_session, label="UO2")
    await _make_node(db_session, label="UO2-Shell")
    await _make_node(db_session, label="MOX")

    response = await kg_query_service.property_query(
        db_session, label="UO2", fuzzy=True,
    )

    assert response.total == 2
    labels = sorted(n.label for n in response.nodes)
    assert labels == ["UO2", "UO2-Shell"]


@pytest.mark.asyncio
async def test_property_query_excludes_merged_nodes(
    db_session: AsyncSession,
) -> None:
    """``status != 'active'`` nodes are filtered out by default."""
    await _make_node(db_session, label="UO2", status="active")
    await _make_node(db_session, label="Old", status="merged")

    response = await kg_query_service.property_query(db_session)
    assert response.total == 1
    assert response.nodes[0].label == "UO2"


@pytest.mark.asyncio
async def test_property_query_pagination(
    db_session: AsyncSession,
) -> None:
    """Limit + offset are honored."""
    for i in range(5):
        await _make_node(db_session, label=f"Material-{i:02d}")

    page_1 = await kg_query_service.property_query(
        db_session, limit=2, offset=0,
    )
    page_2 = await kg_query_service.property_query(
        db_session, limit=2, offset=2,
    )

    assert page_1.total == 5
    assert len(page_1.nodes) == 2
    assert page_2.total == 5
    assert len(page_2.nodes) == 2
    page_1_ids = {n.id for n in page_1.nodes}
    page_2_ids = {n.id for n in page_2.nodes}
    assert page_1_ids.isdisjoint(page_2_ids)


# ---------------------------------------------------------------------------
# relation_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relation_query_outgoing(
    db_session: AsyncSession,
) -> None:
    """Filtering by ``direction='outgoing'`` returns only edges from the source."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    c = await _make_node(db_session, label="C")
    edge_ab = await _make_edge(db_session, a, b, relation_type="hasProperty")
    await _make_edge(db_session, a, c, relation_type="measuredIn")
    incoming = await _make_edge(db_session, c, a, relation_type="references")

    response = await kg_query_service.relation_query(
        db_session,
        source_node_id=a.id,
        direction="outgoing",
    )

    edge_ids = {e.id for e in response.edges}
    assert edge_ab.id in edge_ids
    assert incoming.id not in edge_ids
    assert response.total == 2


@pytest.mark.asyncio
async def test_relation_query_incoming(
    db_session: AsyncSession,
) -> None:
    """Filtering by ``direction='incoming'`` returns only edges into the target."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    edge_ab = await _make_edge(db_session, a, b)

    response = await kg_query_service.relation_query(
        db_session,
        target_node_id=b.id,
        direction="incoming",
    )

    assert {e.id for e in response.edges} == {edge_ab.id}


@pytest.mark.asyncio
async def test_relation_query_both(
    db_session: AsyncSession,
) -> None:
    """Filtering by ``direction='both'`` returns edges in either direction."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    edge_ab = await _make_edge(db_session, a, b)
    edge_ba = await _make_edge(db_session, b, a)

    response = await kg_query_service.relation_query(
        db_session,
        source_node_id=a.id,
        direction="both",
    )

    edge_ids = {e.id for e in response.edges}
    assert edge_ids == {edge_ab.id, edge_ba.id}


@pytest.mark.asyncio
async def test_relation_query_invalid_relation_type_returns_empty(
    db_session: AsyncSession,
) -> None:
    """An unknown ``relation_type`` returns an empty response (no edges match)."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    await _make_edge(db_session, a, b, relation_type="hasProperty")

    response = await kg_query_service.relation_query(
        db_session, relation_type="not_a_real_type",
    )

    assert response.total == 0
    assert response.edges == []


@pytest.mark.asyncio
async def test_relation_query_returns_referenced_nodes(
    db_session: AsyncSession,
) -> None:
    """The response includes the nodes referenced by the matching edges."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    await _make_edge(db_session, a, b)

    response = await kg_query_service.relation_query(
        db_session, source_node_id=a.id, direction="outgoing",
    )

    node_ids = {n.id for n in response.nodes}
    assert {a.id, b.id} == node_ids


# ---------------------------------------------------------------------------
# path_query (BFS path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_query_finds_two_hop_path(
    db_session: AsyncSession,
) -> None:
    """Two-hop path ``A → B → C`` is returned as a single PathResult."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    c = await _make_node(db_session, label="C")
    await _make_edge(db_session, a, b, relation_type="hasProperty")
    await _make_edge(db_session, b, c, relation_type="measuredIn")

    response = await kg_query_service.path_query(
        db_session,
        source_node_id=a.id,
        target_node_id=c.id,
        max_depth=3,
    )

    assert response.total == 1
    path = response.paths[0]
    assert [n.label for n in path.nodes] == ["A", "B", "C"]
    assert path.length == 2
    assert path.edges[0].relation_type == "hasProperty"
    assert path.edges[1].relation_type == "measuredIn"


@pytest.mark.asyncio
async def test_path_query_no_path_returns_empty(
    db_session: AsyncSession,
) -> None:
    """Disconnected source/target returns no paths."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")

    response = await kg_query_service.path_query(
        db_session,
        source_node_id=a.id,
        target_node_id=b.id,
        max_depth=3,
    )

    assert response.total == 0
    assert response.paths == []


@pytest.mark.asyncio
async def test_path_query_respects_max_depth(
    db_session: AsyncSession,
) -> None:
    """A three-edge path is NOT returned when ``max_depth=2``."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    c = await _make_node(db_session, label="C")
    d = await _make_node(db_session, label="D")
    await _make_edge(db_session, a, b)
    await _make_edge(db_session, b, c)
    await _make_edge(db_session, c, d)

    response = await kg_query_service.path_query(
        db_session,
        source_node_id=a.id,
        target_node_id=d.id,
        max_depth=2,
    )

    assert response.total == 0
    assert response.paths == []


@pytest.mark.asyncio
async def test_path_query_relation_types_filter(
    db_session: AsyncSession,
) -> None:
    """``relation_types`` restricts which edges can be traversed."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    await _make_edge(db_session, a, b, relation_type="hasProperty")
    intermediate = await _make_node(db_session, label="I")
    await _make_edge(db_session, a, intermediate, relation_type="measuredIn")
    await _make_edge(db_session, intermediate, b, relation_type="measuredIn")

    response = await kg_query_service.path_query(
        db_session,
        source_node_id=a.id,
        target_node_id=b.id,
        max_depth=3,
        relation_types=["measuredIn"],
    )

    assert response.total == 1
    node_labels = [n.label for n in response.paths[0].nodes]
    assert "I" in node_labels


@pytest.mark.asyncio
async def test_path_query_target_reachable_directly(
    db_session: AsyncSession,
) -> None:
    """An edge ``A → B`` is returned as a one-edge path."""
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    await _make_edge(db_session, a, b, relation_type="hasProperty")

    response = await kg_query_service.path_query(
        db_session,
        source_node_id=a.id,
        target_node_id=b.id,
        max_depth=3,
    )

    assert response.total == 1
    assert response.paths[0].length == 1
    assert response.paths[0].edges[0].relation_type == "hasProperty"


# ---------------------------------------------------------------------------
# AGE Cypher guards (H1) — no live AGE required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_age_path_query_unknown_relation_type_raises(
    db_session: AsyncSession,
) -> None:
    """H1: unknown ``relation_types`` raises ``ValueError`` (no Cypher injection)."""
    a = uuid.uuid4()
    b = uuid.uuid4()

    with pytest.raises(ValueError) as excinfo:
        await kg_query_service._try_age_path_query(
            db_session,
            source_node_id=a,
            target_node_id=b,
            max_depth=3,
            relation_types=["not_a_relation", "still_not_a_relation"],
        )
    assert "unknown relation_types" in str(excinfo.value)


@pytest.mark.asyncio
async def test_age_path_query_max_depth_out_of_range_raises(
    db_session: AsyncSession,
) -> None:
    """M1: ``max_depth`` outside [1, 3] raises ``ValueError`` (defence-in-depth)."""
    a = uuid.uuid4()
    b = uuid.uuid4()

    with pytest.raises(ValueError):
        await kg_query_service._try_age_path_query(
            db_session,
            source_node_id=a,
            target_node_id=b,
            max_depth=10,
            relation_types=None,
        )


def test_kg_query_service_default_graph_name_is_hard_coded() -> None:
    """H1: callers cannot override the AGE graph name (it's hard-coded)."""
    assert kg_query_service.DEFAULT_GRAPH_NAME == "nucpot_kg"
    import inspect
    sig = inspect.signature(kg_query_service._try_age_path_query)
    assert "graph_name" not in sig.parameters


# ---------------------------------------------------------------------------
# BFS adjacency scope (M3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_path_adjacency_bounds_to_reachable_nodes(
    db_session: AsyncSession,
) -> None:
    """M3: the BFS adjacency does not load the whole ``kg_edges`` table.

    Ten edges unrelated to the source/target are seeded; the function
    should not surface them because they are not reachable.
    """
    a = await _make_node(db_session, label="A")
    b = await _make_node(db_session, label="B")
    edge_ab = await _make_edge(db_session, a, b, relation_type="hasProperty")

    unrelated_ids: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for _ in range(10):
        u = await _make_node(db_session)
        v = await _make_node(db_session)
        edge = await _make_edge(db_session, u, v, relation_type="references")
        unrelated_ids.add((edge.source_node_id, edge.target_node_id))

    adjacency = await kg_query_service._load_path_adjacency(
        db_session,
        source_node_id=a.id,
    )

    for src_id, neighbors in adjacency.items():
        for tgt_id, _rel in neighbors:
            pair = (src_id, tgt_id)
            assert pair in {(a.id, b.id), (b.id, a.id)} or pair in unrelated_ids
            if pair in unrelated_ids:
                pytest.fail(
                    f"Unrelated edge {pair} leaked into adjacency for source={a.id!s}",
                )
    assert edge_ab.id is not None


# ---------------------------------------------------------------------------
# BFS algorithm (no DB)
# ---------------------------------------------------------------------------


def _build_adjacency(pairs: Iterable[tuple[uuid.UUID, uuid.UUID, str]]) -> dict[
    uuid.UUID, list[tuple[uuid.UUID, str]]
]:
    """Build an adjacency map the same way ``_load_path_adjacency`` does."""
    adj: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for src, tgt, rel in pairs:
        adj.setdefault(src, []).append((tgt, rel))
        adj.setdefault(tgt, []).append((src, f"_inv_{rel}"))
    return adj


def test_bfs_find_paths_simple_chain() -> None:
    """BFS finds the direct path A → B → C."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    adj = _build_adjacency([(a, b, "hasProperty"), (b, c, "measuredIn")])

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=c, max_depth=3,
        relation_types=None, limit=10,
    )
    assert paths == [[a, b, c]]


def test_bfs_find_paths_branching_chooses_shortest() -> None:
    """When multiple paths exist, BFS picks the shortest first."""
    a = uuid.uuid4()
    via_x = uuid.uuid4()
    via_y = uuid.uuid4()
    b = uuid.uuid4()
    adj = _build_adjacency([
        (a, via_x, "hasProperty"),
        (via_x, b, "measuredIn"),
        (a, via_y, "hasProperty"),
        (via_y, b, "measuredIn"),
    ])

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=b, max_depth=3,
        relation_types=None, limit=10,
    )
    assert len(paths) == 2
    assert all(len(p) == 3 for p in paths)


def test_bfs_find_paths_depth_cap() -> None:
    """A 4-edge path is rejected when ``max_depth=3``."""
    a, b, c, d, e = (uuid.uuid4() for _ in range(5))
    adj = _build_adjacency([
        (a, b, "hasProperty"),
        (b, c, "hasProperty"),
        (c, d, "hasProperty"),
        (d, e, "hasProperty"),
    ])

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=e, max_depth=3,
        relation_types=None, limit=10,
    )
    assert paths == []


def test_bfs_find_paths_relation_type_filter() -> None:
    """``relation_types`` filter excludes forbidden edges."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    adj = _build_adjacency([
        (a, b, "hasProperty"),
        (b, c, "measuredIn"),
    ])

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=c, max_depth=3,
        relation_types=frozenset({"measuredIn"}), limit=10,
    )
    assert paths == []

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=c, max_depth=3,
        relation_types=frozenset({"hasProperty"}), limit=10,
    )
    assert paths == []


def test_bfs_find_paths_handles_inverse_edges() -> None:
    """BFS relaxation works when the KGEdge direction is the reverse of the path."""
    a, b = uuid.uuid4(), uuid.uuid4()
    adj = _build_adjacency([(b, a, "hasProperty")])

    paths = kg_query_service._bfs_find_paths(
        adj, source=a, target=b, max_depth=3,
        relation_types=None, limit=10,
    )
    assert paths == [[a, b]]
