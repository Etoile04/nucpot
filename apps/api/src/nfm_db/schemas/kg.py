"""Knowledge Graph Pydantic schemas (NFM-838 Batch 2).

Request/response schemas for KG nodes, edges, review queue, and queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# --- KG Node ---

class KGNodeCreate(BaseModel):
    """Schema for creating a new KG node."""

    entity_type: str = Field(
        min_length=1,
        max_length=100,
        description="Entity type (material, property, value, etc.)",
    )
    name: str = Field(min_length=1, max_length=1000)
    label: str | None = Field(default=None, max_length=200)
    description: str | None = None
    properties: dict | None = Field(
        default=None,
        description="Additional ontology-specific properties",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Extraction confidence 0.0-1.0",
    )
    source_document_id: uuid.UUID | None = None
    figure_id: uuid.UUID | None = None
    extraction_method: str | None = Field(default=None, max_length=50)


class KGNodeUpdate(BaseModel):
    """Schema for updating an existing KG node (partial)."""

    name: str | None = Field(default=None, max_length=1000)
    label: str | None = Field(default=None, max_length=200)
    description: str | None = None
    properties: dict | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


class KGNodeResponse(BaseModel):
    """Full KG node response including database-generated fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    name: str
    label: str | None = None
    description: str | None = None
    properties: dict | None = None
    confidence_score: float
    source_document_id: uuid.UUID | None = None
    figure_id: uuid.UUID | None = None
    extraction_method: str | None = None
    created_at: datetime
    updated_at: datetime


# --- KG Edge ---

class KGEdgeCreate(BaseModel):
    """Schema for creating a new KG edge."""

    source_id: uuid.UUID
    target_id: uuid.UUID
    relation_type: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=300)
    properties: dict | None = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_document_id: uuid.UUID | None = None
    extraction_method: str | None = Field(default=None, max_length=50)


class KGEdgeUpdate(BaseModel):
    """Schema for updating an existing KG edge (partial)."""

    label: str | None = Field(default=None, max_length=300)
    properties: dict | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


class KGEdgeResponse(BaseModel):
    """Full KG edge response including database-generated fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    relation_type: str
    label: str | None = None
    properties: dict | None = None
    confidence_score: float
    source_document_id: uuid.UUID | None = None
    extraction_method: str | None = None
    created_at: datetime
    updated_at: datetime


# --- Review Queue ---

ReviewStatus = Literal["pending", "approved", "rejected", "skipped"]
ReviewItemType = Literal["node", "edge"]


class KGReviewItemResponse(BaseModel):
    """Review queue item response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_type: str
    item_id: uuid.UUID
    confidence_score: float
    review_status: str
    reviewer_id: uuid.UUID | None = None
    review_note: str | None = None
    reviewed_at: str | None = None
    original_data: dict | None = None
    created_at: datetime
    updated_at: datetime


class KGReviewAction(BaseModel):
    """Schema for approving or rejecting a review queue item."""

    action: Literal["approve", "reject", "skip"]
    review_note: str | None = None


class KGReviewListResponse(BaseModel):
    """Paginated review queue response."""

    items: list[KGReviewItemResponse]
    total: int
    page: int
    limit: int


# --- Query API ---

class PropertyQueryResult(BaseModel):
    """Property query result: material_id + property_type → values with sources."""

    node_id: uuid.UUID
    entity_type: str
    name: str
    properties: dict | None = None
    confidence_score: float
    source_document_id: uuid.UUID | None = None


class PropertyQueryResponse(BaseModel):
    """Response envelope for property queries."""

    material_id: uuid.UUID
    property_type: str | None = None
    values: list[PropertyQueryResult]
    total: int


class RelationQueryResponse(BaseModel):
    """Response envelope for relation queries."""

    entity_id: uuid.UUID
    entity_name: str
    depth: int
    related: list[dict]
    total_relations: int


class PathQueryResponse(BaseModel):
    """Response envelope for path queries (shortest path)."""

    source_id: uuid.UUID
    target_id: uuid.UUID
    path: list[dict]
    path_length: int
    found: bool = True
