"""Tests for entity linking service (NFM-856).

TDD: These tests define the expected behavior of:
1. KG data models (kg_nodes, kg_review_queue, kg_provenance)
2. Entity linking service (dedup, matching, creation, review routing)
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# KG Model tests — RED phase: models do not exist yet
# ---------------------------------------------------------------------------


class TestKGNodeModel:
    """Tests for the kg_nodes SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_create_kg_node_minimal(self, db_session: AsyncSession) -> None:
        """A KGNode can be created with required fields only."""
        from nfm_db.models.kg_node import KGNode

        node = KGNode(
            name="Uranium Dioxide",
            canonical_name="UO2",
            node_type="material",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.id is not None
        assert node.name == "Uranium Dioxide"
        assert node.canonical_name == "UO2"
        assert node.node_type == "material"
        assert node.aliases == []
        assert node.confidence_score == 1.0
        assert node.properties == {}

    @pytest.mark.asyncio
    async def test_create_kg_node_with_properties(self, db_session: AsyncSession) -> None:
        """A KGNode can store material properties as JSON."""
        from nfm_db.models.kg_node import KGNode

        node = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
            properties={
                "chemical_formula": "UO2",
                "cas_number": "1344-57-6",
                "crystal_system": "FCC",
            },
            aliases=["uranium dioxide", "urania"],
            confidence_score=0.95,
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.properties["chemical_formula"] == "UO2"
        assert node.properties["cas_number"] == "1344-57-6"
        assert "uranium dioxide" in node.aliases
        assert node.confidence_score == 0.95

    @pytest.mark.asyncio
    async def test_kg_node_has_timestamps(self, db_session: AsyncSession) -> None:
        """KGNode inherits created_at and updated_at timestamps."""
        from nfm_db.models.kg_node import KGNode

        node = KGNode(
            name="Plutonium",
            canonical_name="Pu",
            node_type="element",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.created_at is not None
        assert node.updated_at is not None


class TestKGReviewQueueModel:
    """Tests for the kg_review_queue SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_create_review_queue_entry(self, db_session: AsyncSession) -> None:
        """A review queue entry stores low-confidence match candidates."""
        from nfm_db.models.kg_node import KGNode, KGReviewQueue

        node = KGNode(
            name="Extracted Material X",
            canonical_name="Material_X",
            node_type="material",
            confidence_score=0.5,
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        review_entry = KGReviewQueue(
            node_id=node.id,
            entity_name="Material X",
            match_candidate_id=None,
            confidence=0.5,
            status="pending",
            reason="Low confidence match: below 0.6 threshold",
        )
        db_session.add(review_entry)
        await db_session.commit()
        await db_session.refresh(review_entry)

        assert review_entry.id is not None
        assert review_entry.node_id == node.id
        assert review_entry.confidence == 0.5
        assert review_entry.status == "pending"

    @pytest.mark.asyncio
    async def test_review_queue_with_candidate(self, db_session: AsyncSession) -> None:
        """A review entry can reference a match candidate for human review."""
        from nfm_db.models.kg_node import KGNode, KGReviewQueue

        existing = KGNode(
            name="Known Material",
            canonical_name="Known_Material",
            node_type="material",
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        candidate = KGNode(
            name="Candidate Match",
            canonical_name="Candidate_Match",
            node_type="material",
            confidence_score=0.55,
        )
        db_session.add(candidate)
        await db_session.commit()
        await db_session.refresh(candidate)

        review_entry = KGReviewQueue(
            node_id=candidate.id,
            entity_name="Candidate Match",
            match_candidate_id=existing.id,
            confidence=0.55,
            status="pending",
            reason="Fuzzy name match below threshold",
        )
        db_session.add(review_entry)
        await db_session.commit()
        await db_session.refresh(review_entry)

        assert review_entry.match_candidate_id == existing.id


class TestKGProvenanceModel:
    """Tests for the kg_provenance SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_create_provenance_entry(self, db_session: AsyncSession) -> None:
        """Provenance tracks the source of each entity extraction."""
        from nfm_db.models.kg_node import KGNode, KGProvenance

        node = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        prov = KGProvenance(
            node_id=node.id,
            source_id="doi:10.1234/test",
            source_type="doi",
        )
        db_session.add(prov)
        await db_session.commit()
        await db_session.refresh(prov)

        assert prov.id is not None
        assert prov.node_id == node.id
        assert prov.source_id == "doi:10.1234/test"
        assert prov.source_type == "doi"

    @pytest.mark.asyncio
    async def test_multiple_provenance_per_node(self, db_session: AsyncSession) -> None:
        """A node can have multiple provenance entries from different sources."""
        from nfm_db.models.kg_node import KGNode, KGProvenance

        node = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        prov1 = KGProvenance(
            node_id=node.id,
            source_id="doi:10.1234/a",
            source_type="doi",
        )
        prov2 = KGProvenance(
            node_id=node.id,
            source_id="file:/data/paper2.pdf",
            source_type="file",
        )
        db_session.add(prov1)
        db_session.add(prov2)
        await db_session.commit()

        assert prov1.id is not None
        assert prov2.id is not None
        assert prov1.id != prov2.id


# ---------------------------------------------------------------------------
# Entity Linker service tests — RED phase: service does not exist yet
# ---------------------------------------------------------------------------


class TestEntityDeduplication:
    """Tests for entity deduplication within an extraction batch."""

    def test_deduplicate_identical_entities(self) -> None:
        """Identical entity names in a batch are merged, keeping highest confidence."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        entities = [
            {"name": "UO2", "confidence": 0.7},
            {"name": "UO2", "confidence": 0.9},
            {"name": "UO2", "confidence": 0.5},
        ]
        result = linker.deduplicate_entities(entities)

        assert len(result) == 1
        assert result[0]["name"] == "UO2"
        assert result[0]["confidence"] == 0.9

    def test_deduplicate_keeps_distinct_entities(self) -> None:
        """Distinct entity names are not merged."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        entities = [
            {"name": "UO2", "confidence": 0.9},
            {"name": "PuO2", "confidence": 0.8},
            {"name": "UN", "confidence": 0.7},
        ]
        result = linker.deduplicate_entities(entities)

        assert len(result) == 3

    def test_deduplicate_case_insensitive(self) -> None:
        """Entities differing only in case are treated as duplicates."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        entities = [
            {"name": "UO2", "confidence": 0.8},
            {"name": "uo2", "confidence": 0.9},
        ]
        result = linker.deduplicate_entities(entities)

        assert len(result) == 1
        assert result[0]["confidence"] == 0.9

    def test_deduplicate_merges_properties(self) -> None:
        """When merging duplicates, properties are combined (not mutated)."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        entities = [
            {
                "name": "UO2",
                "confidence": 0.8,
                "properties": {"cas_number": "1344-57-6"},
            },
            {
                "name": "UO2",
                "confidence": 0.9,
                "properties": {"crystal_system": "FCC"},
            },
        ]
        result = linker.deduplicate_entities(entities)

        assert len(result) == 1
        assert result[0]["properties"]["cas_number"] == "1344-57-6"
        assert result[0]["properties"]["crystal_system"] == "FCC"

    def test_deduplicate_preserves_all_sources(self) -> None:
        """All source_ids from duplicates are preserved in merged entity."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        entities = [
            {
                "name": "UO2",
                "confidence": 0.8,
                "source_id": "doi:10.1234/a",
            },
            {
                "name": "UO2",
                "confidence": 0.9,
                "source_id": "doi:10.5678/b",
            },
        ]
        result = linker.deduplicate_entities(entities)

        assert set(result[0]["source_ids"]) == {
            "doi:10.1234/a",
            "doi:10.5678/b",
        }


class TestEntityMatching:
    """Tests for matching extracted entities against existing KG nodes."""

    def test_exact_name_match(self) -> None:
        """Entity with exact name match returns high confidence."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {"id": 1, "name": "UO2", "canonical_name": "UO2", "aliases": []},
        ]
        match = linker.find_best_match(
            entity_name="UO2",
            kg_nodes=kg_nodes,
        )

        assert match is not None
        assert match["node_id"] == 1
        assert match["confidence"] >= 0.9

    def test_alias_match(self) -> None:
        """Entity matching a known alias returns high confidence."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {
                "id": 1,
                "name": "Uranium Dioxide",
                "canonical_name": "UO2",
                "aliases": ["UO2", "urania", "uranium dioxide"],
            },
        ]
        match = linker.find_best_match(
            entity_name="UO2",
            kg_nodes=kg_nodes,
        )

        assert match is not None
        assert match["node_id"] == 1
        assert match["confidence"] >= 0.8

    def test_fuzzy_name_match(self) -> None:
        """Entity with slight name variation returns moderate confidence."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {"id": 1, "name": "Uranium Dioxide", "canonical_name": "UO2", "aliases": []},
        ]
        match = linker.find_best_match(
            entity_name="uranium dioxide",
            kg_nodes=kg_nodes,
        )

        assert match is not None
        assert match["confidence"] >= 0.7

    def test_no_match_returns_none(self) -> None:
        """Entity with no similar match returns None."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {"id": 1, "name": "UO2", "canonical_name": "UO2", "aliases": []},
        ]
        match = linker.find_best_match(
            entity_name="Completely Different Material",
            kg_nodes=kg_nodes,
        )

        assert match is None

    def test_cas_number_property_match(self) -> None:
        """Matching by CAS number returns high confidence regardless of name."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {
                "id": 1,
                "name": "UO2",
                "canonical_name": "UO2",
                "aliases": [],
                "properties": {"cas_number": "1344-57-6"},
            },
        ]
        match = linker.find_best_match(
            entity_name="Unknown Material",
            kg_nodes=kg_nodes,
            entity_properties={"cas_number": "1344-57-6"},
        )

        assert match is not None
        assert match["node_id"] == 1
        assert match["confidence"] >= 0.9

    def test_formula_property_match(self) -> None:
        """Matching by chemical formula returns high confidence."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {
                "id": 1,
                "name": "UO2",
                "canonical_name": "UO2",
                "aliases": [],
                "properties": {"chemical_formula": "UO2"},
            },
        ]
        match = linker.find_best_match(
            entity_name="some material",
            kg_nodes=kg_nodes,
            entity_properties={"chemical_formula": "UO2"},
        )

        assert match is not None
        assert match["confidence"] >= 0.9

    def test_best_match_selected_amultiple_candidates(self) -> None:
        """When multiple candidates exist, the best match is selected."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker()
        kg_nodes = [
            {
                "id": 1,
                "name": "Uranium Dioxide",
                "canonical_name": "UO2",
                "aliases": [],
            },
            {
                "id": 2,
                "name": "UO2",
                "canonical_name": "UO2",
                "aliases": ["urania"],
            },
        ]
        match = linker.find_best_match(
            entity_name="UO2",
            kg_nodes=kg_nodes,
        )

        assert match is not None
        # Should match the exact name match (id=2) over fuzzy (id=1)
        assert match["confidence"] >= 0.9


