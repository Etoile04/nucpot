"""Unit tests for ontology_service (T3: RED phase).

Tests for:
- get_nvl_data (class filter, search term, max_nodes)
- get_viz_stats (class distribution)
- derive_ontology_graph (full graph derivation, cursor pagination, chunking, 50K ceiling)
- _encode_cursor / _decode_cursor (opaque cursor roundtrip, edge cases)
- _material_ego_components (ego subgraph computation)
- _chunk_by_material (greedy packing, hard ceiling)
- build_record_ref (URL-safe deep link generation)
- CorpusNotFoundError
- _compute_source_digest (deterministic hash)
- _node_id / _relationship_id (ID generation helpers)

Existing integration tests (test_ontology_derivation.py) cover derive_ontology_graph
with real DB queries. These unit tests cover pure-function helpers and edge cases.
"""

from __future__ import annotations

import base64
import json
import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.ontology import OntologyNode, OntologyRelationship
from nfm_db.services.ontology_service import (
    SAMPLE_NODES,
    SAMPLE_RELATIONSHIPS,
    CorpusNotFoundError,
    _chunk_by_material,
    _compute_source_digest,
    _decode_cursor,
    _encode_cursor,
    _material_ego_components,
    _node_id,
    _relationship_id,
    build_record_ref,
    derive_ontology_graph,
    get_nvl_data,
    get_viz_stats,
)

# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestNodeId:
    """Tests for _node_id helper."""

    def test_material_node(self) -> None:
        assert _node_id("mat", "UO2") == "mat:UO2"

    def test_property_node(self) -> None:
        assert _node_id("prop", "lattice_constant") == "prop:lattice_constant"

    def test_source_node(self) -> None:
        assert _node_id("src", "corpus123") == "src:corpus123"


class TestRelationshipId:
    """Tests for _relationship_id helper."""

    def test_standard_relationship(self) -> None:
        rid = _relationship_id("mat:UO2", "HAS_PROPERTY", "prop:density")
        assert rid == "mat:UO2|HAS_PROPERTY|prop:density"

    def test_cited_in_relationship(self) -> None:
        rid = _relationship_id("method:DFT", "CITED_IN", "src:corpus")
        assert rid == "method:DFT|CITED_IN|src:corpus"


# ---------------------------------------------------------------------------
# Cursor encoding / decoding tests
# ---------------------------------------------------------------------------


class TestCursorEncoding:
    """Tests for opaque cursor encode/decode roundtrip."""

    def test_encode_decode_roundtrip(self) -> None:
        for offset in [0, 1, 10, 100, 9999]:
            encoded = _encode_cursor(offset)
            decoded = _decode_cursor(encoded)
            assert decoded == offset, f"offset={offset}, encoded={encoded!r}"

    def test_decode_none_returns_zero(self) -> None:
        assert _decode_cursor(None) == 0

    def test_decode_empty_string_returns_zero(self) -> None:
        assert _decode_cursor("") == 0

    def test_decode_malformed_returns_zero(self) -> None:
        assert _decode_cursor("not-base64!!!") == 0

    def test_decode_negative_offset_clamped_to_zero(self) -> None:
        payload = base64.urlsafe_b64encode(json.dumps({"o": -5}).encode()).decode()
        assert _decode_cursor(payload) == 0

    def test_encoded_is_base64url(self) -> None:
        encoded = _encode_cursor(42)
        # Should not have padding
        assert "=" not in encoded
        # Should decode cleanly
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode())
        data = json.loads(decoded)
        assert data["o"] == 42


# ---------------------------------------------------------------------------
# build_record_ref tests
# ---------------------------------------------------------------------------


class TestBuildRecordRef:
    """Tests for the origin-relative deep link builder."""

    def test_basic_ref(self) -> None:
        ref = build_record_ref("corpus1", "UO2")
        assert ref.startswith("/materials/")
        assert "corpus=corpus1" in ref

    def test_ref_with_property(self) -> None:
        ref = build_record_ref("corpus1", "UO2", property_name="density")
        assert "property=density" in ref
        assert "corpus=corpus1" in ref

    def test_ref_encodes_special_chars(self) -> None:
        ref = build_record_ref("corpus with spaces", "UO2+O")
        assert "corpus" in ref
        # Spaces should be encoded
        assert "corpus+with+spaces" in ref or "corpus%20with%20spaces" in ref

    def test_ref_is_relative(self) -> None:
        ref = build_record_ref("c", "m")
        assert ref.startswith("/")
        assert "://" not in ref


