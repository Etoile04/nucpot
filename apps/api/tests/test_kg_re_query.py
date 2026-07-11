"""Tests for kg_re.py query functions (query_graph_nodes, query_graph_edges).

Covers:
- Query all nodes with default params
- Filter by entity_types
- Text search across node labels
- Limit enforcement
- Empty DB returns empty list
- Edge retrieval by matching node IDs
- Edge filtering by relation_type
- Edge limit enforcement
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_re import query_graph_edges, query_graph_nodes


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
async def seed_kg_nodes(db_session: AsyncSession) -> list[KGNode]:
    """Create a small set of KG nodes for query tests."""
    nodes = [
        KGNode(node_type="Material", label="Uranium Dioxide", confidence=0.95),
        KGNode(node_type="Material", label="Zircaloy-4", confidence=0.90),
        KGNode(node_type="Property", label="thermal conductivity", confidence=0.85),
        KGNode(node_type="Property", label="density", confidence=0.88),
        KGNode(node_type="Experiment", label="pellet sintering", confidence=0.70),
        KGNode(
            node_type="Material",
            label="Stainless Steel 316",
            confidence=0.80,
            status="active",
        ),
        KGNode(
            node_type="Material",
            label="Deprecated Alloy X",
            confidence=0.50,
            status="deprecated",
        ),
    ]
    for node in nodes:
        db_session.add(node)
    await db_session.commit()
    for node in nodes:
        await db_session.refresh(node)
    return nodes


@pytest.fixture
async def seed_kg_edges(
    db_session: AsyncSession, seed_kg_nodes: list[KGNode],
) -> list[KGEdge]:
    """Create edges linking the seeded nodes."""
    uo2, zr4, tc, density, pellet, ss316, _ = seed_kg_nodes

    edges = [
        KGEdge(
            source_node_id=uo2.id,
            target_node_id=tc.id,
            relation_type="hasProperty",
            confidence=0.9,
        ),
        KGEdge(
            source_node_id=uo2.id,
            target_node_id=density.id,
            relation_type="hasProperty",
            confidence=0.85,
        ),
        KGEdge(
            source_node_id=pellet.id,
            target_node_id=uo2.id,
            relation_type="measuredIn",
            confidence=0.7,
        ),
        KGEdge(
            source_node_id=ss316.id,
            target_node_id=tc.id,
            relation_type="hasProperty",
            confidence=0.75,
        ),
    ]
    for edge in edges:
        db_session.add(edge)
    await db_session.commit()
    for edge in edges:
        await db_session.refresh(edge)
    return edges


# ============================================================
# query_graph_nodes tests
# ============================================================


class TestQueryGraphNodes:
    """Tests for the query_graph_nodes function."""

    @pytest.mark.asyncio
    async def test_query_all_active_nodes(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Returns all active nodes when no filters are applied."""
        nodes = await query_graph_nodes(db_session)
        labels = {n.label for n in nodes}
        # "Deprecated Alloy X" has status=deprecated, should be excluded
        assert "Uranium Dioxide" in labels
        assert "Zircaloy-4" in labels
        assert "Deprecated Alloy X" not in labels

    @pytest.mark.asyncio
    async def test_filter_by_entity_type(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Filters nodes by a single entity type."""
        nodes = await query_graph_nodes(db_session, entity_types=["Material"])
        assert all(n.node_type == "Material" for n in nodes)
        labels = {n.label for n in nodes}
        assert "Uranium Dioxide" in labels
        assert "thermal conductivity" not in labels

    @pytest.mark.asyncio
    async def test_filter_by_multiple_entity_types(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Filters nodes by multiple entity types."""
        nodes = await query_graph_nodes(
            db_session, entity_types=["Material", "Property"],
        )
        types = {n.node_type for n in nodes}
        assert types == {"Material", "Property"}

    @pytest.mark.asyncio
    async def test_text_search_on_label(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Finds nodes whose label contains the query string (case-insensitive)."""
        nodes = await query_graph_nodes(db_session, query="uranium")
        labels = [n.label for n in nodes]
        assert "Uranium Dioxide" in labels
        assert "Zircaloy-4" not in labels

    @pytest.mark.asyncio
    async def test_text_search_combined_with_type_filter(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Combines text search with entity type filter."""
        nodes = await query_graph_nodes(
            db_session, query="conductivity", entity_types=["Property"],
        )
        assert len(nodes) == 1
        assert nodes[0].label == "thermal conductivity"

    @pytest.mark.asyncio
    async def test_limit_enforcement(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Respects the limit parameter."""
        nodes = await query_graph_nodes(db_session, limit=2)
        assert len(nodes) <= 2

    @pytest.mark.asyncio
    async def test_empty_database_returns_empty(
        self, db_session: AsyncSession,
    ) -> None:
        """Returns empty list when no active nodes exist."""
        nodes = await query_graph_nodes(db_session)
        assert nodes == []

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(
        self, db_session: AsyncSession, seed_kg_nodes: list[KGNode],
    ) -> None:
        """Returns empty list when no nodes match the query."""
        nodes = await query_graph_nodes(db_session, query="nonexistent material")
        assert nodes == []


# ============================================================
# query_graph_edges tests
# ============================================================


class TestQueryGraphEdges:
    """Tests for the query_graph_edges function."""

    @pytest.mark.asyncio
    async def test_edges_for_matching_nodes(
        self,
        db_session: AsyncSession,
        seed_kg_edges: list[KGEdge],
        seed_kg_nodes: list[KGNode],
    ) -> None:
        """Returns edges connected to the given node IDs."""
        uo2 = seed_kg_nodes[0]
        edges = await query_graph_edges(db_session, node_ids={uo2.id})
        # UO2 has 2 outgoing edges (hasProperty→tc, hasProperty→density)
        # and 1 incoming edge (pellet→measuredIn→UO2)
        assert len(edges) == 3

    @pytest.mark.asyncio
    async def test_filter_by_relation_type(
        self,
        db_session: AsyncSession,
        seed_kg_edges: list[KGEdge],
        seed_kg_nodes: list[KGNode],
    ) -> None:
        """Filters edges by relation type."""
        uo2 = seed_kg_nodes[0]
        edges = await query_graph_edges(
            db_session, node_ids={uo2.id}, relation_type="hasProperty",
        )
        assert all(e.relation_type == "hasProperty" for e in edges)
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_limit_enforcement(
        self,
        db_session: AsyncSession,
        seed_kg_edges: list[KGEdge],
        seed_kg_nodes: list[KGNode],
    ) -> None:
        """Respects the limit parameter."""
        uo2 = seed_kg_nodes[0]
        edges = await query_graph_edges(db_session, node_ids={uo2.id}, limit=1)
        assert len(edges) <= 1

    @pytest.mark.asyncio
    async def test_empty_node_ids_returns_empty(
        self, db_session: AsyncSession, seed_kg_edges: list[KGEdge],
    ) -> None:
        """Returns empty list when no node IDs provided."""
        edges = await query_graph_edges(db_session, node_ids=set())
        assert edges == []

    @pytest.mark.asyncio
    async def test_nonexistent_node_ids_returns_empty(
        self, db_session: AsyncSession, seed_kg_edges: list[KGEdge],
    ) -> None:
        """Returns empty list when node IDs don't exist in DB."""
        import uuid

        edges = await query_graph_edges(
            db_session, node_ids={uuid.uuid4()},
        )
        assert edges == []