class TestLowConfidenceRouting:
    """Tests for routing low-confidence matches to review queue."""

    @pytest.mark.asyncio
    async def test_low_confidence_creates_review_entry(
        self, db_session: AsyncSession,
    ) -> None:
        """Fuzzy matches below review threshold create review queue entries."""
        from nfm_db.models.kg_node import KGNode
        from nfm_db.services.entity_linker import EntityLinker

        # Use high review threshold so a fuzzy match (0.67) triggers review
        linker = EntityLinker(db_session, review_threshold=0.9)
        existing = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
            aliases=[],
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        # "U02" fuzzy-matches "UO2" with ~0.67 confidence, below 0.9 threshold
        entity = {
            "name": "U02",
            "confidence": 0.55,
            "source_id": "doi:10.1234/test",
        }

        result = await linker.route_entity(entity, kg_node_dicts=[{
            "id": existing.id,
            "name": existing.name,
            "canonical_name": existing.canonical_name,
            "aliases": [],
        }])

        assert result["action"] == "review"
        assert result["confidence"] < 0.9

    @pytest.mark.asyncio
    async def test_high_confidence_matches_skip_review(
        self, db_session: AsyncSession,
    ) -> None:
        """Matches at or above 0.6 confidence do not create review entries."""
        from nfm_db.models.kg_node import KGNode
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker(db_session)
        existing = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
        )
        db_session.add(existing)
        await db_session.commit()
        await db_session.refresh(existing)

        entity = {
            "name": "UO2",
            "confidence": 0.9,
            "source_id": "doi:10.1234/test",
        }

        result = await linker.route_entity(entity, kg_node_dicts=[{
            "id": existing.id,
            "name": existing.name,
            "canonical_name": existing.canonical_name,
            "aliases": ["urania"],
        }])

        assert result["action"] == "match"
        assert result["confidence"] >= 0.8