# ---------------------------------------------------------------------------
# _compute_source_digest tests
# ---------------------------------------------------------------------------


class TestComputeSourceDigest:
    """Tests for deterministic graph digest."""

    def test_digest_is_hex_string(self) -> None:
        nodes = [OntologyNode(id="n1", type="class", name="A", label="A")]
        rels = [OntologyRelationship(id="r1", from_="n1", to="n2", type="HAS")]
        digest = _compute_source_digest(nodes, rels)
        assert re.match(r"^[a-f0-9]{16}$", digest)

    def test_digest_is_deterministic(self) -> None:
        nodes = [OntologyNode(id="n1", type="class", name="A", label="A")]
        rels = [OntologyRelationship(id="r1", from_="n1", to="n2", type="HAS")]
        d1 = _compute_source_digest(nodes, rels)
        d2 = _compute_source_digest(list(nodes), list(rels))
        assert d1 == d2

    def test_digest_differs_for_different_graphs(self) -> None:
        nodes_a = [OntologyNode(id="n1", type="class", name="A", label="A")]
        nodes_b = [OntologyNode(id="n2", type="class", name="B", label="B")]
        rels = []
        da = _compute_source_digest(nodes_a, rels)
        db_ = _compute_source_digest(nodes_b, rels)
        assert da != db_

    def test_digest_order_independent(self) -> None:
        nodes = [
            OntologyNode(id="n2", type="class", name="B", label="B"),
            OntologyNode(id="n1", type="class", name="A", label="A"),
        ]
        rels = [
            OntologyRelationship(id="r2", from_="n2", to="n3", type="HAS"),
            OntologyRelationship(id="r1", from_="n1", to="n3", type="HAS"),
        ]
        d1 = _compute_source_digest(nodes, rels)
        d2 = _compute_source_digest(list(reversed(nodes)), list(reversed(rels)))
        assert d1 == d2


# ---------------------------------------------------------------------------
# get_nvl_data tests
# ---------------------------------------------------------------------------


class TestGetNvlData:
    """Tests for legacy NVL data endpoint."""

    @pytest.mark.asyncio
    async def test_returns_all_nodes_without_filters(self) -> None:
        response = await get_nvl_data()
        assert len(response.nodes) == len(SAMPLE_NODES)

    @pytest.mark.asyncio
    async def test_class_filter(self) -> None:
        response = await get_nvl_data(class_filter="Metal")
        assert all("Metal" in node.classes for node in response.nodes)

    @pytest.mark.asyncio
    async def test_class_filter_excludes_non_matching(self) -> None:
        response = await get_nvl_data(class_filter="Property")
        assert len(response.nodes) == 1
        assert response.nodes[0].name == "Density"

    @pytest.mark.asyncio
    async def test_search_term(self) -> None:
        response = await get_nvl_data(search_term="uranium")
        assert len(response.nodes) > 0
        assert all("uranium" in node.name.lower() for node in response.nodes)

    @pytest.mark.asyncio
    async def test_search_term_case_insensitive(self) -> None:
        response = await get_nvl_data(search_term="URANIUM")
        assert len(response.nodes) > 0

    @pytest.mark.asyncio
    async def test_max_nodes(self) -> None:
        response = await get_nvl_data(max_nodes=2)
        assert len(response.nodes) == 2

    @pytest.mark.asyncio
    async def test_max_nodes_larger_than_total(self) -> None:
        response = await get_nvl_data(max_nodes=1000)
        assert len(response.nodes) == len(SAMPLE_NODES)

    @pytest.mark.asyncio
    async def test_relationships_filtered(self) -> None:
        response = await get_nvl_data(class_filter="Property")
        # Only density node, no relationships should reference removed nodes
        node_ids = {n.id for n in response.nodes}
        for rel in response.relationships:
            assert rel.source in node_ids
            assert rel.target in node_ids

    @pytest.mark.asyncio
    async def test_no_filter_returns_relationships(self) -> None:
        response = await get_nvl_data()
        assert len(response.relationships) == len(SAMPLE_RELATIONSHIPS)

    @pytest.mark.asyncio
    async def test_empty_filter_string_returns_all(self) -> None:
        response = await get_nvl_data(class_filter="")
        assert len(response.nodes) == len(SAMPLE_NODES)

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self) -> None:
        response = await get_nvl_data(search_term="nonexistent_xyz")
        assert len(response.nodes) == 0
        assert len(response.relationships) == 0


