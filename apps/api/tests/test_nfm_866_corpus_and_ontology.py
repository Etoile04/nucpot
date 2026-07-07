"""Tests for NFM-866: corpus columns on KGNode/KGEdge + OntologyIdMap model.

Covers:
- KGNode corpus_id, synced_to_graph, graph_synced_at defaults and explicit values
- KGEdge corpus_id, synced_to_graph, graph_synced_at defaults and explicit values
- OntologyIdMap creation with composite PK (nvl_id, corpus_id)
- OntologyIdMap FK to KGNode (CASCADE delete)
- OntologyIdMap relationship to KGNode
- OntologyIdMap __repr__
- Duplicate (nvl_id, corpus_id) rejected via unique constraint
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, OntologyIdMap

# ============================================================
# KGNode corpus columns
# ============================================================


class TestKGNodeCorpusColumns:
    """Corpus and graph-sync fields on KGNode."""

    @pytest.mark.asyncio
    async def test_node_corpus_defaults(
        self, db_session: AsyncSession,
    ) -> None:
        """New KGNode has corpus_id=None, synced_to_graph=False."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.corpus_id is None
        assert node.synced_to_graph is False
        assert node.graph_synced_at is None

    @pytest.mark.asyncio
    async def test_node_corpus_explicit(
        self, db_session: AsyncSession,
    ) -> None:
        """KGNode accepts explicit corpus_id and sync fields."""
        node = KGNode(
            node_type="Material",
            label="UO2",
            corpus_id="nvl-1.1",
            synced_to_graph=True,
            graph_synced_at=datetime(2026, 1, 15, tzinfo=datetime.UTC),
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.corpus_id == "nvl-1.1"
        assert node.synced_to_graph is True
        assert node.graph_synced_at is not None
        assert node.graph_synced_at.year == 2026
        assert node.graph_synced_at.month == 1
        assert node.graph_synced_at.day == 15

    @pytest.mark.asyncio
    async def test_node_existing_fields_unchanged(
        self, db_session: AsyncSession,
    ) -> None:
        """Existing KGNode fields still work with corpus fields."""
        node = KGNode(
            node_type="Property",
            label="Thermal Conductivity",
            confidence=0.85,
            status="active",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)

        assert node.node_type == "Property"
        assert node.label == "Thermal Conductivity"
        assert node.confidence == pytest.approx(0.85)
        assert node.status == "active"
        assert node.corpus_id is None
        assert node.synced_to_graph is False


# ============================================================
# KGEdge corpus columns
# ============================================================


class TestKGEdgeCorpusColumns:
    """Corpus and graph-sync fields on KGEdge."""

    @pytest.mark.asyncio
    async def test_edge_corpus_defaults(
        self, db_session: AsyncSession,
    ) -> None:
        """New KGEdge has corpus_id=None, synced_to_graph=False."""
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Density")
        db_session.add_all([source, target])
        await db_session.flush()

        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.corpus_id is None
        assert edge.synced_to_graph is False
        assert edge.graph_synced_at is None

    @pytest.mark.asyncio
    async def test_edge_corpus_explicit(
        self, db_session: AsyncSession,
    ) -> None:
        """KGEdge accepts explicit corpus_id and sync fields."""
        source = KGNode(node_type="Material", label="UO2")
        target = KGNode(node_type="Property", label="Melting Point")
        db_session.add_all([source, target])
        await db_session.flush()

        sync_time = datetime(2026, 2, 1, 12, 0, 0, tzinfo=datetime.UTC)
        edge = KGEdge(
            source_node_id=source.id,
            target_node_id=target.id,
            relation_type="hasProperty",
            corpus_id="nvl-1.1",
            synced_to_graph=True,
            graph_synced_at=sync_time,
        )
        db_session.add(edge)
        await db_session.commit()
        await db_session.refresh(edge)

        assert edge.corpus_id == "nvl-1.1"
        assert edge.synced_to_graph is True
        assert edge.graph_synced_at is not None
        assert edge.graph_synced_at.year == 2026
        assert edge.graph_synced_at.month == 2
        assert edge.graph_synced_at.day == 1


# ============================================================
# OntologyIdMap model
# ============================================================


class TestOntologyIdMapCreation:
    """OntologyIdMap CRUD tests."""

    @pytest.mark.asyncio
    async def test_create_ontology_id_map(
        self, db_session: AsyncSession,
    ) -> None:
        """OntologyIdMap can be created with required fields."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.flush()

        mapping = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node.id,
            graph_label="Material_UO2",
        )
        db_session.add(mapping)
        await db_session.commit()
        await db_session.refresh(mapping)

        assert mapping.nvl_id == "MAT-001"
        assert mapping.corpus_id == "nvl-1.1"
        assert mapping.node_id == node.id
        assert mapping.graph_label == "Material_UO2"
        assert mapping.created_at is not None

    @pytest.mark.asyncio
    async def test_create_ontology_id_map_defaults(
        self, db_session: AsyncSession,
    ) -> None:
        """OntologyIdMap graph_label is optional, created_at auto-set."""
        node = KGNode(node_type="Property", label="Thermal Conductivity")
        db_session.add(node)
        await db_session.flush()

        mapping = OntologyIdMap(
            nvl_id="PROP-042",
            corpus_id="nvl-1.1",
            node_id=node.id,
        )
        db_session.add(mapping)
        await db_session.commit()
        await db_session.refresh(mapping)

        assert mapping.graph_label is None
        assert mapping.created_at is not None

    @pytest.mark.asyncio
    async def test_duplicate_nvl_corpus_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate (nvl_id, corpus_id) rejected via unique constraint."""
        node1 = KGNode(node_type="Material", label="UO2")
        node2 = KGNode(node_type="Material", label="Uranium Dioxide")
        db_session.add_all([node1, node2])
        await db_session.flush()

        mapping1 = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node1.id,
        )
        db_session.add(mapping1)
        await db_session.commit()

        mapping2 = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node2.id,
        )
        db_session.add(mapping2)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_same_nvl_different_corpus_accepted(
        self, db_session: AsyncSession,
    ) -> None:
        """Same nvl_id with different corpus_id is accepted."""
        node1 = KGNode(node_type="Material", label="UO2")
        node2 = KGNode(node_type="Material", label="Uranium Dioxide")
        db_session.add_all([node1, node2])
        await db_session.flush()

        db_session.add_all([
            OntologyIdMap(
                nvl_id="MAT-001",
                corpus_id="nvl-1.1",
                node_id=node1.id,
            ),
            OntologyIdMap(
                nvl_id="MAT-001",
                corpus_id="other-corpus",
                node_id=node2.id,
            ),
        ])
        await db_session.commit()

        from sqlalchemy import select

        result = await db_session.execute(select(OntologyIdMap))
        mappings = list(result.scalars().all())
        assert len(mappings) == 2

    @pytest.mark.asyncio
    async def test_fk_to_nonexistent_node_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """OntologyIdMap with non-existent node_id FK rejected."""
        import uuid

        mapping = OntologyIdMap(
            nvl_id="MAT-999",
            corpus_id="nvl-1.1",
            node_id=uuid.uuid4(),  # non-existent
        )
        db_session.add(mapping)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_cascade_delete_from_node(
        self, db_session: AsyncSession,
    ) -> None:
        """Deleting a KGNode cascades to its OntologyIdMap entries."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.flush()

        mapping = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node.id,
        )
        db_session.add(mapping)
        await db_session.commit()

        await db_session.delete(node)
        await db_session.commit()

        from sqlalchemy import select

        result = await db_session.execute(select(OntologyIdMap))
        mappings = list(result.scalars().all())
        assert len(mappings) == 0


class TestOntologyIdMapRelationship:
    """ORM relationship tests for OntologyIdMap -> KGNode."""

    @pytest.mark.asyncio
    async def test_mapping_node_relationship(
        self, db_session: AsyncSession,
    ) -> None:
        """OntologyIdMap.node resolves to the correct KGNode."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.flush()

        mapping = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node.id,
        )
        db_session.add(mapping)
        await db_session.commit()
        await db_session.refresh(mapping, ["node"])

        assert mapping.node.label == "UO2"
        assert mapping.node.node_type == "Material"


class TestOntologyIdMapRepr:
    """__repr__ format test."""

    @pytest.mark.asyncio
    async def test_repr(self, db_session: AsyncSession) -> None:
        """OntologyIdMap repr includes nvl_id, corpus_id, and node_id."""
        node = KGNode(node_type="Material", label="UO2")
        db_session.add(node)
        await db_session.flush()

        mapping = OntologyIdMap(
            nvl_id="MAT-001",
            corpus_id="nvl-1.1",
            node_id=node.id,
        )
        db_session.add(mapping)
        await db_session.commit()
        await db_session.refresh(mapping)

        r = repr(mapping)
        assert "MAT-001" in r
        assert "nvl-1.1" in r
        assert str(node.id) in r
