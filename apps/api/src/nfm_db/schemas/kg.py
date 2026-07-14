"""Response schemas for KG search endpoints (NFM-1166).

Provides Pydantic models for GET /api/v1/kg/search responses.
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


class SemanticQueryResponse(BaseModel):
    """Response from the LightRAG semantic query bridge.

    Returned when ``mode=lightrag`` is used on the KG search endpoint.
    """

    response: str = Field(default="")
    references: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = Field(default="")
    fallback: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Node detail & relation edge schemas (NFM-1099)
# ---------------------------------------------------------------------------


class KGNodeDetail(KGSearchItem):
    """Detail response for GET /api/v1/kg/nodes/{node_type}/{node_id}.

    Extends the lightweight search item with the originating source_id so
    callers can navigate back to the literature/material source.
    """

    source_id: str | None = None


class RelationEdgeItem(BaseModel):
    """A single edge in the relation listing for a KG node."""

    id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    properties: dict[str, Any] = Field(default_factory=dict)
    source_node: KGSearchItem
    target_node: KGSearchItem


class KGRelationsResponse(BaseModel):
    """Paginated response for GET /api/v1/kg/nodes/{node_id}/relations."""

    items: list[RelationEdgeItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Graph visualization schemas (NFM-1093)
# ---------------------------------------------------------------------------


class GraphNodeItem(BaseModel):
    """A node formatted for force-directed graph visualization.

    Maps KGNode fields to GraphCanvas GraphNode type expectations.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    type: str = Field(
        default="default",
        description="Mapped GraphNodeType: material, property, entity, default",
    )
    size: float = Field(default=1.0)
    color: str = Field(default="")
    child_count: int = Field(default=0)


class GraphEdgeItem(BaseModel):
    """An edge formatted for force-directed graph visualization."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    label: str = Field(default="")
    type: str = Field(default="")


class KGGraphResponse(BaseModel):
    """Response for GET /api/v1/kg/graph — nodes + edges for visualization."""

    nodes: list[GraphNodeItem] = Field(default_factory=list)
    edges: list[GraphEdgeItem] = Field(default_factory=list)
    total_nodes: int = Field(ge=0)
    total_edges: int = Field(ge=0)
