"""Response schemas for KG search and graph endpoints (NFM-1166, NFM-1270).

Provides Pydantic models for GET /api/v1/kg/search and
GET /api/v1/kg/graph responses.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class KGSearchItem(BaseModel):
    """A single KG node returned by the search endpoint."""

    id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    status: str = Field(min_length=1)
    source_id: str | None = None


class KGSearchResponse(BaseModel):
    """Paginated response for GET /api/v1/kg/search."""

    items: list[KGSearchItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Graph endpoint schemas (NFM-1270)
# ---------------------------------------------------------------------------


class KGGraphRequest(BaseModel):
    """Query params for GET /api/v1/kg/graph."""

    nodeId: str = Field(min_length=1, description="Focal node: UUID, 'type:label', or label")
    depth: int = Field(default=2, ge=1, le=3)
    status: str = Field(default="active", pattern="^(active|all)$")


class KGGraphNode(BaseModel):
    id: str
    label: str
    type: str  # mapped from node_type
    properties: dict[str, Any]  # contains __depth
    status: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_id: str | None = None


class KGGraphEdge(BaseModel):
    source: str
    target: str
    type: str  # mapped from relation_type
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class KGGraphResponse(BaseModel):
    focal: dict[str, Any]  # {"id": str, "depth": 0}
    nodes: list[KGGraphNode]
    edges: list[KGGraphEdge]
