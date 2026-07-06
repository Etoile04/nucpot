"""Tests for Knowledge Graph models (kg_nodes, kg_edges).

Covers:
- KGNode creation with all field types
- KGEdge creation with FK constraints
- FK constraint enforcement (source_id, source_node_id, target_node_id)
- Unique constraint on edges (source_node_id, target_node_id, relation_type)
- JSONB properties storage/retrieval
- Relationship: KGNode -> outgoing/incoming KGEdges
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode


async def _refresh_rel(session: AsyncSession, obj: object, *attrs: str) -> None:
    await session.refresh(obj, list(attrs))


# ============================================================
# KGNode Model Creation Tests
# ============================================================


class TestKGNodeCreation:
    """KGNode model creation tests."""

    @pytest.mark.asyncio
    async def test_create_kg_node_with_defaults(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode can be created with required fields; defaults applied."""
        node = KGNode(
            node_type="Material",
            label="Uranium Dioxide",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.id is not None
        assert node.node_type == "Material"
        assert node.label == "Uranium Dioxide"
        assert node.aliases is None  # not set
        assert node.properties == {}  # default
        assert node.confidence == 1.0  # default
        assert node.source_id is None  # nullable
        assert node.status == "active"  # default
        assert node.created_at is not None
        assert node.updated_at is not None

    @pytest.mark.asyncio
    async def test_create_kg_node_property(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode node_type=Property accepted."""
        node = KGNode(node_type="Property", label="Thermal Conductivity")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.node_type == "Property"

    @pytest.mark.asyncio
    async def test_create_kg_node_experiment(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode node_type=Experiment accepted."""
        node = KGNode(node_type="Experiment", label="Irradiation Test #42")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.node_type == "Experiment"

    @pytest.mark.asyncio
    async def test_create_kg_node_condition(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode node_type=Condition accepted."""
        node = KGNode(node_type="Condition", label="1200K, inert atmosphere")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.node_type == "Condition"

    @pytest.mark.asyncio
    async def test_create_kg_node_publication(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode node_type=Publication accepted."""
        node = KGNode(node_type="Publication", label="Finkelstein 2001")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.node_type == "Publication"

    @pytest.mark.asyncio
    async def test_create_kg_node_with_jsonb_properties(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode stores JSONB properties correctly."""
        props = {
            "chemical_formula": "UO2",
            "crystal_structure": "fluorite",
            "molecular_weight": 270.03,
        }
        node = KGNode(
            node_type="Material",
            label="UO2",
            properties=props,
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.properties["chemical_formula"] == "UO2"
        assert node.properties["crystal_structure"] == "fluorite"
        assert node.properties["molecular_weight"] == 270.03

    @pytest.mark.asyncio
    async def test_create_kg_node_with_confidence(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode stores non-default confidence."""
        node = KGNode(
            node_type="Material",
            label="Unknown Compound",
            confidence=0.65,
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.confidence == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_create_kg_node_with_status(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode stores non-default status."""
        node = KGNode(
            node_type="Material",
            label="Pending Node",
            status="pending_review",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.status == "pending_review"

    @pytest.mark.asyncio
    async def test_create_kg_node_with_source_id(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode stores source_id FK to data_sources."""
        from nfm_db.models import DataSource

        source = DataSource(title="Test Paper", source_type="journal_article")
        db_session.add(source)
        await db_session.flush()

        node = KGNode(
            node_type="Publication",
            label="Paper Node",
            source_id=source.id,
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.source_id == source.id


# ============================================================
# KGEdge Model Creation Tests
# ============================================================


class TestKGEdgeCreation:
    """KGEdge model creation tests."""

    @pytest.mark.asyncio
    async def test_create_kg_edge_with_defaults(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge can be created with required fields; defaults applied."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Thermal Conductivity")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.id is not None
        assert edge.source_node_id == source_node.id
        assert edge.target_node_id == target_node.id
        assert edge.relation_type == "hasProperty"
        assert edge.properties == {}  # default
        assert edge.confidence == 1.0  # default
        assert edge.source_id is None  # nullable
        assert edge.created_at is not None

    @pytest.mark.asyncio
    async def test_create_kg_edge_with_jsonb_properties(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge stores JSONB properties (e.g., value + unit)."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Melting Point")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge_props = {"value": "3135", "unit": "K"}
        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
            properties=edge_props,
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.properties["value"] == "3135"
        assert edge.properties["unit"] == "K"

    @pytest.mark.asyncio
    async def test_create_kg_edge_measured_in(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge with relation_type=measuredIn accepted."""
        source_node = KGNode(node_type="Experiment", label="Exp-001")
        target_node = KGNode(node_type="Property", label="Thermal Conductivity")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="measuredIn",
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.relation_type == "measuredIn"

    @pytest.mark.asyncio
    async def test_create_kg_edge_cites(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge with relation_type=cites accepted."""
        source_node = KGNode(node_type="Publication", label="Paper A")
        target_node = KGNode(node_type="Publication", label="Paper B")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="cites",
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.relation_type == "cites"

    @pytest.mark.asyncio
    async def test_create_kg_edge_with_source_id(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge stores source_id FK to data_sources."""
        from nfm_db.models import DataSource

        source = DataSource(title="Test Paper", source_type="journal_article")
        db_session.add(source)
        await db_session.flush()

        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
            source_id=source.id,
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.source_id == source.id


# ============================================================
# Constraint Tests
# ============================================================


class TestKGConstraints:
    """Database-level constraint tests for KG models."""

    @pytest.mark.asyncio
    async def test_duplicate_edge_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate (source_node_id, target_node_id, relation_type) rejected."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge1 = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge1)
        await db_session.commit()

        edge2 = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge2)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_different_relation_type_same_nodes_accepted(
        self, db_session: AsyncSession,
    ) -> None:
        """Different relation_types on same node pair accepted."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Publication", label="Paper A")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        db_session.add_all([
            KGEdge(
                source_node_id=source_node.id,
                target_node_id=target_node.id,
                relation_type="cites",
            ),
            KGEdge(
                source_node_id=source_node.id,
                target_node_id=target_node.id,
                relation_type="relatedTo",
            ),
        ])
        await db_session.commit()

        # Both edges should exist
        edges = await _list_edges(db_session)
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_edge_fk_to_nonexistent_source_node_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge with non-existent source_node_id rejected."""
        import uuid

        target_node = KGNode(node_type="Property", label="Density")
        db_session.add(target_node)
        await db_session.flush()

        edge = KGEdge(
            source_node_id=uuid.uuid4(),  # non-existent
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_edge_fk_to_nonexistent_target_node_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge with non-existent target_node_id rejected."""
        import uuid

        source_node = KGNode(node_type="Material", label="UO2")
        db_session.add(source_node)
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=uuid.uuid4(),  # non-existent
            relation_type="hasProperty",
        )
        db_session.add(edge)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_node_fk_to_nonexistent_source_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode with non-existent source_id rejected."""
        import uuid

        node = KGNode(
            node_type="Publication",
            label="Orphan Node",
            source_id=uuid.uuid4(),  # non-existent
        )
        db_session.add(node)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()


async def _list_edges(session: AsyncSession) -> list[KGEdge]:
    """Helper to list all edges via raw SQL (no lazy-load issues)."""
    from sqlalchemy import select

    result = await session.execute(select(KGEdge))
    return list(result.scalars().all())


# ============================================================
# Relationship Tests
# ============================================================


class TestKGRelationships:
    """ORM relationship tests between KGNode and KGEdge."""

    @pytest.mark.asyncio
    async def test_node_has_outgoing_edges(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode -> outgoing_edges relationship works."""
        source_node = KGNode(node_type="Material", label="UO2")
        target1 = KGNode(node_type="Property", label="Density")
        target2 = KGNode(node_type="Property", label="Melting Point")
        db_session.add_all([source_node, target1, target2])
        await db_session.flush()

        db_session.add_all([
            KGEdge(
                source_node_id=source_node.id,
                target_node_id=target1.id,
                relation_type="hasProperty",
            ),
            KGEdge(
                source_node_id=source_node.id,
                target_node_id=target2.id,
                relation_type="hasProperty",
            ),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, source_node, "outgoing_edges")

        assert len(source_node.outgoing_edges) == 2
        relation_types = {e.relation_type for e in source_node.outgoing_edges}
        assert relation_types == {"hasProperty"}

    @pytest.mark.asyncio
    async def test_node_has_incoming_edges(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode -> incoming_edges relationship works."""
        target_node = KGNode(node_type="Publication", label="Paper A")
        source1 = KGNode(node_type="Publication", label="Paper B")
        source2 = KGNode(node_type="Publication", label="Paper C")
        db_session.add_all([target_node, source1, source2])
        await db_session.flush()

        db_session.add_all([
            KGEdge(
                source_node_id=source1.id,
                target_node_id=target_node.id,
                relation_type="cites",
            ),
            KGEdge(
                source_node_id=source2.id,
                target_node_id=target_node.id,
                relation_type="cites",
            ),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, target_node, "incoming_edges")

        assert len(target_node.incoming_edges) == 2

    @pytest.mark.asyncio
    async def test_edge_source_node_relationship(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge -> source_node relationship works."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        await db_session.commit()
        await _refresh_rel(db_session, edge, "source_node")

        assert edge.source_node.label == "UO2"

    @pytest.mark.asyncio
    async def test_edge_target_node_relationship(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge -> target_node relationship works."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        await db_session.commit()
        await _refresh_rel(db_session, edge, "target_node")

        assert edge.target_node.label == "Density"


# ============================================================
# __repr__ Tests
# ============================================================


class TestRepr:
    """__repr__ format tests."""

    @pytest.mark.asyncio
    async def test_kg_node_repr(self, db_session: AsyncSession) -> None:
        """KGNode repr includes type and label."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        r = repr(node)
        assert "Material" in r
        assert "UO2" in r

    @pytest.mark.asyncio
    async def test_kg_edge_repr(self, db_session: AsyncSession) -> None:
        """KGEdge repr includes relation_type."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        r = repr(edge)
        assert "hasProperty" in r


# ============================================================
# Check Constraint Tests
# ============================================================


class TestKGNodeCheckConstraints:
    """CK constraint tests for kg_nodes on SQLite with FK pragma."""

    @pytest.mark.asyncio
    async def test_node_type_check_constraint(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode with invalid node_type is rejected."""
        node = KGNode(node_type="InvalidType", label="Bad Node")
        db_session.add(node)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_status_check_constraint(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode with invalid status is rejected."""
        node = KGNode(node_type="Material", label="Bad Status", status="deleted")
        db_session.add(node)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_confidence_bounds_check(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode with confidence > 1.0 is rejected."""
        node = KGNode(
            node_type="Material", label="Overconfident", confidence=1.5,
        )
        db_session.add(node)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_edge_confidence_bounds_check(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge with confidence < 0.0 is rejected."""
        source_node = KGNode(node_type="Material", label="UO2")
        target_node = KGNode(node_type="Property", label="Density")
        db_session.add_all([source_node, target_node])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source_node.id,
            target_node_id=target_node.id,
            relation_type="hasProperty",
            confidence=-0.1,
        )
        db_session.add(edge)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()
