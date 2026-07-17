"""Unit tests for KG query service (NFM-858).

Tests the three query modes: property, relation, and path.
Uses the shared db_session fixture (SQLite) from conftest.py.
"""

from __future__ import annotations

import json
import uuid

import pytest

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_query_service import (
    _bfs_find_paths,
    _edge_to_response,
    _node_to_response,
    path_query,
    property_query,
    relation_query,
)


# ---------------------------------------------------------------------------
# Fixtures — deterministic IDs for reproducibility
# ---------------------------------------------------------------------------

_UO2_ID = uuid.uuid4()
_DENSITY_PROP_ID = uuid.uuid4()
_MELTING_PT_ID = uuid.uuid4()
_EXPERIMENT_ID = uuid.uuid4()
_PUO2_ID = uuid.uuid4()


@pytest.fixture
async def seed_nodes(db_session) -> dict[str, uuid.UUID]:
    """Create a small test graph for property and relation queries."""
    nodes = [
        KGNode(
            id=_UO2_ID,
            node_type="Material",
            label="Uranium Dioxide",
            aliases=json.dumps(["UO2", "Urania"]),
            properties={"formula": "UO2", "crystal": "Fluorite"},
            confidence=0.95,
            status="active",
        ),
        KGNode(
            id=_DENSITY_PROP_ID,
            node_type="Property",
            label="Density",
            aliases=json.dumps(["ρ"]),
            properties={"unit": "g/cm³", "type": "physical"},
            confidence=0.9,
            status="active",
        ),
        KGNode(
            id=_MELTING_PT_ID,
            node_type="Property",
            label="Melting Point",
            properties={"unit": "K", "type": "thermal"},
            confidence=0.85,
            status="active",
        ),
        KGNode(
            id=_EXPERIMENT_ID,
            node_type="Experiment",
            label="Density Measurement 2024",
            properties={"method": "Archimedes"},
            confidence=0.8,
            status="active",
        ),
        KGNode(
            id=_PUO2_ID,
            node_type="Material",
            label="Plutonium Dioxide",
            properties={"formula": "PuO2"},
            confidence=0.92,
            status="active",
        ),
        KGNode(
            node_type="Material",
            label="Deprecated Material",
            status="deprecated",
        ),
    ]
    for node in nodes:
        db_session.add(node)
    await db_session.flush()
    return {
        "uo2": _UO2_ID,
        "density": _DENSITY_PROP_ID,
        "melting": _MELTING_PT_ID,
        "experiment": _EXPERIMENT_ID,
        "puo2": _PUO2_ID,
    }


@pytest.fixture
async def seed_edges(db_session, seed_nodes) -> None:
    """Create edges connecting the seeded nodes."""
    ids = seed_nodes
    edges = [
        KGEdge(
            source_node_id=ids["uo2"],
            target_node_id=ids["density"],
            relation_type="hasProperty",
            confidence=0.9,
        ),
        KGEdge(
            source_node_id=ids["uo2"],
            target_node_id=ids["melting"],
            relation_type="hasProperty",
            confidence=0.85,
        ),
        KGEdge(
            source_node_id=ids["experiment"],
            target_node_id=ids["uo2"],
            relation_type="measuredIn",
            confidence=0.8,
        ),
        KGEdge(
            source_node_id=ids["puo2"],
            target_node_id=ids["density"],
            relation_type="hasProperty",
            confidence=0.88,
        ),
    ]
    for edge in edges:
        db_session.add(edge)
    await db_session.flush()


# ---------------------------------------------------------------------------
# Helper conversions
# ---------------------------------------------------------------------------


class TestNodeToResponse:
    """Tests for _node_to_response helper."""

    def test_basic_conversion(self) -> None:
        node = KGNode(
            id=uuid.uuid4(),
            node_type="Material",
            label="Test",
            aliases='["alias1", "alias2"]',
            properties={"key": "val"},
            confidence=0.5,
        )
        result = _node_to_response(node)
        assert result.id == node.id
        assert result.node_type == "Material"
        assert result.label == "Test"
        assert result.aliases == ["alias1", "alias2"]
        assert result.properties == {"key": "val"}
        assert result.confidence == 0.5

    def test_none_aliases(self) -> None:
        node = KGNode(id=uuid.uuid4(), node_type="Material", label="X", confidence=1.0)
        result = _node_to_response(node)
        assert result.aliases == []

    def test_invalid_aliases_json(self) -> None:
        node = KGNode(id=uuid.uuid4(), node_type="Material", label="X", aliases="not-json", confidence=1.0)
        result = _node_to_response(node)
        assert result.aliases == []

    def test_aliases_not_list(self) -> None:
        node = KGNode(id=uuid.uuid4(), node_type="Material", label="X", aliases='"string"', confidence=1.0)
        result = _node_to_response(node)
        assert result.aliases == []

    def test_none_properties(self) -> None:
        node = KGNode(
            id=uuid.uuid4(), node_type="Material", label="X", properties=None, confidence=1.0,
        )
        result = _node_to_response(node)
        assert result.properties == {}