# ---------------------------------------------------------------------------
# get_viz_stats tests
# ---------------------------------------------------------------------------


class TestGetVizStats:
    """Tests for ontology statistics."""

    @pytest.mark.asyncio
    async def test_returns_correct_totals(self) -> None:
        stats = await get_viz_stats()
        assert stats.total_nodes == len(SAMPLE_NODES)
        assert stats.total_relationships == len(SAMPLE_RELATIONSHIPS)

    @pytest.mark.asyncio
    async def test_class_distribution(self) -> None:
        stats = await get_viz_stats()
        assert stats.class_counts["Metal"] == 2
        assert stats.class_counts["Actinide"] == 2
        assert stats.class_counts["Compound"] == 1
        assert stats.class_counts["Property"] == 1


# ---------------------------------------------------------------------------
# _material_ego_components tests
# ---------------------------------------------------------------------------


class TestMaterialEgoComponents:
    """Tests for ego subgraph computation."""

    def _make_graph(self) -> tuple[dict[str, OntologyNode], list[OntologyRelationship]]:
        nodes = {
            "mat:UO2": OntologyNode(id="mat:UO2", type="individual", name="UO2", label="UO2"),
            "mat:U": OntologyNode(id="mat:U", type="individual", name="U", label="U"),
            "prop:density": OntologyNode(
                id="prop:density", type="class", name="density", label="density"
            ),
            "prop:lattice": OntologyNode(
                id="prop:lattice", type="class", name="lattice", label="lattice"
            ),
            "method:DFT": OntologyNode(id="method:DFT", type="class", name="DFT", label="DFT"),
            "src:corpus1": OntologyNode(
                id="src:corpus1", type="class", name="corpus1", label="corpus1"
            ),
        }
        relationships = [
            OntologyRelationship(id="r1", from_="mat:UO2", to="prop:density", type="HAS_PROPERTY"),
            OntologyRelationship(id="r2", from_="mat:UO2", to="prop:lattice", type="HAS_PROPERTY"),
            OntologyRelationship(
                id="r3", from_="prop:density", to="method:DFT", type="MEASURED_BY"
            ),
            OntologyRelationship(id="r4", from_="method:DFT", to="src:corpus1", type="CITED_IN"),
            OntologyRelationship(id="r5", from_="mat:U", to="prop:lattice", type="HAS_PROPERTY"),
        ]
        return nodes, relationships

    def test_material_includes_own_properties(self) -> None:
        nodes, rels = self._make_graph()
        ego = _material_ego_components(nodes, rels, "src:corpus1")
        uo2_ego = ego["mat:UO2"]
        assert "prop:density" in uo2_ego
        assert "prop:lattice" in uo2_ego

    def test_material_includes_source_node(self) -> None:
        nodes, rels = self._make_graph()
        ego = _material_ego_components(nodes, rels, "src:corpus1")
        for _mat_id, component in ego.items():
            assert "src:corpus1" in component

    def test_material_includes_hops(self) -> None:
        """Ego includes neighbors of properties (method)."""
        nodes, rels = self._make_graph()
        ego = _material_ego_components(nodes, rels, "src:corpus1")
        uo2_ego = ego["mat:UO2"]
        assert "method:DFT" in uo2_ego

    def test_only_material_nodes_have_egos(self) -> None:
        nodes, rels = self._make_graph()
        ego = _material_ego_components(nodes, rels, "src:corpus1")
        # Only mat: prefix nodes should have entries
        for key in ego:
            assert key.startswith("mat:")

    def test_shared_property_in_both_egos(self) -> None:
        """lattice is shared between UO2 and U — both egos include it."""
        nodes, rels = self._make_graph()
        ego = _material_ego_components(nodes, rels, "src:corpus1")
        assert "prop:lattice" in ego["mat:UO2"]
        assert "prop:lattice" in ego["mat:U"]


