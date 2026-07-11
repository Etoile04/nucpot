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


# ============================================================
# EntityLinker._fuzzy_alias_match regression tests
# ============================================================


class TestFuzzyAliasMatch:
    """Regression tests for EntityLinker._fuzzy_alias_match.

    Locks the SQL-ILIKE behaviour so future changes preserve
    the documented alias-resolution parity with the old
    Python-side filtering approach.
    """

    @pytest.fixture
    async def seed_alias_nodes(
        self, db_session: AsyncSession,
    ) -> list[KGNode]:
        """Create nodes with JSON-encoded aliases for fuzzy matching."""
        nodes = [
            KGNode(
                node_type="Material",
                label="Uranium Dioxide",
                aliases='["UO2", "urania", "uranium oxide"]',
                confidence=0.95,
            ),
            KGNode(
                node_type="Material",
                label="Zircaloy-4",
                aliases='["Zry-4", "zircaloy four", "Zr-4"]',
                confidence=0.90,
            ),
            KGNode(
                node_type="Material",
                label="Silicon Carbide",
                aliases='["SiC", "silicon carbide", "SiCf"]',
                confidence=0.88,
            ),
            # No aliases — should never match fuzzy
            KGNode(
                node_type="Property",
                label="density",
                aliases=None,
                confidence=0.80,
            ),
        ]
        for node in nodes:
            db_session.add(node)
        await db_session.commit()
        for node in nodes:
            await db_session.refresh(node)
        return nodes

    @pytest.mark.asyncio
    async def test_exact_alias_match(
        self,
        db_session: AsyncSession,
        seed_alias_nodes: list[KGNode],
    ) -> None:
        """Matches an exact alias string (case-insensitive via ILIKE)."""
        from nfm_db.services.kg_re import EntityLinker

        linker = EntityLinker()
        result = await linker._fuzzy_alias_match(db_session, "UO2", corpus_id=None)
        assert result is not None
        assert result.label == "Uranium Dioxide"

    @pytest.mark.asyncio
    async def test_case_insensitive_alias_match(
        self,
        db_session: AsyncSession,
        seed_alias_nodes: list[KGNode],
    ) -> None:
        """Matches alias regardless of case (ILIKE is case-insensitive)."""
        from nfm_db.services.kg_re import EntityLinker

        linker = EntityLinker()
        result = await linker._fuzzy_alias_match(db_session, "uo2", corpus_id=None)
        assert result is not None
        assert result.label == "Uranium Dioxide"

    @pytest.mark.asyncio
    async def test_partial_alias_substring_match(
        self,
        db_session: AsyncSession,
        seed_alias_nodes: list[KGNode],
    ) -> None:
        """Matches when the label is a substring of an alias (ILIKE %label%)."""
        from nfm_db.services.kg_re import EntityLinker

        linker = EntityLinker()
        result = await linker._fuzzy_alias_match(db_session, "carbide", corpus_id=None)
        assert result is not None
        # Could be Silicon Carbide or the alias "silicon carbide"
        assert "carbide" in result.label.lower() or any(
            "carbide" in str(a).lower()
            for a in (result.aliases or [])
        )

    @pytest.mark.asyncio
    async def test_no_match_for_node_without_aliases(
        self,
        db_session: AsyncSession,
        seed_alias_nodes: list[KGNode],
    ) -> None:
        """Returns None when no nodes have matching aliases."""
        from nfm_db.services.kg_re import EntityLinker

        linker = EntityLinker()
        result = await linker._fuzzy_alias_match(db_session, "nonexistent", corpus_id=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_first_match_with_limit_1(
        self,
        db_session: AsyncSession,
        seed_alias_nodes: list[KGNode],
    ) -> None:
        """Returns at most one node (query uses LIMIT 1)."""
        from nfm_db.services.kg_re import EntityLinker

        linker = EntityLinker()
        result = await linker._fuzzy_alias_match(db_session, "Zr", corpus_id=None)
        # "Zr" appears in multiple aliases — we just verify we get one node
        assert result is not None
        assert isinstance(result, KGNode)
