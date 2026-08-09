"""Tests for the DataCollectionRequest ORM model (NFM-2619).

Covers table metadata, column set, index constraints, CRUD behaviour,
and the Pydantic response schema round-trip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DATA_COLLECTION_REQUEST_STATUSES,
    SOURCE_PREFERENCES,
    DataCollectionRequest,
)
from nfm_db.models.data_collection_request import (
    DataCollectionRequest as DirectImport,
)
from nfm_db.models.ontology_version import OntologyVersion
from nfm_db.models.user import User
from nfm_db.schemas.data_collection_request import (
    CoverageMetricsResponse,
    DataCollectionRequestResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEED_USER_ID = uuid.uuid4()


async def _make_ov(session: AsyncSession) -> OntologyVersion:
    """Create and flush a minimal OntologyVersion for FK references."""
    user = User(
        id=_SEED_USER_ID,
        username="testuser",
        email="test@example.com",
        hashed_password="dummy-hash",
    )
    session.add(user)
    await session.flush()
    ov = OntologyVersion(
        version="1.0.0",
        status="published",
        created_by=user.id,
        ontology_data={"entities": []},
    )
    session.add(ov)
    await session.flush()
    return ov


# ---------------------------------------------------------------------------
# Static metadata
# ---------------------------------------------------------------------------


class TestDataCollectionRequestMetadata:
    """Table name, column set, barrel export."""

    def test_tablename(self) -> None:
        assert DataCollectionRequest.__tablename__ == "data_collection_requests"

    def test_direct_module_import(self) -> None:
        assert DirectImport is DataCollectionRequest

    def test_columns(self) -> None:
        columns = {c.name for c in DataCollectionRequest.__table__.columns}
        expected = {
            "id",
            "ontology_version_id",
            "entity_type",
            "property",
            "material_system",
            "urgency",
            "source_preference",
            "status",
            "requested_at",
            "completed_at",
            "dispatched_at",
            "dispatched_path",
            "dispatch_status",
            "result_reference",
            "metadata_",
            "created_at",
            "updated_at",
        }
        assert columns == expected

    def test_status_tuple(self) -> None:
        assert DATA_COLLECTION_REQUEST_STATUSES == (
            "open",
            "in_progress",
            "completed",
            "declined",
        )

    def test_source_preference_tuple(self) -> None:
        assert SOURCE_PREFERENCES == (
            "literature",
            "dft",
            "external_db",
            "any",
        )


class TestDataCollectionRequestRepr:
    """__repr__ smoke test."""

    def test_repr_contains_key_fields(self) -> None:
        req = DataCollectionRequest(
            id=uuid.uuid4(),
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
            status="open",
        )
        r = repr(req)
        assert "NuclearMaterial" in r
        assert "density" in r
        assert "UO2" in r
        assert "open" in r


# ---------------------------------------------------------------------------
# CRUD behaviour
# ---------------------------------------------------------------------------


class TestDataCollectionRequestCreation:
    """Insertion & persistence."""

    @pytest.mark.asyncio
    async def test_create_minimal(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A request can be persisted with required columns."""
        ov = await _make_ov(db_session)

        req = DataCollectionRequest(
            ontology_version_id=ov.id,
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
        )
        db_session.add(req)
        await db_session.commit()
        await db_session.refresh(req)

        assert isinstance(req.id, uuid.UUID)
        assert req.ontology_version_id == ov.id
        assert req.entity_type == "NuclearMaterial"
        assert req.property == "density"
        assert req.material_system == "UO2"
        assert req.urgency == 0
        assert req.source_preference == "any"
        assert req.status == "open"
        assert req.requested_at is not None
        assert req.completed_at is None
        assert req.metadata_ is None
        assert req.created_at is not None

    @pytest.mark.asyncio
    async def test_create_with_all_fields(
        self,
        db_session: AsyncSession,
    ) -> None:
        """All optional/overridable fields are stored correctly."""
        ov = await _make_ov(db_session)
        now = datetime.now(UTC)
        meta = {"reason": "fuel performance study", "priority_team": "reactor"}

        req = DataCollectionRequest(
            ontology_version_id=ov.id,
            entity_type="Isotope",
            property="thermal_conductivity",
            material_system="Zr",
            urgency=5,
            source_preference="literature",
            status="in_progress",
            requested_at=now,
            completed_at=None,
            metadata_=meta,
        )
        db_session.add(req)
        await db_session.commit()
        await db_session.refresh(req)

        assert req.urgency == 5
        assert req.source_preference == "literature"
        assert req.status == "in_progress"
        assert req.metadata_ == meta

    @pytest.mark.asyncio
    async def test_unique_constraint_violation(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Duplicate (ov, entity, property, material) raises IntegrityError."""
        ov = await _make_ov(db_session)

        req1 = DataCollectionRequest(
            ontology_version_id=ov.id,
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
        )
        db_session.add(req1)
        await db_session.flush()

        req2 = DataCollectionRequest(
            ontology_version_id=ov.id,
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
        )
        db_session.add(req2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_different_material_system_allows_duplicate(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Same (ov, entity, property) but different material_system is OK."""
        ov = await _make_ov(db_session)

        for mat in ("UO2", "Zr", "U"):
            req = DataCollectionRequest(
                ontology_version_id=ov.id,
                entity_type="NuclearMaterial",
                property="density",
                material_system=mat,
            )
            db_session.add(req)
        await db_session.commit()

        result = await db_session.execute(
            select(DataCollectionRequest),
        )
        assert len(result.scalars().all()) == 3

    @pytest.mark.asyncio
    async def test_fk_ontology_version_required(
        self,
        db_session: AsyncSession,
    ) -> None:
        """ontology_version_id FK must reference a real ontology_versions row."""
        req = DataCollectionRequest(
            ontology_version_id=uuid.uuid4(),
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
        )
        db_session.add(req)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ---------------------------------------------------------------------------
# Pydantic schema round-trip
# ---------------------------------------------------------------------------


class TestDataCollectionRequestResponseSchema:
    """ORM → Pydantic serialization."""

    @pytest.mark.asyncio
    async def test_from_attributes(
        self,
        db_session: AsyncSession,
    ) -> None:
        """DataCollectionRequestResponse.model_validate works from ORM object."""
        ov = await _make_ov(db_session)

        req = DataCollectionRequest(
            ontology_version_id=ov.id,
            entity_type="NuclearMaterial",
            property="density",
            material_system="UO2",
            urgency=3,
            source_preference="dft",
        )
        db_session.add(req)
        await db_session.commit()
        await db_session.refresh(req)

        resp = DataCollectionRequestResponse.model_validate(req)

        assert resp.id == req.id
        assert resp.ontology_version_id == ov.id
        assert resp.entity_type == "NuclearMaterial"
        assert resp.property == "density"
        assert resp.material_system == "UO2"
        assert resp.urgency == 3
        assert resp.source_preference == "dft"
        assert resp.status == "open"
        assert resp.completed_at is None
        assert resp.metadata_ is None


class TestCoverageMetricsResponseSchema:
    """CoverageMetricsResponse construction and validation."""

    def test_valid_construction(self) -> None:
        m = CoverageMetricsResponse(
            ontology_version_id=uuid.uuid4(),
            total_requests=10,
            open_requests=4,
            in_progress_requests=2,
            completed_requests=3,
            declined_requests=1,
            coverage_rate=0.3,
            computed_at=datetime.now(UTC),
        )
        assert m.coverage_rate == 0.3
        assert m.total_requests == 10

    def test_coverage_rate_bounds(self) -> None:
        """coverage_rate must be in [0, 1]."""
        with pytest.raises(Exception):
            CoverageMetricsResponse(
                ontology_version_id=uuid.uuid4(),
                total_requests=1,
                open_requests=0,
                in_progress_requests=0,
                completed_requests=0,
                declined_requests=1,
                coverage_rate=1.5,  # > 1.0
                computed_at=datetime.now(UTC),
            )