# ---------------------------------------------------------------------------
# _chunk_by_material tests
# ---------------------------------------------------------------------------


class TestChunkByMaterial:
    """Tests for greedy material packing into pages."""

    def test_single_material_fits(self) -> None:
        nodes = {
            "mat:A": OntologyNode(id="mat:A", type="individual", name="A", label="A"),
            "prop:X": OntologyNode(id="prop:X", type="class", name="X", label="X"),
            "src:S": OntologyNode(id="src:S", type="class", name="S", label="S"),
        }
        rels = [
            OntologyRelationship(id="r1", from_="mat:A", to="prop:X", type="HAS"),
        ]
        ego = {"mat:A": {"mat:A", "prop:X", "src:S"}}

        page_nodes, page_rels, next_offset = _chunk_by_material(
            nodes, rels, ego, max_nodes=10, offset=0
        )
        assert len(page_nodes) == 3
        assert next_offset is None

    def test_two_materials_split_across_pages(self) -> None:
        nodes = {
            "mat:A": OntologyNode(id="mat:A", type="individual", name="A", label="A"),
            "mat:B": OntologyNode(id="mat:B", type="individual", name="B", label="B"),
            "prop:X": OntologyNode(id="prop:X", type="class", name="X", label="X"),
            "src:S": OntologyNode(id="src:S", type="class", name="S", label="S"),
        }
        rels = [
            OntologyRelationship(id="r1", from_="mat:A", to="prop:X", type="HAS"),
            OntologyRelationship(id="r2", from_="mat:B", to="prop:X", type="HAS"),
        ]
        ego = {
            "mat:A": {"mat:A", "prop:X", "src:S"},
            "mat:B": {"mat:B", "prop:X", "src:S"},
        }
        # max_nodes=3 means only one material fits per page (ego size=3)
        page_nodes, _, next_offset = _chunk_by_material(nodes, rels, ego, max_nodes=3, offset=0)
        assert len(page_nodes) == 3
        assert next_offset == 1  # Next page starts at material B

    def test_hard_ceiling_enforced(self) -> None:
        """When a single ego exceeds max_nodes, it is truncated."""
        nodes = {"mat:A": OntologyNode(id="mat:A", type="individual", name="A", label="A")}
        for i in range(20):
            nodes[f"prop:P{i}"] = OntologyNode(
                id=f"prop:P{i}", type="class", name=f"P{i}", label=f"P{i}"
            )
        rels = [
            OntologyRelationship(id=f"r{i}", from_="mat:A", to=f"prop:P{i}", type="HAS")
            for i in range(20)
        ]
        ego = {"mat:A": set(nodes.keys()) | {"src:S"}}
        page_nodes, _, _ = _chunk_by_material(nodes, rels, ego, max_nodes=5, offset=0)
        assert len(page_nodes) <= 5

    def test_offset_skips_materials(self) -> None:
        nodes = {
            "mat:A": OntologyNode(id="mat:A", type="individual", name="A", label="A"),
            "mat:B": OntologyNode(id="mat:B", type="individual", name="B", label="B"),
            "mat:C": OntologyNode(id="mat:C", type="individual", name="C", label="C"),
            "src:S": OntologyNode(id="src:S", type="class", name="S", label="S"),
        }
        rels = []
        ego = {m: {m, "src:S"} for m in nodes if m.startswith("mat:")}

        page_nodes, _, next_offset = _chunk_by_material(nodes, rels, ego, max_nodes=2, offset=1)
        ids = {n.id for n in page_nodes}
        assert "mat:A" not in ids  # Skipped
        assert next_offset == 2

    def test_page_relationships_fully_inside(self) -> None:
        nodes = {
            "mat:A": OntologyNode(id="mat:A", type="individual", name="A", label="A"),
            "prop:X": OntologyNode(id="prop:X", type="class", name="X", label="X"),
            "src:S": OntologyNode(id="src:S", type="class", name="S", label="S"),
        }
        rels = [
            OntologyRelationship(id="r1", from_="mat:A", to="prop:X", type="HAS"),
        ]
        ego = {"mat:A": {"mat:A", "prop:X", "src:S"}}

        _, page_rels, _ = _chunk_by_material(nodes, rels, ego, max_nodes=10, offset=0)
        assert len(page_rels) == 1
        assert page_rels[0].id == "r1"


