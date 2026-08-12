"""NFM-2873-T2: ORM relationship tests for ontology_version FK.

Verifies the ``ontology_version`` relationship on KEntityType and
KRelationType, plus the back-populated ``entity_types`` and
``relation_types`` collections on OntologyVersion.

These tests complement the migration parity tests in
``test_migration_055_ontology_version_fk.py`` (T1) by exercising the
runtime ORM behavior, not just the DDL.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import KEntityType, KRelationType, OntologyVersion


async def _refresh_rel(session: AsyncSession, obj: object, *attrs: str) -> None:
    """Refresh specific relationship attributes on a mapped instance."""
    await session.refresh(obj, list(attrs))


# ============================================================
# KEntityType.ontology_version
# ============================================================


class TestKEntityTypeOntologyVersionRelationship:
    """KEntityType.ontology_version resolves to the parent OntologyVersion."""

    @pytest.mark.asyncio
    async def test_relationship_attribute_exists(self) -> None:
        """KEntityType declares an ``ontology_version`` mapper attribute."""
        assert hasattr(KEntityType, "ontology_version")

    @pytest.mark.asyncio
    async def test_round_trips_to_ontology_version(
        self,
        db_session: AsyncSession,
        admin_user,  # noqa: ANN001
    ) -> None:
        """KEntityType.ontology_version resolves to the linked OntologyVersion."""
        version = OntologyVersion(
            version="1.0.0",
            status="published",
            created_by=admin_user.id,
        )
        entity = KEntityType(
            name="Material",
            ontology_version_id=None,  # assigned after flush
        )
        db_session.add_all([version, entity])
        await db_session.flush()
        entity.ontology_version_id = version.id

        await db_session.commit()
        await _refresh_rel(db_session, entity, "ontology_version")

        assert entity.ontology_version is not None
        assert entity.ontology_version.id == version.id
        assert entity.ontology_version.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_relationship_is_none_when_fk_is_null(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A row with NULL ontology_version_id has ``ontology_version is None``."""
        entity = KEntityType(name="Property")
        db_session.add(entity)
        await db_session.commit()
        await _refresh_rel(db_session, entity, "ontology_version")

        assert entity.ontology_version is None


# ============================================================
# KRelationType.ontology_version
# ============================================================


class TestKRelationTypeOntologyVersionRelationship:
    """KRelationType.ontology_version resolves to the parent OntologyVersion."""

    @pytest.mark.asyncio
    async def test_relationship_attribute_exists(self) -> None:
        """KRelationType declares an ``ontology_version`` mapper attribute."""
        assert hasattr(KRelationType, "ontology_version")

    @pytest.mark.asyncio
    async def test_round_trips_to_ontology_version(
        self,
        db_session: AsyncSession,
        admin_user,  # noqa: ANN001
    ) -> None:
        """KRelationType.ontology_version resolves to the linked OntologyVersion."""
        version = OntologyVersion(
            version="2.0.0",
            status="published",
            created_by=admin_user.id,
        )
        relation = KRelationType(name="hasProperty", ontology_version_id=None)
        db_session.add_all([version, relation])
        await db_session.flush()
        relation.ontology_version_id = version.id

        await db_session.commit()
        await _refresh_rel(db_session, relation, "ontology_version")

        assert relation.ontology_version is not None
        assert relation.ontology_version.id == version.id
        assert relation.ontology_version.version == "2.0.0"


# ============================================================
# OntologyVersion.entity_types back-populates
# ============================================================


class TestOntologyVersionEntityTypesBackPopulates:
    """OntologyVersion.entity_types collects linked KEntityType rows."""

    @pytest.mark.asyncio
    async def test_relationship_attribute_exists(self) -> None:
        """OntologyVersion declares an ``entity_types`` mapper attribute."""
        assert hasattr(OntologyVersion, "entity_types")

    @pytest.mark.asyncio
    async def test_collects_attached_entity_types(
        self,
        db_session: AsyncSession,
        admin_user,  # noqa: ANN001
    ) -> None:
        """entity_types returns every KEntityType linked to this version."""
        version = OntologyVersion(
            version="3.0.0",
            status="published",
            created_by=admin_user.id,
        )
        db_session.add(version)
        await db_session.flush()

        entity_a = KEntityType(
            name="Material",
            ontology_version_id=version.id,
        )
        entity_b = KEntityType(
            name="Property",
            ontology_version_id=version.id,
        )
        db_session.add_all([entity_a, entity_b])
        await db_session.commit()
        await _refresh_rel(db_session, version, "entity_types")

        names = {e.name for e in version.entity_types}
        assert names == {"Material", "Property"}


# ============================================================
# OntologyVersion.relation_types back-populates
# ============================================================


class TestOntologyVersionRelationTypesBackPopulates:
    """OntologyVersion.relation_types collects linked KRelationType rows."""

    @pytest.mark.asyncio
    async def test_relationship_attribute_exists(self) -> None:
        """OntologyVersion declares a ``relation_types`` mapper attribute."""
        assert hasattr(OntologyVersion, "relation_types")

    @pytest.mark.asyncio
    async def test_collects_attached_relation_types(
        self,
        db_session: AsyncSession,
        admin_user,  # noqa: ANN001
    ) -> None:
        """relation_types returns every KRelationType linked to this version."""
        version = OntologyVersion(
            version="3.1.0",
            status="published",
            created_by=admin_user.id,
        )
        db_session.add(version)
        await db_session.flush()

        rel_a = KRelationType(
            name="hasProperty",
            ontology_version_id=version.id,
        )
        rel_b = KRelationType(
            name="measuredBy",
            ontology_version_id=version.id,
        )
        db_session.add_all([rel_a, rel_b])
        await db_session.commit()
        await _refresh_rel(db_session, version, "relation_types")

        names = {r.name for r in version.relation_types}
        assert names == {"hasProperty", "measuredBy"}


# ============================================================
# Bidirectional symmetry
# ============================================================


class TestBidirectionalSymmetry:
    """Forward and reverse sides agree after attach."""

    @pytest.mark.asyncio
    async def test_setting_fk_updates_back_collection(
        self,
        db_session: AsyncSession,
        admin_user,  # noqa: ANN001
    ) -> None:
        """Setting ontology_version_id is visible from both sides after flush."""
        version = OntologyVersion(
            version="4.0.0",
            status="published",
            created_by=admin_user.id,
        )
        entity = KEntityType(name="Experiment")
        relation = KRelationType(name="measuredBy")
        db_session.add_all([version, entity, relation])
        await db_session.flush()

        entity.ontology_version_id = version.id
        relation.ontology_version_id = version.id
        await db_session.commit()

        await _refresh_rel(db_session, version, "entity_types", "relation_types")
        assert any(e.name == "Experiment" for e in version.entity_types)
        assert any(r.name == "measuredBy" for r in version.relation_types)