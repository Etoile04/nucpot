"""Pydantic schemas for the M2 Data Submission 1+N architecture.

NFM-2019: Covers hub_nodes, resource_nodes, data_dna,
classification_level, upload_sessions, and ingest_logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeStatus(str):
    """Operational status of a hub or resource node."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class NodeType(str):
    """Type of a resource node in the 1+N architecture."""

    COMPUTING = "computing"
    STORAGE = "storage"
    OBSERVATORY = "observatory"


class ClassificationLabel(str):
    """Classification level per contract requirements."""

    UNCLASSIFIED = "非密"
    INTERNAL = "内部"
    SECRET = "秘密"


class UploadSessionStatus(str):
    """Status of a chunked upload session."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestDirection(str):
    """Direction of data flow for an ingest log entry."""

    UPLOAD = "upload"
    DOWNLOAD = "download"


class IngestLogStatus(str):
    """Status of an ingest (upload/download) operation."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Hub Node
# ---------------------------------------------------------------------------


class HubNodeRead(BaseModel):
    """Public representation of a hub node."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str = Field(description="Hub node display name.")
    api_endpoint: str = Field(description="Base URL of the hub node API.")
    public_key: str | None = None
    status: str = Field(description="Operational status.")
    last_heartbeat: datetime | None = None
    created_at: datetime
    updated_at: datetime


class HubNodeCreate(BaseModel):
    """Payload for registering a new hub node."""

    name: str = Field(min_length=1, max_length=200)
    api_endpoint: str = Field(min_length=1, max_length=500)
    public_key: str | None = Field(default=None, max_length=2000)



# ---------------------------------------------------------------------------
# Resource Node
# ---------------------------------------------------------------------------


class ResourceNodeRead(BaseModel):
    """Public representation of a resource node."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_node_id: uuid.UUID
    name: str
    node_type: str
    api_endpoint: str
    public_key: str | None = None
    status: str
    last_heartbeat: datetime | None = None
    offline_since: datetime | None = None
    sync_watermark: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ResourceNodeCreate(BaseModel):
    """Payload for registering a new resource node."""

    hub_node_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    node_type: str = Field(min_length=1, max_length=50)
    api_endpoint: str = Field(min_length=1, max_length=500)
    public_key: str | None = Field(default=None, max_length=2000)



# ---------------------------------------------------------------------------
# Data DNA
# ---------------------------------------------------------------------------


class DataDnaRead(BaseModel):
    """Public representation of a data DNA record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    record_type: str = Field(description="Type of the record (e.g. material, property).")
    record_id: uuid.UUID = Field(description="FK to the source record.")
    dna_uuid: uuid.UUID = Field(description="UUIDv4 content fingerprint.")
    sha256_hash: str = Field(description="SHA-256 hash of the record content.")
    sm3_hash: str | None = Field(default=None, description="SM3 hash (GB/T 32905).")
    created_at: datetime


class DataDnaCreate(BaseModel):
    """Payload for creating a data DNA record."""

    record_type: str = Field(min_length=1, max_length=100)
    record_id: uuid.UUID
    dna_uuid: uuid.UUID
    sha256_hash: str = Field(min_length=64, max_length=64)
    sm3_hash: str | None = Field(default=None, max_length=64)



# ---------------------------------------------------------------------------
# Classification Level
# ---------------------------------------------------------------------------


class ClassificationLevelRead(BaseModel):
    """Public representation of a classification level."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    description: str | None = None
    created_at: datetime


class ClassificationLevelCreate(BaseModel):
    """Payload for creating a classification level."""

    label: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)



# ---------------------------------------------------------------------------
# Upload Session
# ---------------------------------------------------------------------------


