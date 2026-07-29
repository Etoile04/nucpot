"""Unit tests for M2 Data Submission Pydantic schemas (NFM-2019).

Covers all 6 tables' Read/Create schemas with:
- Valid data acceptance
- Field constraint validation (min/max length, gt/ge)
- Optional field defaults
- Enum value validation
- from_attributes=True compatibility (using mock objects)

Target: >= 80% coverage on data_submission.py
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from nfm_db.schemas.data_submission import (
    ClassificationLabel,
    ClassificationLevelCreate,
    ClassificationLevelRead,
    DataDnaCreate,
    DataDnaRead,
    HubNodeCreate,
    HubNodeRead,
    IngestDirection,
    IngestLogCreate,
    IngestLogRead,
    IngestLogStatus,
    NodeStatus,
    NodeType,
    ResourceNodeCreate,
    ResourceNodeRead,
    UploadSessionCreate,
    UploadSessionRead,
    UploadSessionStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
HUB_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
RESOURCE_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
RECORD_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
DNA_UUID = uuid.UUID("44444444-4444-4444-4444-444444444444")
SAMPLE_SHA256 = "a" * 64
SAMPLE_SM3 = "b" * 64


# ---------------------------------------------------------------------------
# Hub Node Tests
# ---------------------------------------------------------------------------


class TestHubNodeCreate:
    """Validation tests for HubNodeCreate schema."""

    def test_valid_payload(self) -> None:
        node = HubNodeCreate(
            name="National Hub",
            api_endpoint="https://hub.nucpot.example.com/api",
            public_key="ssh-rsa AAAA...",
        )
        assert node.name == "National Hub"
        assert node.api_endpoint == "https://hub.nucpot.example.com/api"
        assert node.public_key == "ssh-rsa AAAA..."

    def test_public_key_optional(self) -> None:
        node = HubNodeCreate(
            name="Hub",
            api_endpoint="https://hub.example.com/api",
        )
        assert node.public_key is None

    def test_name_too_short(self) -> None:
        with pytest.raises(ValidationError):
            HubNodeCreate(
                name="",
                api_endpoint="https://hub.example.com/api",
            )

    def test_name_too_long(self) -> None:
        with pytest.raises(ValidationError):
            HubNodeCreate(
                name="x" * 201,
                api_endpoint="https://hub.example.com/api",
            )

    def test_api_endpoint_required(self) -> None:
        with pytest.raises(ValidationError):
            HubNodeCreate(name="Hub")

    def test_public_key_max_length(self) -> None:
        with pytest.raises(ValidationError):
            HubNodeCreate(
                name="Hub",
                api_endpoint="https://hub.example.com/api",
                public_key="k" * 2001,
            )


class TestHubNodeRead:
    """Validation tests for HubNodeRead schema."""

    def test_from_attributes(self) -> None:
        obj = type(
            "MockHubNode",
            (),
            {
                "id": HUB_ID,
                "name": "Test Hub",
                "api_endpoint": "https://hub.example.com/api",
                "public_key": None,
                "status": "active",
                "last_heartbeat": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = HubNodeRead.model_validate(obj)
        assert schema.id == HUB_ID
        assert schema.status == "active"

    def test_optional_fields_none(self) -> None:
        obj = type(
            "MockHubNode",
            (),
            {
                "id": HUB_ID,
                "name": "Hub",
                "api_endpoint": "https://hub.example.com",
                "public_key": None,
                "status": "active",
                "last_heartbeat": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = HubNodeRead.model_validate(obj)
        assert schema.public_key is None
        assert schema.last_heartbeat is None


# ---------------------------------------------------------------------------
# Resource Node Tests
# ---------------------------------------------------------------------------


class TestResourceNodeCreate:
    """Validation tests for ResourceNodeCreate schema."""

    def test_valid_payload(self) -> None:
        node = ResourceNodeCreate(
            hub_node_id=HUB_ID,
            name="Compute Node 1",
            node_type="computing",
            api_endpoint="https://node1.example.com/api",
        )
        assert node.hub_node_id == HUB_ID
        assert node.node_type == "computing"

    def test_invalid_node_type_empty(self) -> None:
        with pytest.raises(ValidationError):
            ResourceNodeCreate(
                hub_node_id=HUB_ID,
                name="Node",
                node_type="",
                api_endpoint="https://node.example.com/api",
            )

    def test_name_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ResourceNodeCreate(
                hub_node_id=HUB_ID,
                name="n" * 201,
                node_type="storage",
                api_endpoint="https://node.example.com/api",
            )


class TestResourceNodeRead:
    """Validation tests for ResourceNodeRead schema."""

    def test_from_attributes(self) -> None:
        obj = type(
            "MockResourceNode",
            (),
            {
                "id": RESOURCE_ID,
                "hub_node_id": HUB_ID,
                "name": "Node 1",
                "node_type": "storage",
                "api_endpoint": "https://node1.example.com/api",
                "public_key": None,
                "status": "active",
                "last_heartbeat": NOW,
                "offline_since": None,
                "sync_watermark": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = ResourceNodeRead.model_validate(obj)
        assert schema.hub_node_id == HUB_ID
        assert schema.sync_watermark is None

    def test_all_optional_datetimes_populated(self) -> None:
        obj = type(
            "MockResourceNode",
            (),
            {
                "id": RESOURCE_ID,
                "hub_node_id": HUB_ID,
                "name": "Node 1",
                "node_type": "storage",
                "api_endpoint": "https://node1.example.com/api",
                "public_key": "key",
                "status": "inactive",
                "last_heartbeat": NOW,
                "offline_since": NOW,
                "sync_watermark": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = ResourceNodeRead.model_validate(obj)
        assert schema.offline_since == NOW
        assert schema.sync_watermark == NOW


# ---------------------------------------------------------------------------
# Data DNA Tests
# ---------------------------------------------------------------------------


class TestDataDnaCreate:
    """Validation tests for DataDnaCreate schema."""

    def test_valid_payload(self) -> None:
        dna = DataDnaCreate(
            record_type="material",
            record_id=RECORD_ID,
            dna_uuid=DNA_UUID,
            sha256_hash=SAMPLE_SHA256,
        )
        assert dna.record_type == "material"
        assert dna.sm3_hash is None

    def test_with_sm3_hash(self) -> None:
        dna = DataDnaCreate(
            record_type="property",
            record_id=RECORD_ID,
            dna_uuid=DNA_UUID,
            sha256_hash=SAMPLE_SHA256,
            sm3_hash=SAMPLE_SM3,
        )
        assert dna.sm3_hash == SAMPLE_SM3

    def test_sha256_wrong_length(self) -> None:
        with pytest.raises(ValidationError):
            DataDnaCreate(
                record_type="material",
                record_id=RECORD_ID,
                dna_uuid=DNA_UUID,
                sha256_hash="too-short",
            )

    def test_sha256_too_long(self) -> None:
        with pytest.raises(ValidationError):
            DataDnaCreate(
                record_type="material",
                record_id=RECORD_ID,
                dna_uuid=DNA_UUID,
                sha256_hash="a" * 65,
            )

    def test_sm3_too_long(self) -> None:
        with pytest.raises(ValidationError):
            DataDnaCreate(
                record_type="material",
                record_id=RECORD_ID,
                dna_uuid=DNA_UUID,
                sha256_hash=SAMPLE_SHA256,
                sm3_hash="b" * 65,
            )

    def test_record_type_required(self) -> None:
        with pytest.raises(ValidationError):
            DataDnaCreate(
                record_id=RECORD_ID,
                dna_uuid=DNA_UUID,
                sha256_hash=SAMPLE_SHA256,
            )


class TestDataDnaRead:
    """Validation tests for DataDnaRead schema."""

    def test_from_attributes(self) -> None:
        obj = type(
            "MockDataDna",
            (),
            {
                "id": uuid.uuid4(),
                "record_type": "material",
                "record_id": RECORD_ID,
                "dna_uuid": DNA_UUID,
                "sha256_hash": SAMPLE_SHA256,
                "sm3_hash": SAMPLE_SM3,
                "created_at": NOW,
            },
        )()
        schema = DataDnaRead.model_validate(obj)
        assert schema.record_type == "material"
        assert schema.sm3_hash == SAMPLE_SM3

    def test_sm3_none(self) -> None:
        obj = type(
            "MockDataDna",
            (),
            {
                "id": uuid.uuid4(),
                "record_type": "property",
                "record_id": RECORD_ID,
                "dna_uuid": DNA_UUID,
                "sha256_hash": SAMPLE_SHA256,
                "sm3_hash": None,
                "created_at": NOW,
            },
        )()
        schema = DataDnaRead.model_validate(obj)
        assert schema.sm3_hash is None


# ---------------------------------------------------------------------------
# Classification Level Tests
# ---------------------------------------------------------------------------


class TestClassificationLevelCreate:
    """Validation tests for ClassificationLevelCreate schema."""

    def test_valid_payload(self) -> None:
        cl = ClassificationLevelCreate(
            label="非密",
            description="Unclassified public data.",
        )
        assert cl.label == "非密"
        assert cl.description == "Unclassified public data."

    def test_description_optional(self) -> None:
        cl = ClassificationLevelCreate(label="内部")
        assert cl.description is None

    def test_label_empty(self) -> None:
        with pytest.raises(ValidationError):
            ClassificationLevelCreate(label="")

    def test_label_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ClassificationLevelCreate(label="x" * 51)


class TestClassificationLevelRead:
    """Validation tests for ClassificationLevelRead schema."""

    def test_from_attributes(self) -> None:
        obj = type(
            "MockClassificationLevel",
            (),
            {
                "id": uuid.uuid4(),
                "label": "秘密",
                "description": "Secret data.",
                "created_at": NOW,
            },
        )()
        schema = ClassificationLevelRead.model_validate(obj)
        assert schema.label == "秘密"

    def test_description_none(self) -> None:
        obj = type(
            "MockClassificationLevel",
            (),
            {
                "id": uuid.uuid4(),
                "label": "非密",
                "description": None,
                "created_at": NOW,
            },
        )()
        schema = ClassificationLevelRead.model_validate(obj)
        assert schema.description is None


# ---------------------------------------------------------------------------
# Upload Session Tests
# ---------------------------------------------------------------------------


class TestUploadSessionCreate:
    """Validation tests for UploadSessionCreate schema."""

    def test_valid_payload(self) -> None:
        session = UploadSessionCreate(
            resource_node_id=RESOURCE_ID,
            file_name="UO2_density.dat",
            total_size=104857600,
            chunk_size=5242880,
            total_chunks=20,
        )
        assert session.file_name == "UO2_density.dat"
        assert session.total_chunks == 20

    def test_total_size_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UploadSessionCreate(
                resource_node_id=RESOURCE_ID,
                file_name="file.dat",
                total_size=0,
                chunk_size=1024,
                total_chunks=1,
            )

    def test_total_size_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UploadSessionCreate(
                resource_node_id=RESOURCE_ID,
                file_name="file.dat",
                total_size=-1,
                chunk_size=1024,
                total_chunks=1,
            )

    def test_chunk_size_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UploadSessionCreate(
                resource_node_id=RESOURCE_ID,
                file_name="file.dat",
                total_size=1024,
                chunk_size=0,
                total_chunks=1,
            )

    def test_total_chunks_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UploadSessionCreate(
                resource_node_id=RESOURCE_ID,
                file_name="file.dat",
                total_size=1024,
                chunk_size=1024,
                total_chunks=0,
            )

    def test_file_name_empty(self) -> None:
        with pytest.raises(ValidationError):
            UploadSessionCreate(
                resource_node_id=RESOURCE_ID,
                file_name="",
                total_size=1024,
                chunk_size=1024,
                total_chunks=1,
            )


class TestUploadSessionRead:
    """Validation tests for UploadSessionRead schema."""

    def test_from_attributes(self) -> None:
        obj = type(
            "MockUploadSession",
            (),
            {
                "id": uuid.uuid4(),
                "resource_node_id": RESOURCE_ID,
                "file_name": "data.dat",
                "total_size": 1000,
                "chunk_size": 100,
                "total_chunks": 10,
                "uploaded_chunks": 5,
                "resume_token": "token-abc",
                "sha256_full": SAMPLE_SHA256,
                "status": "in_progress",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = UploadSessionRead.model_validate(obj)
        assert schema.uploaded_chunks == 5
        assert schema.resume_token == "token-abc"

    def test_optional_fields_none(self) -> None:
        obj = type(
            "MockUploadSession",
            (),
            {
                "id": uuid.uuid4(),
                "resource_node_id": RESOURCE_ID,
                "file_name": "data.dat",
                "total_size": 1000,
                "chunk_size": 100,
                "total_chunks": 10,
                "uploaded_chunks": 0,
                "resume_token": None,
                "sha256_full": None,
                "status": "pending",
                "created_at": NOW,
                "updated_at": NOW,
            },
        )()
        schema = UploadSessionRead.model_validate(obj)
        assert schema.resume_token is None
        assert schema.sha256_full is None


# ---------------------------------------------------------------------------
# Ingest Log Tests
# ---------------------------------------------------------------------------


class TestIngestLogCreate:
    """Validation tests for IngestLogCreate schema."""

    def test_valid_upload(self) -> None:
        log = IngestLogCreate(
            resource_node_id=RESOURCE_ID,
            direction="upload",
        )
        assert log.direction == "upload"
        assert log.hub_node_id is None

    def test_with_hub_node(self) -> None:
        log = IngestLogCreate(
            resource_node_id=RESOURCE_ID,
            hub_node_id=HUB_ID,
            direction="download",
        )
        assert log.hub_node_id == HUB_ID
        assert log.direction == "download"

    def test_direction_empty(self) -> None:
        with pytest.raises(ValidationError):
            IngestLogCreate(
                resource_node_id=RESOURCE_ID,
                direction="",
            )

    def test_direction_too_long(self) -> None:
        with pytest.raises(ValidationError):
            IngestLogCreate(
                resource_node_id=RESOURCE_ID,
                direction="x" * 21,
            )


class TestIngestLogRead:
    """Validation tests for IngestLogRead schema."""

    def test_from_attributes_full(self) -> None:
        obj = type(
            "MockIngestLog",
            (),
            {
                "id": uuid.uuid4(),
                "resource_node_id": RESOURCE_ID,
                "hub_node_id": HUB_ID,
                "direction": "upload",
                "record_count": 150,
                "data_size_bytes": 2048000,
                "status": "completed",
                "error_detail": None,
                "started_at": NOW,
                "completed_at": NOW,
            },
        )()
        schema = IngestLogRead.model_validate(obj)
        assert schema.record_count == 150
        assert schema.completed_at == NOW

    def test_optional_fields_none(self) -> None:
        obj = type(
            "MockIngestLog",
            (),
            {
                "id": uuid.uuid4(),
                "resource_node_id": RESOURCE_ID,
                "hub_node_id": None,
                "direction": "upload",
                "record_count": 0,
                "data_size_bytes": 0,
                "status": "pending",
                "error_detail": None,
                "started_at": NOW,
                "completed_at": None,
            },
        )()
        schema = IngestLogRead.model_validate(obj)
        assert schema.hub_node_id is None
        assert schema.error_detail is None
        assert schema.completed_at is None


# ---------------------------------------------------------------------------
# Enum Tests
# ---------------------------------------------------------------------------


class TestEnums:
    """Tests for enum string classes."""

    def test_node_status_values(self) -> None:
        assert NodeStatus.ACTIVE == "active"
        assert NodeStatus.INACTIVE == "inactive"
        assert NodeStatus.SUSPENDED == "suspended"

    def test_node_type_values(self) -> None:
        assert NodeType.COMPUTING == "computing"
        assert NodeType.STORAGE == "storage"
        assert NodeType.OBSERVATORY == "observatory"

    def test_classification_labels(self) -> None:
        assert ClassificationLabel.UNCLASSIFIED == "非密"
        assert ClassificationLabel.INTERNAL == "内部"
        assert ClassificationLabel.SECRET == "秘密"

    def test_upload_session_status_values(self) -> None:
        assert UploadSessionStatus.PENDING == "pending"
        assert UploadSessionStatus.IN_PROGRESS == "in_progress"
        assert UploadSessionStatus.COMPLETED == "completed"
        assert UploadSessionStatus.FAILED == "failed"

    def test_ingest_direction_values(self) -> None:
        assert IngestDirection.UPLOAD == "upload"
        assert IngestDirection.DOWNLOAD == "download"

    def test_ingest_log_status_values(self) -> None:
        assert IngestLogStatus.PENDING == "pending"
        assert IngestLogStatus.IN_PROGRESS == "in_progress"
        assert IngestLogStatus.COMPLETED == "completed"
        assert IngestLogStatus.FAILED == "failed"
