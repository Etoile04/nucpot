"""Tests for NucMat ontology alignment and review queue (NFM-859).

Covers:
- Ontology entity/relation type enums and counts
- Extraction-to-ontology mapping (entity and relation)
- build_node_properties per entity type
- Confidence threshold checks
- Review queue service: add, list, approve, reject
- Review queue API endpoints
- Auto-routing of low-confidence items
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.schemas.nucmat_ontology import (
    LOW_CONFIDENCE_THRESHOLD,
    EntityType,
    RelationType,
    build_node_properties,
    map_extraction_entity,
    map_extraction_relation,
)
from nfm_db.services.review_queue_service import (
    add_to_review_queue,
    approve_review_item,
    auto_route_to_review,
    list_pending_reviews,
    reject_review_item,
    should_route_to_review,
)

# ============================================================
# Ontology type count tests (acceptance criteria)
# ============================================================


class TestOntologyTypeCounts:
    """Verify ≥5 entity types and ≥10 relation types."""

    def test_entity_type_count_at_least_5(self) -> None:
        """EntityType enum has ≥5 members."""
        assert len(EntityType) >= 5

    def test_relation_type_count_at_least_10(self) -> None:
        """RelationType enum has ≥10 members."""
        assert len(RelationType) >= 10

    def test_specific_entity_types_exist(self) -> None:
        """Required entity types are present."""
        expected = {"Material", "Property", "Experiment", "Publication", "Measurement"}
        actual = {e.value for e in EntityType}
        assert expected.issubset(actual)

    def test_specific_relation_types_exist(self) -> None:
        """Required relation types from the spec are present."""
        expected = {
            "hasProperty",
            "measuredIn",
            "publishedIn",
            "containsData",
            "synthesizedBy",
            "alloyOf",
            "irradiatedIn",
            "testedAt",
            "references",
            "derivedFrom",
        }
        actual = {r.value for r in RelationType}
        assert expected.issubset(actual)


# ============================================================
# Extraction mapping tests
# ============================================================


class TestExtractionEntityMapping:
    """Tests for map_extraction_entity()."""

    def test_map_material(self) -> None:
        assert map_extraction_entity("material") == EntityType.MATERIAL

    def test_map_compound(self) -> None:
        assert map_extraction_entity("compound") == EntityType.MATERIAL

    def test_map_alloy(self) -> None:
        assert map_extraction_entity("alloy") == EntityType.MATERIAL

    def test_map_property(self) -> None:
        assert map_extraction_entity("property") == EntityType.PROPERTY

    def test_map_thermal_property(self) -> None:
        assert map_extraction_entity("thermal_property") == EntityType.PROPERTY

    def test_map_experiment(self) -> None:
        assert map_extraction_entity("experiment") == EntityType.EXPERIMENT

    def test_map_irradiation(self) -> None:
        assert map_extraction_entity("irradiation") == EntityType.EXPERIMENT

    def test_map_condition(self) -> None:
        assert map_extraction_entity("condition") == EntityType.CONDITION

    def test_map_publication(self) -> None:
        assert map_extraction_entity("publication") == EntityType.PUBLICATION

    def test_map_paper(self) -> None:
        assert map_extraction_entity("paper") == EntityType.PUBLICATION

    def test_map_measurement(self) -> None:
        assert map_extraction_entity("measurement") == EntityType.MEASUREMENT

    def test_map_data_point(self) -> None:
        assert map_extraction_entity("data_point") == EntityType.MEASUREMENT

    def test_map_unknown_returns_none(self) -> None:
        assert map_extraction_entity("unknown_category") is None

    def test_map_case_insensitive(self) -> None:
        assert map_extraction_entity("MATERIAL") == EntityType.MATERIAL
        assert map_extraction_entity("Material") == EntityType.MATERIAL


class TestExtractionRelationMapping:
    """Tests for map_extraction_relation()."""

    def test_map_has_property_snake(self) -> None:
        assert map_extraction_relation("has_property") == RelationType.HAS_PROPERTY

    def test_map_has_property_camel(self) -> None:
        assert map_extraction_relation("hasProperty") == RelationType.HAS_PROPERTY

    def test_map_measured_in(self) -> None:
        assert map_extraction_relation("measured_in") == RelationType.MEASURED_IN

    def test_map_published_in(self) -> None:
        assert map_extraction_relation("published_in") == RelationType.PUBLISHED_IN

    def test_map_contains_data(self) -> None:
        assert map_extraction_relation("contains_data") == RelationType.CONTAINS_DATA

    def test_map_synthesized_by(self) -> None:
        assert map_extraction_relation("synthesized_by") == RelationType.SYNTHESIZED_BY

    def test_map_alloy_of(self) -> None:
        assert map_extraction_relation("alloy_of") == RelationType.ALLOY_OF

    def test_map_irradiated_in(self) -> None:
        assert map_extraction_relation("irradiated_in") == RelationType.IRRADIATED_IN

    def test_map_tested_at(self) -> None:
        assert map_extraction_relation("tested_at") == RelationType.TESTED_AT

    def test_map_references(self) -> None:
        assert map_extraction_relation("references") == RelationType.REFERENCES

    def test_map_derived_from(self) -> None:
        assert map_extraction_relation("derived_from") == RelationType.DERIVED_FROM

    def test_map_unknown_returns_none(self) -> None:
        assert map_extraction_relation("unknown_relation") is None


# ============================================================
# build_node_properties tests
# ============================================================


class TestBuildNodeProperties:
    """Tests for build_node_properties()."""

    def test_material_properties_filters_correctly(self) -> None:
        raw = {
            "chemical_formula": "UO2",
            "crystal_structure": "fluorite",
            "irrelevant_field": "should_be_filtered",
        }
        result = build_node_properties(entity_type=EntityType.MATERIAL, raw_data=raw)
        assert result == {"chemical_formula": "UO2", "crystal_structure": "fluorite"}

    def test_property_properties_filters_correctly(self) -> None:
        raw = {"value": 10.5, "unit": "W/mK", "unrelated": True}
        result = build_node_properties(entity_type=EntityType.PROPERTY, raw_data=raw)
        assert result == {"value": 10.5, "unit": "W/mK"}

    def test_publication_properties_filters_correctly(self) -> None:
        raw = {"doi": "10.1016/j.nucengdes.2020.123", "authors": ["Smith"], "extra": False}
        result = build_node_properties(entity_type=EntityType.PUBLICATION, raw_data=raw)
        assert "doi" in result
        assert "authors" in result
        assert "extra" not in result

    def test_measurement_properties_filters_correctly(self) -> None:
        raw = {"value": 3.14, "unit": "GPa", "technique": "nanoindentation"}
        result = build_node_properties(entity_type=EntityType.MEASUREMENT, raw_data=raw)
        assert result == {"value": 3.14, "unit": "GPa", "technique": "nanoindentation"}

    def test_empty_raw_data(self) -> None:
        result = build_node_properties(entity_type=EntityType.MATERIAL, raw_data={})
        assert result == {}


# ============================================================
# Confidence threshold tests
# ============================================================


class TestConfidenceThresholds:
    """Tests for LOW_CONFIDENCE_THRESHOLD and should_route_to_review."""

    def test_low_confidence_threshold_value(self) -> None:
        assert LOW_CONFIDENCE_THRESHOLD == 0.6

    def test_should_route_low_confidence(self) -> None:
        assert should_route_to_review(0.5) is True

    def test_should_route_very_low_confidence(self) -> None:
        assert should_route_to_review(0.1) is True

    def test_should_not_route_high_confidence(self) -> None:
        assert should_route_to_review(0.7) is False

    def test_should_not_route_exact_threshold(self) -> None:
        assert should_route_to_review(0.6) is False

    def test_should_not_route_perfect_confidence(self) -> None:
        assert should_route_to_review(1.0) is False


# ============================================================
# Review queue service tests
# ============================================================


class TestAddToReviewQueue:
    """Tests for add_to_review_queue()."""

    @pytest.mark.asyncio
    async def test_add_entity_to_queue(self, db_session: AsyncSession) -> None:
        node = KGNode(node_type="Material", label="UO2", confidence=0.4)
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence (0.40)",
        )

        assert item.id is not None
        assert item.item_type == "entity"
        assert item.item_id == node.id
        assert item.status == "pending"
        assert "Low confidence" in item.review_reason

    @pytest.mark.asyncio
    async def test_add_relation_to_queue(self, db_session: AsyncSession) -> None:
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([source, target])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.3,
        )
        db_session.add(edge)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="relation",
            item_id=edge.id,
            review_reason="Low confidence relation",
        )

        assert item.item_type == "relation"
        assert item.item_id == edge.id


class TestListPendingReviews:
    """Tests for list_pending_reviews()."""

    @pytest.mark.asyncio
    async def test_empty_queue(self, db_session: AsyncSession) -> None:
        items, total = await list_pending_reviews(db_session)
        assert items == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_pending_items(self, db_session: AsyncSession) -> None:
        node1 = KGNode(node_type="Material", label="Mat-A", confidence=0.4)
        node2 = KGNode(node_type="Property", label="Prop-B", confidence=0.3)
        db_session.add_all([node1, node2])
        await db_session.flush()

        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node1.id,
            review_reason="Low confidence",
        )
        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node2.id,
            review_reason="Very low confidence",
        )
        await db_session.flush()

        items, total = await list_pending_reviews(db_session)
        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_filter_by_item_type(self, db_session: AsyncSession) -> None:
        node = KGNode(node_type="Material", label="Mat-A", confidence=0.4)
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([node, source, target])
        await db_session.flush()

        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence entity",
        )

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.3,
        )
        db_session.add(edge)
        await db_session.flush()
        await add_to_review_queue(
            db_session,
            item_type="relation",
            item_id=edge.id,
            review_reason="Low confidence relation",
        )

        entity_items, entity_total = await list_pending_reviews(
            db_session,
            item_type="entity",
        )
        assert entity_total == 1
        assert entity_items[0]["item_type"] == "entity"

    @pytest.mark.asyncio
    async def test_approved_items_not_listed(self, db_session: AsyncSession) -> None:
        node = KGNode(node_type="Material", label="Mat-A", confidence=0.4)
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )

        await approve_review_item(db_session, review_id=item.id)
        await db_session.flush()

        items, total = await list_pending_reviews(db_session)
        assert total == 0

    @pytest.mark.asyncio
    async def test_entity_data_attached(self, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            confidence=0.4,
            properties={"chemical_formula": "UO2"},
        )
        db_session.add(node)
        await db_session.flush()

        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )

        items, _ = await list_pending_reviews(db_session)
        assert len(items) == 1
        assert items[0]["entity_data"]["label"] == "UO2"
        assert items[0]["entity_data"]["node_type"] == "Material"


class TestApproveReviewItem:
    """Tests for approve_review_item()."""

    @pytest.mark.asyncio
    async def test_approve_entity_sets_active(self, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            status="pending_review",
            confidence=0.4,
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )

        result = await approve_review_item(
            db_session,
            review_id=item.id,
            reviewer_notes="Looks valid",
        )
        assert result["status"] == "approved"
        assert result["reviewer_notes"] == "Looks valid"

        await db_session.refresh(node)
        assert node.status == "active"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_404(self, db_session: AsyncSession) -> None:
        import uuid

        result = await approve_review_item(
            db_session,
            review_id=uuid.uuid4(),
        )
        assert result["error"] == "Review item not found"
        assert result["status_code"] == 404

    @pytest.mark.asyncio
    async def test_approve_already_approved_returns_409(
        self,
        db_session: AsyncSession,
    ) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            status="pending_review",
            confidence=0.4,
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )
        await approve_review_item(db_session, review_id=item.id)

        result = await approve_review_item(db_session, review_id=item.id)
        assert "already approved" in result["error"]
        assert result["status_code"] == 409


class TestRejectReviewItem:
    """Tests for reject_review_item()."""

    @pytest.mark.asyncio
    async def test_reject_entity_deprecates_node(self, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            status="pending_review",
            confidence=0.3,
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )

        result = await reject_review_item(
            db_session,
            review_id=item.id,
            reason="Invalid material",
        )
        assert result["status"] == "rejected"
        assert result["reviewer_notes"] == "Invalid material"

        await db_session.refresh(node)
        assert node.status == "deprecated"

    @pytest.mark.asyncio
    async def test_reject_relation_deletes_edge(self, db_session: AsyncSession) -> None:
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([source, target])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.2,
        )
        db_session.add(edge)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="relation",
            item_id=edge.id,
            review_reason="Low confidence relation",
        )

        await reject_review_item(
            db_session,
            review_id=item.id,
            reason="Incorrect relation",
        )

        deleted = await db_session.get(KGEdge, edge.id)
        assert deleted is None

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_404(self, db_session: AsyncSession) -> None:
        import uuid

        result = await reject_review_item(
            db_session,
            review_id=uuid.uuid4(),
            reason="No such item",
        )
        assert result["error"] == "Review item not found"
        assert result["status_code"] == 404

    @pytest.mark.asyncio
    async def test_reject_already_rejected_returns_409(
        self,
        db_session: AsyncSession,
    ) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            status="pending_review",
            confidence=0.3,
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )
        await reject_review_item(
            db_session,
            review_id=item.id,
            reason="Invalid",
        )

        result = await reject_review_item(
            db_session,
            review_id=item.id,
            reason="Still invalid",
        )
        assert "already rejected" in result["error"]
        assert result["status_code"] == 409


# ============================================================
# Auto-route tests
# ============================================================


class TestAutoRouteToReview:
    """Tests for auto_route_to_review()."""

    @pytest.mark.asyncio
    async def test_low_confidence_node_routed(self, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="Unknown",
            confidence=0.4,
        )
        db_session.add(node)
        await db_session.flush()

        result = await auto_route_to_review(db_session, node=node)
        assert result is not None
        assert result.item_type == "entity"
        assert result.status == "pending"

        await db_session.refresh(node)
        assert node.status == "pending_review"

    @pytest.mark.asyncio
    async def test_high_confidence_node_not_routed(self, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            confidence=0.9,
        )
        db_session.add(node)
        await db_session.flush()

        result = await auto_route_to_review(db_session, node=node)
        assert result is None

        await db_session.refresh(node)
        assert node.status == "active"

    @pytest.mark.asyncio
    async def test_low_confidence_edge_routed(self, db_session: AsyncSession) -> None:
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([source, target])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.3,
        )
        db_session.add(edge)
        await db_session.flush()

        result = await auto_route_to_review(db_session, edge=edge)
        assert result is not None
        assert result.item_type == "relation"

    @pytest.mark.asyncio
    async def test_high_confidence_edge_not_routed(self, db_session: AsyncSession) -> None:
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([source, target])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.95,
        )
        db_session.add(edge)
        await db_session.flush()

        result = await auto_route_to_review(db_session, edge=edge)
        assert result is None


# ============================================================
# Review queue API endpoint tests
# ============================================================


class TestReviewQueueAPI:
    """Integration tests for review queue API endpoints."""

    @pytest.mark.asyncio
    async def test_get_empty_queue(self, async_client) -> None:
        response = await async_client.get("/api/v1/kg/review/queue")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["total"] == 0
        assert data["data"]["items"] == []

    @pytest.mark.asyncio
    async def test_get_queue_with_items(self, async_client, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            confidence=0.4,
            status="pending_review",
        )
        db_session.add(node)
        await db_session.flush()

        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence (0.40)",
        )
        await db_session.commit()

        response = await async_client.get("/api/v1/kg/review/queue")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["item_type"] == "entity"

    @pytest.mark.asyncio
    async def test_approve_endpoint(self, async_client, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            confidence=0.4,
            status="pending_review",
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )
        await db_session.commit()

        response = await async_client.post(
            f"/api/v1/kg/review/{item.id}/approve",
            json={"reviewer_notes": "Valid material"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_endpoint(self, async_client, db_session: AsyncSession) -> None:
        node = KGNode(
            node_type="Material",
            label="UO2",
            confidence=0.3,
            status="pending_review",
        )
        db_session.add(node)
        await db_session.flush()

        item = await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence",
        )
        await db_session.commit()

        response = await async_client.post(
            f"/api/v1/kg/review/{item.id}/reject",
            json={"reason": "Invalid material name"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_returns_404(self, async_client) -> None:
        import uuid

        response = await async_client.post(
            f"/api/v1/kg/review/{uuid.uuid4()}/approve",
            json={},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_404(self, async_client) -> None:
        import uuid

        response = await async_client.post(
            f"/api/v1/kg/review/{uuid.uuid4()}/reject",
            json={"reason": "Not found"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_filter_queue_by_entity_type(
        self,
        async_client,
        db_session: AsyncSession,
    ) -> None:
        node = KGNode(
            node_type="Material",
            label="Mat-A",
            confidence=0.4,
            status="pending_review",
        )
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([node, source, target])
        await db_session.flush()

        await add_to_review_queue(
            db_session,
            item_type="entity",
            item_id=node.id,
            review_reason="Low confidence entity",
        )

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            confidence=0.3,
        )
        db_session.add(edge)
        await db_session.flush()
        await add_to_review_queue(
            db_session,
            item_type="relation",
            item_id=edge.id,
            review_reason="Low confidence relation",
        )
        await db_session.commit()

        response = await async_client.get(
            "/api/v1/kg/review/queue?item_type=entity",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["item_type"] == "entity"