class UploadSessionRead(BaseModel):
    """Public representation of an upload session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_node_id: uuid.UUID
    file_name: str
    total_size: int = Field(description="Total file size in bytes.")
    chunk_size: int = Field(description="Size of each chunk in bytes.")
    total_chunks: int
    uploaded_chunks: int = Field(default=0, ge=0)
    resume_token: str | None = None
    sha256_full: str | None = Field(default=None, description="SHA-256 of the complete file.")
    status: str
    created_at: datetime
    updated_at: datetime


class UploadSessionCreate(BaseModel):
    """Payload for creating an upload session."""

    resource_node_id: uuid.UUID
    file_name: str = Field(min_length=1, max_length=500)
    total_size: int = Field(gt=0)
    chunk_size: int = Field(gt=0)
    total_chunks: int = Field(gt=0)


# ---------------------------------------------------------------------------
# Chunk Upload API (NFM-2024)
# ---------------------------------------------------------------------------


class UploadInitRequest(BaseModel):
    """Payload for POST /api/v1/upload/init."""

    resource_node_id: uuid.UUID
    classification_level_id: uuid.UUID = Field(
        description="FK to classification_levels.id (security label, §3.1.2)."
    )
    file_name: str = Field(min_length=1, max_length=500)
    total_size: int = Field(gt=0, description="Total file size in bytes.")
    sha256_full: str = Field(
        min_length=64,
        max_length=64,
        description="Expected SHA-256 hex digest of the complete file.",
    )
    chunk_size: int = Field(
        default=5 * 1024 * 1024,
        gt=0,
        le=100 * 1024 * 1024,
        description="Chunk size in bytes (default 5MB, max 100MB).",
    )


class UploadInitResponse(BaseModel):
    """Response from POST /api/v1/upload/init."""

    session_id: uuid.UUID
    resume_token: str
    chunk_size: int
    total_chunks: int
    status: str


class ChunkUploadRequest(BaseModel):
    """Payload for POST /api/v1/upload/chunk."""

    resume_token: str = Field(min_length=1, max_length=64)
    chunk_index: int = Field(ge=0, description="Zero-based chunk index.")
    sha256_chunk: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hex digest of this chunk's data.",
    )


class ChunkUploadResponse(BaseModel):
    """Response from POST /api/v1/upload/chunk."""

    session_id: uuid.UUID
    chunk_index: int
    uploaded_chunks: int
    total_chunks: int
    status: str


class UploadCompleteRequest(BaseModel):
    """Payload for POST /api/v1/upload/complete."""

    resume_token: str = Field(min_length=1, max_length=64)


class UploadCompleteResponse(BaseModel):
    """Response from POST /api/v1/upload/complete."""

    session_id: uuid.UUID
    status: str
    sha256_full: str | None = None
    error: str | None = None


class UploadResumeRequest(BaseModel):
    """Payload for POST /api/v1/upload/resume."""

    resume_token: str = Field(min_length=1, max_length=64)


class UploadResumeResponse(BaseModel):
    """Response from POST /api/v1/upload/resume."""

    session_id: uuid.UUID
    status: str
    total_chunks: int
    uploaded_chunks: int
    missing_chunks: list[int] = Field(
        description="Zero-based indices of chunks not yet received."
    )



# ---------------------------------------------------------------------------
# Ingest Log
# ---------------------------------------------------------------------------


class IngestLogRead(BaseModel):
    """Public representation of an ingest log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_node_id: uuid.UUID
    hub_node_id: uuid.UUID | None = None
    direction: str
    record_count: int = Field(default=0, ge=0)
    data_size_bytes: int = Field(default=0, ge=0)
    status: str
    error_detail: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class IngestLogCreate(BaseModel):
    """Payload for creating an ingest log entry."""

    resource_node_id: uuid.UUID
    hub_node_id: uuid.UUID | None = None
    direction: str = Field(min_length=1, max_length=20)



__all__ = [
    "ChunkUploadRequest",
    "ChunkUploadResponse",
    "ClassificationLabel",
    "ClassificationLevelCreate",
    "ClassificationLevelRead",
    "DataDnaCreate",
    "DataDnaRead",
    "HubNodeCreate",
    "HubNodeRead",
    "IngestDirection",
    "IngestLogCreate",
    "IngestLogRead",
    "IngestLogStatus",
    "NodeStatus",
    "NodeType",
    "ResourceNodeCreate",
    "ResourceNodeRead",
    "UploadCompleteRequest",
    "UploadCompleteResponse",
    "UploadInitRequest",
    "UploadInitResponse",
    "UploadResumeRequest",
    "UploadResumeResponse",
    "UploadSessionCreate",
    "UploadSessionRead",
    "UploadSessionStatus",
]
