"""DTOs / frozen dataclasses for hub API responses."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    """Type of resource node in the 1+N architecture."""

    COMPUTING = "computing"
    STORAGE = "storage"
    OBSERVATORY = "observatory"


@dataclass(frozen=True)
class ResourceNodeRegistration:
    """Result of a successful POST /api/v1/hub/nodes/register."""

    node_id: uuid.UUID
    hub_node_id: uuid.UUID
    name: str
    node_type: NodeType
    api_endpoint: str
    status: str
    public_key: str | None = None
    last_heartbeat: Any = None
    offline_since: Any = None
    sync_watermark: Any = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "ResourceNodeRegistration":
        """Parse a ResourceNodeRead-shaped payload from the hub."""
        return cls(
            node_id=uuid.UUID(payload["id"]),
            hub_node_id=uuid.UUID(payload["hub_node_id"]),
            name=str(payload["name"]),
            node_type=NodeType(payload["node_type"]),
            api_endpoint=str(payload["api_endpoint"]),
            status=str(payload["status"]),
            public_key=payload.get("public_key"),
            last_heartbeat=payload.get("last_heartbeat"),
            offline_since=payload.get("offline_since"),
            sync_watermark=payload.get("sync_watermark"),
        )


@dataclass(frozen=True)
class HeartbeatResponse:
    """Result of a successful POST /api/v1/hub/nodes/{id}/heartbeat."""

    node_id: uuid.UUID
    ack: bool
    received_at: Any = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "HeartbeatResponse":
        """Parse a heartbeat response payload."""
        return cls(
            node_id=uuid.UUID(payload["node_id"]),
            ack=bool(payload.get("ack", True)),
            received_at=payload.get("received_at"),
        )


@dataclass(frozen=True)
class UploadResult:
    """Result of a successful POST /api/v1/hub/nodes/{id}/upload (init)."""

    session_id: uuid.UUID
    resource_node_id: uuid.UUID
    file_name: str
    total_size: int
    chunk_size: int
    total_chunks: int
    uploaded_chunks: int = 0
    resume_token: str | None = None
    sha256_full: str | None = None
    status: str = "pending"

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "UploadResult":
        """Parse an UploadSessionRead payload."""
        return cls(
            session_id=uuid.UUID(payload["id"]),
            resource_node_id=uuid.UUID(payload["resource_node_id"]),
            file_name=str(payload["file_name"]),
            total_size=int(payload["total_size"]),
            chunk_size=int(payload["chunk_size"]),
            total_chunks=int(payload["total_chunks"]),
            uploaded_chunks=int(payload.get("uploaded_chunks", 0)),
            resume_token=payload.get("resume_token"),
            sha256_full=payload.get("sha256_full"),
            status=str(payload.get("status", "pending")),
        )


@dataclass(frozen=True)
class SyncStatus:
    """Result of a successful GET /api/v1/hub/nodes/{id}/sync-status."""

    node_id: uuid.UUID
    online: bool
    last_heartbeat: Any = None
    sync_watermark: Any = None
    pending_uploads: int = 0
    pending_downloads: int = 0

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "SyncStatus":
        """Parse a sync-status payload."""
        return cls(
            node_id=uuid.UUID(payload["node_id"]),
            online=bool(payload.get("online", False)),
            last_heartbeat=payload.get("last_heartbeat"),
            sync_watermark=payload.get("sync_watermark"),
            pending_uploads=int(payload.get("pending_uploads", 0)),
            pending_downloads=int(payload.get("pending_downloads", 0)),
        )


@dataclass(frozen=True)
class Credentials:
    """Bearer credentials for the hub API."""

    token: str
    extra_headers: dict[str, str] = field(default_factory=dict)

    def auth_headers(self) -> dict[str, str]:
        """Return Authorization + extra headers as a plain dict."""
        return {"Authorization": f"Bearer {self.token}", **self.extra_headers}


__all__ = [
    "Credentials",
    "HeartbeatResponse",
    "NodeType",
    "ResourceNodeRegistration",
    "SyncStatus",
    "UploadResult",
]