class TestNodeCreation:
    """Tests for creating new KG nodes for unmatched entities."""

    @pytest.mark.asyncio
    async def test_create_new_node_for_unmatched(self, db_session: AsyncSession) -> None:
        """Unmatched entities create new kg_nodes."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker(db_session)
        entity = {
            "name": "Novel Material Z",
            "confidence": 0.85,
            "source_id": "doi:10.9999/novel",
            "properties": {"chemical_formula": "NMZ"},
        }

        result = await linker.create_unmatched_node(entity)

        assert result["action"] == "created"
        assert result["node_id"] is not None
        assert result["name"] == "Novel Material Z"

    @pytest.mark.asyncio
    async def test_provenance_created_with_node(
        self, db_session: AsyncSession,
    ) -> None:
        """Creating a node also creates a provenance entry."""
        from nfm_db.services.entity_linker import EntityLinker

        linker = EntityLinker(db_session)
        entity = {
            "name": "Novel Material Z",
            "confidence": 0.85,
            "source_id": "doi:10.9999/novel",
            "source_type": "doi",
        }

        result = await linker.create_unmatched_node(entity)

        assert result["provenance_id"] is not None


class TestLinkEntitiesOrchestration:
    """Tests for the full link_entities orchestration."""

    @pytest.mark.asyncio
    async def test_link_entities_full_cycle(self, db_session: AsyncSession) -> None:
        """Full cycle: dedup, match, create, route."""
        from nfm_db.models.kg_node import KGNode
        from nfm_db.services.entity_linker import EntityLinker

        # Pre-existing KG nodes
        existing_uo2 = KGNode(
            name="UO2",
            canonical_name="UO2",
            node_type="material",
        )
        db_session.add(existing_uo2)
        await db_session.commit()
        await db_session.refresh(existing_uo2)

        linker = EntityLinker(db_session)

        extracted = [
            {"name": "UO2", "confidence": 0.9, "source_id": "doi:10.1/a"},
            {"name": "UO2", "confidence": 0.7, "source_id": "doi:10.2/b"},  # dup
            {"name": "Novel Material", "confidence": 0.85, "source_id": "doi:10.3/c"},
        ]

        result = await linker.link_entities(extracted)

        assert result["total_input"] == 3
        assert result["deduplicated_count"] == 2  # UO2 dups merged
        assert result["matched_count"] >= 1  # UO2 matched
        assert result["created_count"] >= 1  # Novel Material created
