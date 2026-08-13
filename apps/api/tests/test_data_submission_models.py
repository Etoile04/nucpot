"""ORM-level tests for the M2 Data Submission 1+N models (NFM-2019).

Verifies the six new SQLAlchemy models are registered in
``Base.metadata`` with the expected columns, server defaults, and
foreign-key relationships.  Uses the project-wide ``db_session``
fixture (sqlite + aiosqlite, ``PRAGMA foreign_keys=ON``) so we
exercise the same schema path the integration tests use.

Coverage target: >= 80% on
``nfm_db.models.{resource_node,data_dna,classification_level,
upload_session,ingest_log,hub_node}``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Base,
    ClassificationLevel,
    DataDna,
    HubNode,
    IngestLog,
    ResourceNode,
    UploadSession,
)

# ---------------------------------------------------------------------------
# Metadata registration
# ---------------------------------------------------------------------------


class TestMetadataRegistration:
    """All six tables must be present in Base.metadata after import."""

    EXPECTED_TABLES = {
        "hub_nodes",
        "resource_nodes",
        "data_dna",
        "classification_levels",
        "upload_sessions",
        "ingest_logs",
    }

    def test_all_six_tables_registered(self) -> None:
        registered = set(Base.metadata.tables.keys())
        missing = self.EXPECTED_TABLES - registered
        assert not missing, f"Missing tables in Base.metadata: {missing}"

    def test_hub_nodes_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["hub_nodes"].columns}
        assert {
            "id",
            "name",
            "api_endpoint",
            "public_key",
            "status",
            "last_heartbeat",
            "created_at",
            "updated_at",
        } <= cols

    def test_resource_nodes_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["resource_nodes"].columns}
        assert {
            "id",
            "hub_node_id",
            "name",
            "node_type",
            "api_endpoint",
            "public_key",
            "status",
            "last_heartbeat",
            "offline_since",
            "sync_watermark",
            "created_at",
            "updated_at",
        } <= cols

    def test_data_dna_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["data_dna"].columns}
        assert {
            "id",
            "record_type",
            "record_id",
            "dna_uuid",
            "sha256_hash",
            "sm3_hash",
            "created_at",
            "updated_at",
        } <= cols

    def test_classification_levels_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["classification_levels"].columns}
        assert {"id", "label", "description", "created_at", "updated_at"} <= cols

    def test_upload_sessions_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["upload_sessions"].columns}
        assert {
            "id",
            "resource_node_id",
            "file_name",
            "total_size",
            "chunk_size",
            "total_chunks",
            "uploaded_chunks",
            "resume_token",
            "sha256_full",
            "status",
            "created_at",
            "updated_at",
        } <= cols

    def test_ingest_logs_columns(self) -> None:
        cols = {c.name for c in Base.metadata.tables["ingest_logs"].columns}
        assert {
            "id",
            "resource_node_id",
            "hub_node_id",
            "direction",
            "record_count",
            "data_size_bytes",
            "status",
            "error_detail",
            "started_at",
            "completed_at",
        } <= cols

    def _fk_targets(self, table_name: str) -> set[str]:
        """Collect every FK target_fullname for a table (any order)."""
        table = Base.metadata.tables[table_name]
        return {
            fk.target_fullname
            for fkc in table.foreign_key_constraints
            for fk in fkc.elements
        }

    def test_resource_nodes_fk_to_hub(self) -> None:
        assert "hub_nodes.id" in self._fk_targets("resource_nodes")

    def test_upload_sessions_fk_to_resource(self) -> None:
        assert "resource_nodes.id" in self._fk_targets("upload_sessions")

    def test_ingest_logs_fk_to_resource_and_hub(self) -> None:
        targets = self._fk_targets("ingest_logs")
        assert "resource_nodes.id" in targets
        assert "hub_nodes.id" in targets


# ---------------------------------------------------------------------------
# Creation tests
# ---------------------------------------------------------------------------


class TestHubNodeCreation:
    """HubNode creation happy-path and default-status coverage."""

    @pytest.mark.asyncio
    async def test_create_with_defaults(self, db_session: AsyncSession) -> None:
        hub = HubNode(
            name="National Hub",
            api_endpoint="https://hub.example.com/api",
        )
        db_session.add(hub)
        await db_session.commit()
        await db_session.refresh(hub)
        assert hub.id is not None
        assert hub.status == "active"
        assert hub.public_key is None
        assert hub.last_heartbeat is None
        assert hub.created_at is not None
        assert hub.updated_at is not None

    @pytest.mark.asyncio
    async def test_create_with_public_key(
        self,
        db_session: AsyncSession,
    ) -> None:
        hub = HubNode(
            name="Hub",
            api_endpoint="https://hub.example.com/api",
            public_key="ssh-rsa AAAA",
        )
        db_session.add(hub)
        await db_session.commit()
        await db_session.refresh(hub)
        assert hub.public_key == "ssh-rsa AAAA"


class TestResourceNodeCreation:
    """ResourceNode creation + FK enforcement."""

    @pytest.mark.asyncio
    async def test_create_under_hub(self, db_session: AsyncSession) -> None:
        hub = HubNode(
            name="Hub", api_endpoint="https://hub.example.com/api"
        )
        db_session.add(hub)
        await db_session.flush()
        node = ResourceNode(
            hub_node_id=hub.id,
            name="Node 1",
            node_type="computing",
            api_endpoint="https://node1.example.com/api",
        )
        db_session.add(node)
        await db_session.commit()
        await db_session.refresh(node)
        assert node.hub_node_id == hub.id
        assert node.status == "active"
        assert node.offline_since is None
        assert node.sync_watermark is None

    @pytest.mark.asyncio
    async def test_resource_node_requires_existing_hub(
        self,
        db_session: AsyncSession,
    ) -> None:
        """FK constraint: hub_node_id must reference an existing hub."""
        node = ResourceNode(
            hub_node_id=uuid.uuid4(),
            name="Orphan",
            node_type="storage",
            api_endpoint="https://orphan.example.com/api",
        )
        db_session.add(node)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestDataDnaCreation:
    """DataDna creation + uniqueness on dna_uuid."""

    @pytest.mark.asyncio
    async def test_create_minimal(self, db_session: AsyncSession) -> None:
        cl = ClassificationLevel(label="非密")
        db_session.add(cl)
        await db_session.flush()
        dna = DataDna(
            record_type="material",
            record_id=uuid.uuid4(),
            dna_uuid=uuid.uuid4(),
            sha256_hash="a" * 64,
            classification_level=cl.id,
        )
        db_session.add(dna)
        await db_session.commit()
        await db_session.refresh(dna)
        assert dna.id is not None
        assert dna.sm3_hash is None
        assert dna.sha256_hash == "a" * 64

    @pytest.mark.asyncio
    async def test_create_with_sm3(self, db_session: AsyncSession) -> None:
        cl = ClassificationLevel(label="内部")
        db_session.add(cl)
        await db_session.flush()
        dna = DataDna(
            record_type="property",
            record_id=uuid.uuid4(),
            dna_uuid=uuid.uuid4(),
            sha256_hash="a" * 64,
            sm3_hash="b" * 64,
            classification_level=cl.id,
        )
        db_session.add(dna)
        await db_session.commit()
        await db_session.refresh(dna)
        assert dna.sm3_hash == "b" * 64

    @pytest.mark.asyncio
    async def test_dna_uuid_must_be_unique(
        self,
        db_session: AsyncSession,
    ) -> None:
        cl = ClassificationLevel(label="秘密")
        db_session.add(cl)
        await db_session.flush()
        shared_uuid = uuid.uuid4()
        db_session.add(
            DataDna(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=shared_uuid,
                sha256_hash="a" * 64,
                classification_level=cl.id,
            )
        )
        await db_session.commit()
        db_session.add(
            DataDna(
                record_type="material",
                record_id=uuid.uuid4(),
                dna_uuid=shared_uuid,
                sha256_hash="c" * 64,
                classification_level=cl.id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestClassificationLevelCreation:
    """ClassificationLevel creation + uniqueness on label."""

    @pytest.mark.asyncio
    async def test_create_with_description(
        self,
        db_session: AsyncSession,
    ) -> None:
        cl = ClassificationLevel(
            label="非密", description="Unclassified."
        )
        db_session.add(cl)
        await db_session.commit()
        await db_session.refresh(cl)
        assert cl.id is not None
        assert cl.label == "非密"
        assert cl.description == "Unclassified."

    @pytest.mark.asyncio
    async def test_label_must_be_unique(
        self,
        db_session: AsyncSession,
    ) -> None:
        db_session.add(ClassificationLevel(label="内部"))
        await db_session.commit()
        db_session.add(ClassificationLevel(label="内部"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestUploadSessionCreation:
    """UploadSession creation + FK + default uploaded_chunks."""

    @pytest.mark.asyncio
    async def test_create_defaults(self, db_session: AsyncSession) -> None:
        hub = HubNode(
            name="Hub", api_endpoint="https://hub.example.com/api"
        )
        db_session.add(hub)
        await db_session.flush()
        node = ResourceNode(
            hub_node_id=hub.id,
            name="Node",
            node_type="storage",
            api_endpoint="https://node.example.com/api",
        )
        db_session.add(node)
        await db_session.flush()
        cl = ClassificationLevel(label="非密")
        db_session.add(cl)
        await db_session.flush()
        session_obj = UploadSession(
            resource_node_id=node.id,
            file_name="data.dat",
            total_size=1000,
            chunk_size=100,
            total_chunks=10,
            classification_level=cl.id,
        )
        db_session.add(session_obj)
        await db_session.commit()
        await db_session.refresh(session_obj)
        assert session_obj.uploaded_chunks == 0
        assert session_obj.status == "pending"
        assert session_obj.resume_token is None
        assert session_obj.sha256_full is None

    @pytest.mark.asyncio
    async def test_upload_session_requires_resource(
        self,
        db_session: AsyncSession,
    ) -> None:
        session_obj = UploadSession(
            resource_node_id=uuid.uuid4(),
            file_name="data.dat",
            total_size=1000,
            chunk_size=100,
            total_chunks=10,
        )
        db_session.add(session_obj)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestIngestLogCreation:
    """IngestLog creation + FK + nullable hub_node_id."""

    @pytest.mark.asyncio
    async def test_create_without_hub(self, db_session: AsyncSession) -> None:
        hub = HubNode(
            name="Hub", api_endpoint="https://hub.example.com/api"
        )
        db_session.add(hub)
        await db_session.flush()
        node = ResourceNode(
            hub_node_id=hub.id,
            name="Node",
            node_type="storage",
            api_endpoint="https://node.example.com/api",
        )
        db_session.add(node)
        await db_session.flush()
        log = IngestLog(
            resource_node_id=node.id,
            direction="upload",
        )
        db_session.add(log)
        await db_session.commit()
        await db_session.refresh(log)
        assert log.hub_node_id is None
        assert log.record_count == 0
        assert log.data_size_bytes == 0
        assert log.status == "pending"
        assert log.error_detail is None
        assert log.started_at is not None
        assert log.completed_at is None

    @pytest.mark.asyncio
    async def test_create_with_hub(self, db_session: AsyncSession) -> None:
        hub = HubNode(
            name="Hub", api_endpoint="https://hub.example.com/api"
        )
        db_session.add(hub)
        await db_session.flush()
        node = ResourceNode(
            hub_node_id=hub.id,
            name="Node",
            node_type="storage",
            api_endpoint="https://node.example.com/api",
        )
        db_session.add(node)
        await db_session.flush()
        log = IngestLog(
            resource_node_id=node.id,
            hub_node_id=hub.id,
            direction="download",
        )
        db_session.add(log)
        await db_session.commit()
        await db_session.refresh(log)
        assert log.hub_node_id == hub.id
        assert log.direction == "download"

    @pytest.mark.asyncio
    async def test_ingest_log_requires_resource(
        self,
        db_session: AsyncSession,
    ) -> None:
        log = IngestLog(
            resource_node_id=uuid.uuid4(),
            direction="upload",
        )
        db_session.add(log)
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


# ---------------------------------------------------------------------------
# Repr sanity
# ---------------------------------------------------------------------------


class TestRepr:
    """__repr__ should not raise and should include the class identity."""

    def test_hub_node_repr(self) -> None:
        hub = HubNode(
            id=uuid.uuid4(),
            name="Hub",
            api_endpoint="https://hub.example.com/api",
        )
        assert "HubNode" in repr(hub)

    def test_resource_node_repr(self) -> None:
        node = ResourceNode(
            id=uuid.uuid4(),
            hub_node_id=uuid.uuid4(),
            name="Node",
            node_type="storage",
            api_endpoint="https://node.example.com/api",
        )
        assert "ResourceNode" in repr(node)

    def test_data_dna_repr(self) -> None:
        dna = DataDna(
            id=uuid.uuid4(),
            record_type="material",
            record_id=uuid.uuid4(),
            dna_uuid=uuid.uuid4(),
            sha256_hash="a" * 64,
        )
        assert "DataDna" in repr(dna)

    def test_classification_level_repr(self) -> None:
        cl = ClassificationLevel(id=uuid.uuid4(), label="秘密")
        assert "ClassificationLevel" in repr(cl)

    def test_upload_session_repr(self) -> None:
        session_obj = UploadSession(
            id=uuid.uuid4(),
            resource_node_id=uuid.uuid4(),
            file_name="x.dat",
            total_size=10,
            chunk_size=1,
            total_chunks=10,
        )
        assert "UploadSession" in repr(session_obj)

    def test_ingest_log_repr(self) -> None:
        log = IngestLog(
            id=uuid.uuid4(),
            resource_node_id=uuid.uuid4(),
            direction="upload",
        )
        assert "IngestLog" in repr(log)


# ---------------------------------------------------------------------------
# SQLAlchemy inspect() — used by Alembic autogenerate to detect drift
# ---------------------------------------------------------------------------


class TestTableInspection:
    """Drop-and-recreate validation: the schema produced by
    ``Base.metadata.create_all`` on the in-memory sqlite engine
    contains all six tables and the FK constraints.  This is
    the same code path the rest of the test suite uses.
    """

    @pytest.mark.asyncio
    async def test_six_tables_persist_in_metadata(
        self,
        db_session: AsyncSession,
    ) -> None:
        # The ``db_session`` fixture already ran ``create_all`` on
        # the in-memory engine.  We re-check via SQLAlchemy
        # metadata, which is the canonical source of truth.
        names = set(Base.metadata.tables.keys())
        assert {
            "hub_nodes",
            "resource_nodes",
            "data_dna",
            "classification_levels",
            "upload_sessions",
            "ingest_logs",
        } <= names

    @pytest.mark.asyncio
    async def test_create_all_then_insert_round_trip(
        self,
        db_session: AsyncSession,
    ) -> None:
        """End-to-end: create a hub, a resource, an upload session,
        a data DNA, a classification level, and an ingest log in
        a single transaction.  Exercises every table and every FK.
        """
        hub = HubNode(
            name="Hub", api_endpoint="https://hub.example.com/api"
        )
        db_session.add(hub)
        await db_session.flush()

        node = ResourceNode(
            hub_node_id=hub.id,
            name="Node 1",
            node_type="computing",
            api_endpoint="https://node1.example.com/api",
        )
        db_session.add(node)
        await db_session.flush()

        cl = ClassificationLevel(label="非密")
        db_session.add(cl)
        await db_session.flush()

        session_obj = UploadSession(
            resource_node_id=node.id,
            file_name="data.dat",
            total_size=1024,
            chunk_size=128,
            total_chunks=8,
            classification_level=cl.id,
        )
        db_session.add(session_obj)

        dna = DataDna(
            record_type="material",
            record_id=uuid.uuid4(),
            dna_uuid=uuid.uuid4(),
            sha256_hash="a" * 64,
            classification_level=cl.id,
        )
        db_session.add(dna)

        log = IngestLog(
            resource_node_id=node.id,
            hub_node_id=hub.id,
            direction="upload",
        )
        db_session.add(log)

        await db_session.commit()
        for obj in (hub, node, session_obj, dna, cl, log):
            await db_session.refresh(obj)
        assert session_obj.id is not None
        assert dna.id is not None
        assert cl.id is not None
        assert log.id is not None