class TestEdgeToResponse:
    """Tests for _edge_to_response helper."""

    def test_basic_conversion(self) -> None:
        edge = KGEdge(
            id=uuid.uuid4(),
            source_node_id=uuid.uuid4(),
            target_node_id=uuid.uuid4(),
            relation_type="hasProperty",
            properties={"context": "test"},
            confidence=0.7,
        )
        result = _edge_to_response(edge)
        assert result.relation_type == "hasProperty"
        assert result.properties == {"context": "test"}

    def test_none_properties(self) -> None:
        edge = KGEdge(
            id=uuid.uuid4(),
            source_node_id=uuid.uuid4(),
            target_node_id=uuid.uuid4(),
            relation_type="relatedTo",
            properties=None,
            confidence=1.0,
        )
        result = _edge_to_response(edge)
        assert result.properties == {}


# ---------------------------------------------------------------------------
# Property Query
# ---------------------------------------------------------------------------


class TestPropertyQuery:
    """Tests for property_query service function."""

    @pytest.mark.asyncio
    async def test_empty_result(self, db_session) -> None:
        result = await property_query(db_session, label="nonexistent")
        assert result.nodes == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_find_by_label_exact(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, label="Uranium Dioxide")
        assert result.total == 1
        assert result.nodes[0].label == "Uranium Dioxide"
        assert result.nodes[0].node_type == "Material"

    @pytest.mark.asyncio
    async def test_find_by_label_fuzzy(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, label="dioxide", fuzzy=True)
        assert result.total == 2  # "Uranium Dioxide" + "Plutonium Dioxide"

    @pytest.mark.asyncio
    async def test_filter_by_node_type(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, node_type="Property")
        assert result.total == 2
        assert all(n.node_type == "Property" for n in result.nodes)

    @pytest.mark.asyncio
    async def test_invalid_node_type(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, node_type="InvalidType")
        assert result.nodes == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_deprecated_nodes_excluded(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, label="Deprecated")
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_pagination(self, db_session, seed_nodes) -> None:
        result = await property_query(db_session, limit=1, offset=0)
        assert len(result.nodes) <= 1

    @pytest.mark.asyncio
    async def test_pagination_offset(self, db_session, seed_nodes) -> None:
        first = await property_query(db_session, limit=1, offset=0)
        second = await property_query(db_session, limit=1, offset=1)
        if first.total > 1:
            assert first.nodes[0].id != second.nodes[0].id

    @pytest.mark.asyncio
    async def test_combined_filters(self, db_session, seed_nodes) -> None:
        result = await property_query(
            db_session, node_type="Material", fuzzy=True, label="dioxide",
        )
        assert result.total == 2
        for node in result.nodes:
            assert node.node_type == "Material"

    @pytest.mark.asyncio
    async def test_filter_by_property_key_value(self, db_session, seed_nodes) -> None:
        """L1: JSON property key+value filter must return matching nodes only."""
        result = await property_query(
            db_session,
            node_type="Material",
            property_key="formula",
            property_value="UO2",
        )
        assert result.total == 1
        assert result.nodes[0].label == "Uranium Dioxide"
        assert result.nodes[0].properties["formula"] == "UO2"

    @pytest.mark.asyncio
    async def test_filter_by_property_key_only(self, db_session, seed_nodes) -> None:
        """L1: JSON property key-presence filter (no value) returns nodes with the key."""
        result = await property_query(
            db_session,
            node_type="Property",
            property_key="unit",
        )
        # seed_nodes creates two Property nodes with "unit" set
        assert result.total == 2
        for node in result.nodes:
            assert "unit" in node.properties

    @pytest.mark.asyncio
    async def test_filter_by_property_key_value_no_match(self, db_session, seed_nodes) -> None:
        """L1: JSON property key+value filter with non-matching value yields empty."""
        result = await property_query(
            db_session,
            node_type="Material",
            property_key="formula",
            property_value="NonexistentMaterial",
        )
        assert result.total == 0
        assert result.nodes == []


# ---------------------------------------------------------------------------
# Relation Query
# ---------------------------------------------------------------------------


