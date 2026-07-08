"""Pydantic schemas for KG query API (NFM-858).

Request/response models for the three query modes:
property query, relation query, and path query via Apache AGE Cypher.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class KGNodeResponse(BaseModel):
    """A knowledge graph node returned in query results."""

    id: UUID
    node_type: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0

    model_config = ConfigDict(from_attributes=True)


class KGEdgeResponse(BaseModel):
    """A knowledge graph edge returned in query results."""

    id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Property Query
# ---------------------------------------------------------------------------


class PropertyQueryRequest(BaseModel):
    """Request parameters for property-based node lookup."""

    node_type: str | None = Field(
        None,
        description="Filter by node type (Material, Property, Experiment, Condition, Publication)",
    )
    label: str | None = Field(
        None,
        description="Exact or fuzzy match on node label",
    )
    property_key: str | None = Field(
        None,
        description="Key inside the node's JSON properties to match",
    )
    property_value: str | None = Field(
        None,
        description="Value to match (exact) for property_key",
    )
    fuzzy: bool = Field(
        False,
        description="Use ILIKE fuzzy matching for label search",
    )
    limit: int = Field(
        20,
        ge=1,
        le=100,
        description="Max number of results",
    )
    offset: int = Field(
        0,
        ge=0,
        description="Pagination offset",
    )


class PropertyQueryResponse(BaseModel):
    """Response envelope for property query results."""

    nodes: list[KGNodeResponse] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Relation Query
# ---------------------------------------------------------------------------


class RelationQueryRequest(BaseModel):
    """Request parameters for relation-based edge lookup."""

    source_node_id: UUID | None = Field(
        None,
        description="Find edges FROM this node",
    )
    target_node_id: UUID | None = Field(
        None,
        description="Find edges TO this node",
    )
    relation_type: str | None = Field(
        None,
        description="Filter by relation type (hasProperty, measuredIn, relatedTo, cites, hasCondition)",
    )
    direction: str = Field(
        "outgoing",
        description="Edge direction: outgoing, incoming, or both",
    )
    limit: int = Field(
        20,
        ge=1,
        le=100,
    )
    offset: int = Field(
        0,
        ge=0,
    )


class RelationQueryResponse(BaseModel):
    """Response envelope for relation query results."""

    edges: list[KGEdgeResponse] = Field(default_factory=list)
    nodes: list[KGNodeResponse] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Path Query
# ---------------------------------------------------------------------------


class PathQueryRequest(BaseModel):
    """Request parameters for multi-hop path traversal via Apache AGE."""

    source_node_id: UUID = Field(
        ...,
        description="Start node for path search",
    )
    target_node_id: UUID = Field(
        ...,
        description="Target node for path search",
    )
    max_depth: int = Field(
        3,
        ge=1,
        le=5,
        description="Maximum hop depth (1-5, default 3)",
    )
    relation_types: list[str] | None = Field(
        None,
        description="Restrict traversal to these relation types",
    )
    limit: int = Field(
        10,
        ge=1,
        le=50,
        description="Max number of distinct paths",
    )


class PathEdge(BaseModel):
    """An edge within a path result."""

    source_node_id: UUID
    target_node_id: UUID
    relation_type: str


class PathResult(BaseModel):
    """A single path from source to target."""

    nodes: list[KGNodeResponse]
    edges: list[PathEdge]
    length: int = Field(description="Number of edges in this path")


class PathQueryResponse(BaseModel):
    """Response envelope for path query results."""

    paths: list[PathResult] = Field(default_factory=list)
    total: int = 0
