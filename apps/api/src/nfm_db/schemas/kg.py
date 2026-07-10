"""Placeholder KG schemas — minimal stubs to unblock test imports (NFM-724).

Full implementation pending; these allow conftest.py → main.py → kg.py
import chain to resolve without errors.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class NodeResponse(BaseModel):
    """KG node response."""

    id: UUID
    node_type: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class NodeSummary(BaseModel):
    """Brief node summary for list views."""

    id: UUID
    node_type: str
    label: str


class EdgeResponse(BaseModel):
    """KG edge response."""

    id: UUID
    source_id: UUID
    target_id: UUID
    relation_type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class RelationDirection(str, enum.Enum):
    """Direction filter for relation queries."""

    OUT = "outgoing"
    IN = "incoming"
    BOTH = "both"


class PathQueryRequest(BaseModel):
    """Request body for path query."""

    source_id: UUID
    target_id: UUID
    max_depth: int = Field(default=5, ge=1, le=10)


class PathStep(BaseModel):
    """One step in a path result."""

    node_id: UUID
    node_type: str
    label: str
    edge_type: str | None = None


class PathResponse(BaseModel):
    """Path query result."""

    source_id: UUID
    target_id: UUID
    steps: list[PathStep] = Field(default_factory=list)


class IngestRequest(BaseModel):
    """Request body for incremental KG ingest."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class IngestResponse(BaseModel):
    """Response for initiated ingest."""

    batch_id: str
    status: str = "pending"


class IngestStatus(str, enum.Enum):
    """Status enum for ingest batches."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestPollResponse(BaseModel):
    """Poll response for ingest status."""

    batch_id: str
    status: str
    nodes_processed: int = 0
    edges_processed: int = 0
    errors: list[str] = Field(default_factory=list)


class ReviewActionRequest(BaseModel):
    """Request body for approve/reject review action."""

    comment: str | None = None


class ReviewItemResponse(BaseModel):
    """Single review queue item."""

    id: UUID
    item_type: str
    label: str
    status: str
    created_at: datetime
