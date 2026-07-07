"""Tests for Knowledge Graph models (NFM-838 Batch 2 B2.7).

Unit tests for KGNode, KGEdge, KGReviewQueue ORM models and
Pydantic schemas. Run with: pytest apps/api/tests/test_kg_models.py -v
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.models.kg import KGEdge, KGNode, KGReviewQueue
from nfm_db.schemas.kg import (
    KGEdgeCreate,
    KGEdgeResponse,
    KGEdgeUpdate,
    KGNodeCreate,
    KGNodeResponse,
    KGNodeUpdate,
    KGReviewAction,
    KGReviewItemResponse,
    PropertyQueryResponse,
)


# ---------------------------------------------------------------------------
# KGNode model tests
# ---------------------------------------------------------------------------


class TestKGNodeModel:
    """Test KGNode ORM model construction and constraints."""

    def test_node_creation_minimal(self):
        node = KGNode(
            entity_type="material",
            name="UO2",
            confidence_score=0.95,
        )
        assert node.entity_type == "material"
        assert node.name == "UO2"
        assert node.confidence_score == 0.95
        assert node.id is None  # not yet persisted
        assert node.label is None

    def test_node_all_fields(self):
        node = KGNode(
            entity_type="property",
            name="thermal_conductivity",
            label="Thermal Conductivity",
            description="A measure of heat transfer",
            confidence_score=0.88,
            extraction_method="ner_auto",
        )
        assert node.entity_type == "property"
        assert node.extraction_method == "ner_auto"
        assert node.description == "A measure of heat transfer"

    def test_node_repr(self):
        import uuid

        node = KGNode(
            entity_type="material",
            name="UO2",
            confidence_score=0.95,
        )
        node.id = uuid.uuid4()
        assert "KGNode" in repr(node)
        assert "UO2" in repr(node)

    def test_node_valid_entity_types(self):
        """All spec-defined entity types should be accepted."""
        valid_types = [
            "material",
            "property",
            "value",
            "crystal_structure",
            "dataset",
            "publication",
            "author",
            "experiment",
            "composition",
            "defect_mechanism",
            "other",
        ]
        for etype in valid_types:
            node = KGNode(
                entity_type=etype,
                name=f"test_{etype}",
                confidence_score=0.5,
            )
            assert node.entity_type == etype


# ---------------------------------------------------------------------------
# KGEdge model tests
# ---------------------------------------------------------------------------


class TestKGEdgeModel:
    """Test KGEdge ORM model construction and constraints."""

    def test_edge_creation_minimal(self):
        edge = KGEdge(
            source_id="a" * 32,  # placeholder UUID
            target_id="b" * 32,
            relation_type="has_property",
            confidence_score=0.9,
        )
        assert edge.relation_type == "has_property"
        assert edge.confidence_score == 0.9

    def test_edge_all_fields(self):
        edge = KGEdge(
            source_id="a" * 32,
            target_id="b" * 32,
            relation_type="cited_by",
            label="Cited By",
            extraction_method="llm_extracted",
            confidence_score=0.75,
        )
        assert edge.label == "Cited By"
        assert edge.extraction_method == "llm_extracted"

    def test_edge_no_self_loop(self):
        """Self-loops should be prevented at DB level (CHECK constraint)."""
        same_id = "c" * 32
        edge = KGEdge(
            source_id=same_id,
            target_id=same_id,
            relation_type="related_to",
            confidence_score=0.5,
        )
        # ORM allows creation; DB enforces via CHECK constraint
        assert edge.source_id == edge.target_id


# ---------------------------------------------------------------------------
# KGReviewQueue model tests
# ---------------------------------------------------------------------------


class TestKGReviewQueueModel:
    """Test KGReviewQueue ORM model."""

    def test_review_queue_creation(self):
        item = KGReviewQueue(
            item_type="node",
            item_id="d" * 32,
            confidence_score=0.45,
            review_status="pending",
        )
        assert item.item_type == "node"
        assert item.confidence_score == 0.45
        assert item.review_status == "pending"

    def test_review_queue_all_statuses(self):
        valid_statuses = ["pending", "approved", "rejected", "skipped"]
        for status in valid_statuses:
            item = KGReviewQueue(
                item_type="edge",
                item_id="e" * 32,
                confidence_score=0.3,
                review_status=status,
            )
            assert item.review_status == status


# ---------------------------------------------------------------------------
# Pydantic schema tests
# ---------------------------------------------------------------------------


class TestKGSchemas:
    """Test KG Pydantic request/response schemas."""

    def test_node_create_valid(self):
        schema = KGNodeCreate(
            entity_type="material",
            name="UO2",
            confidence_score=0.95,
        )
        assert schema.entity_type == "material"

    def test_node_create_confidence_bounds(self):
        with pytest.raises(ValidationError):
            KGNodeCreate(
                entity_type="material",
                name="UO2",
                confidence_score=1.5,
            )
        with pytest.raises(ValidationError):
            KGNodeCreate(
                entity_type="material",
                name="UO2",
                confidence_score=-0.1,
            )

    def test_node_update_partial(self):
        schema = KGNodeUpdate(
            confidence_score=0.99,
        )
        assert schema.name is None
        assert schema.confidence_score == 0.99

    def test_edge_create_no_self_loop_check(self):
        """Schema doesn't enforce no-self-loop; that's a DB constraint."""
        same_id = "f" * 32
        schema = KGEdgeCreate(
            source_id=same_id,
            target_id=same_id,
            relation_type="related_to",
            confidence_score=0.5,
        )
        assert schema.source_id == schema.target_id

    def test_edge_create_valid(self):
        schema = KGEdgeCreate(
            source_id="a" * 32,
            target_id="b" * 32,
            relation_type="has_property",
            confidence_score=0.85,
            label="Has Property",
        )
        assert schema.label == "Has Property"

    def test_review_action_valid(self):
        for action in ("approve", "reject", "skip"):
            schema = KGReviewAction(action=action)
            assert schema.action == action

    def test_review_action_invalid(self):
        with pytest.raises(ValidationError):
            KGReviewAction(action="delete")

    def test_property_query_response(self):
        import uuid

        response = PropertyQueryResponse(
            material_id=uuid.uuid4(),
            property_type="thermal_conductivity",
            values=[],
            total=0,
        )
        assert response.values == []
        assert response.total == 0