# ---------------------------------------------------------------------------
# CorpusNotFoundError tests
# ---------------------------------------------------------------------------


class TestCorpusNotFoundError:
    """Tests for the corpus-not-found exception."""

    def test_message_format(self) -> None:
        err = CorpusNotFoundError("my-corpus")
        assert str(err) == "corpus not found: 'my-corpus'"
        assert err.corpus_id == "my-corpus"

    def test_is_lookup_error(self) -> None:
        assert issubclass(CorpusNotFoundError, LookupError)


# ---------------------------------------------------------------------------
# derive_ontology_graph tests (using DB)
# ---------------------------------------------------------------------------


class TestDeriveOntologyGraph:
    """Tests that complement test_ontology_derivation.py with edge cases."""

    @pytest.mark.asyncio
    async def test_raises_for_empty_corpus(self, db_session: AsyncSession) -> None:
        with pytest.raises(CorpusNotFoundError):
            await derive_ontology_graph(db_session, "nonexistent_corpus_xyz")

    @pytest.mark.asyncio
    async def test_graph_has_schema_version(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-sv",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-sv")
        assert graph.schema_version is not None

    @pytest.mark.asyncio
    async def test_graph_has_corpus_id(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-cid",
            rows=[
                {
                    "element_system": "U",
                    "property_name": "mass",
                    "value": 238.0,
                    "unit": "amu",
                    "method": None,
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-cid")
        assert graph.corpus_id == "test-cid"

    @pytest.mark.asyncio
    async def test_pagination_none_when_within_limit(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-nopag",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-nopag", max_nodes=100)
        assert graph.pagination is None

    @pytest.mark.asyncio
    async def test_material_nodes_are_individuals(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-types",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-types")
        material_nodes = [n for n in graph.nodes if n.id.startswith("mat:")]
        for node in material_nodes:
            assert node.type == "individual"

    @pytest.mark.asyncio
    async def test_property_nodes_are_classes(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-ptype",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-ptype")
        prop_nodes = [n for n in graph.nodes if n.id.startswith("prop:")]
        for node in prop_nodes:
            assert node.type == "class"

    @pytest.mark.asyncio
    async def test_record_ref_on_material_nodes(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-rref",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-rref")
        mat_node = next(n for n in graph.nodes if n.id == "mat:UO2")
        assert mat_node.record_ref is not None
        assert "/materials/" in mat_node.record_ref

    @pytest.mark.asyncio
    async def test_no_method_cites_source_directly(self, db_session: AsyncSession) -> None:
        """When method is None, property cites source directly."""
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-nomethod",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": None,
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-nomethod")
        # Should have CITED_IN from property to source
        cites = [r for r in graph.relationships if r.type == "CITED_IN"]
        assert len(cites) == 1
        assert cites[0].from_.startswith("prop:")

    @pytest.mark.asyncio
    async def test_stats_count_matches(self, db_session: AsyncSession) -> None:
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-stats",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                },
                {
                    "element_system": "UO2",
                    "property_name": "lattice",
                    "value": 5.47,
                    "unit": "angstrom",
                    "method": "EXP",
                },
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-stats")
        assert graph.stats.nodes == len(graph.nodes)
        assert graph.stats.relationships == len(graph.relationships)

    @pytest.mark.asyncio
    async def test_max_nodes_caps_at_hard_max(self, db_session: AsyncSession) -> None:
        """max_nodes > HARD_MAX_NODES is clamped."""
        from tests.ontology_seed import seed_corpus

        await seed_corpus(
            db_session,
            source="test-cap",
            rows=[
                {
                    "element_system": "UO2",
                    "property_name": "density",
                    "value": 10.0,
                    "unit": "g/cm3",
                    "method": "DFT",
                }
            ],
        )

        graph = await derive_ontology_graph(db_session, "test-cap", max_nodes=100_000)
        # Should not crash, and pagination should be None (graph fits in hard max)
        assert graph.pagination is None