class TestRelationQuery:
    """Tests for relation_query service function."""

    @pytest.mark.asyncio
    async def test_empty_result(self, db_session, seed_nodes, seed_edges) -> None:
        result = await relation_query(
            db_session,
            source_node_id=uuid.uuid4(),
            direction="outgoing",
        )
        assert result.edges == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_outgoing_edges(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await relation_query(
            db_session,
            source_node_id=ids["uo2"],
            direction="outgoing",
        )
        assert result.total == 2  # hasProperty x2
        assert all(e.source_node_id == ids["uo2"] for e in result.edges)

    @pytest.mark.asyncio
    async def test_incoming_edges(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await relation_query(
            db_session,
            target_node_id=ids["uo2"],
            direction="incoming",
        )
        assert result.total == 1  # experiment → measuredIn → uo2

    @pytest.mark.asyncio
    async def test_filter_by_relation_type(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await relation_query(
            db_session,
            source_node_id=ids["uo2"],
            relation_type="hasProperty",
        )
        assert result.total == 2
        for edge in result.edges:
            assert edge.relation_type == "hasProperty"

    @pytest.mark.asyncio
    async def test_invalid_relation_type(self, db_session, seed_nodes, seed_edges) -> None:
        result = await relation_query(
            db_session,
            relation_type="invalidRelation",
        )
        assert result.edges == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_both_direction(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await relation_query(
            db_session,
            source_node_id=ids["uo2"],
            direction="both",
        )
        assert result.total >= 2  # outgoing + incoming

    @pytest.mark.asyncio
    async def test_nodes_included(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await relation_query(
            db_session,
            source_node_id=ids["uo2"],
            direction="outgoing",
        )
        node_ids_in_response = {n.id for n in result.nodes}
        assert ids["uo2"] in node_ids_in_response
        assert ids["density"] in node_ids_in_response


# ---------------------------------------------------------------------------
# Path Query
# ---------------------------------------------------------------------------


class TestBfsFindPaths:
    """Tests for _bfs_find_paths pure function."""

    def test_direct_path(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "relatedTo")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=b, max_depth=3, relation_types=None, limit=10,
        )
        assert len(paths) == 1
        assert paths[0] == [a, b]

    def test_two_hop_path(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "hasProperty")], b: [(c, "relatedTo")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=c, max_depth=3, relation_types=None, limit=10,
        )
        assert len(paths) == 1
        assert paths[0] == [a, b, c]

    def test_max_depth_respected(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "hasProperty")], b: [(c, "relatedTo")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=c, max_depth=1, relation_types=None, limit=10,
        )
        assert paths == []

    def test_no_path(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        adjacency = {a: []}
        paths = _bfs_find_paths(
            adjacency, source=a, target=b, max_depth=3, relation_types=None, limit=10,
        )
        assert paths == []

    def test_same_source_target(self) -> None:
        a = uuid.uuid4()
        adjacency: dict = {}
        paths = _bfs_find_paths(
            adjacency, source=a, target=a, max_depth=3, relation_types=None, limit=10,
        )
        assert paths == []

    def test_relation_type_filter(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "hasProperty"), (b, "cites")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=b, max_depth=3,
            relation_types=frozenset({"hasProperty"}), limit=10,
        )
        assert len(paths) == 1

    def test_limit_respected(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "r1"), (b, "r2")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=b, max_depth=3, relation_types=None, limit=1,
        )
        assert len(paths) == 1

    def test_no_cycles(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        adjacency = {a: [(b, "r1")], b: [(c, "r2")], c: [(a, "r3")]}
        paths = _bfs_find_paths(
            adjacency, source=a, target=a, max_depth=3, relation_types=None, limit=10,
        )
        assert paths == []

    def test_multiple_paths(self) -> None:
        a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        adjacency = {
            a: [(b, "r1"), (c, "r2")],
            b: [(d, "r3")],
            c: [(d, "r4")],
        }
        paths = _bfs_find_paths(
            adjacency, source=a, target=d, max_depth=3, relation_types=None, limit=10,
        )
        assert len(paths) == 2


class TestPathQuery:
    """Tests for path_query service function."""

    @pytest.mark.asyncio
    async def test_no_path_exists(self, db_session, seed_nodes, seed_edges) -> None:
        isolated = uuid.uuid4()
        db_session.add(KGNode(
            id=isolated,
            node_type="Material",
            label="Isolated Node",
            status="active",
        ))
        await db_session.flush()

        result = await path_query(
            db_session,
            source_node_id=seed_nodes["uo2"],
            target_node_id=isolated,
        )
        assert result.paths == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_direct_path(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await path_query(
            db_session,
            source_node_id=ids["uo2"],
            target_node_id=ids["density"],
            max_depth=1,
        )
        assert result.total == 1
        assert result.paths[0].length == 1

    @pytest.mark.asyncio
    async def test_two_hop_path(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await path_query(
            db_session,
            source_node_id=ids["experiment"],
            target_node_id=ids["density"],
            max_depth=2,
        )
        assert result.total >= 1
        path = result.paths[0]
        assert path.length == 2  # experiment -> uo2 -> density

    @pytest.mark.asyncio
    async def test_relation_type_filter(self, db_session, seed_nodes, seed_edges) -> None:
        ids = seed_nodes
        result = await path_query(
            db_session,
            source_node_id=ids["uo2"],
            target_node_id=ids["density"],
            max_depth=1,
            relation_types=["hasProperty"],
        )
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_nonexistent_source(self, db_session, seed_nodes, seed_edges) -> None:
        result = await path_query(
            db_session,
            source_node_id=uuid.uuid4(),
            target_node_id=seed_nodes["uo2"],
        )
        assert result.paths == []
