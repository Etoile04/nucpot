"""Phase 2 query response schemas for AGE-backed ontology endpoints (NFM-832).

Response models for the four new endpoints:
  - GET  /api/v1/ontology/node/{node_id}  -> NodeNeighborsResponse
  - GET  /api/v1/ontology/search         -> SearchResponse
  - GET  /api/v1/ontology/path            -> ShortestPathResponse
  - POST /api/v1/ontology/sync            -> SyncResponse
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _coerce_uuid_to_str(v: Any) -> Any:
    """Convert UUID objects to strings for JSON serialization."""
    return str(v) if isinstance(v, uuid.UUID) else v


def _coerce_aliases(v: Any) -> list[str]:
    """Convert aliases from str or None to list[str]."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v else []
    return v

UuidStr = Annotated[str, BeforeValidator(_coerce_uuid_to_str)]

AliasesList = Annotated[list[str], BeforeValidator(_coerce_aliases)]

PropertiesDict = Annotated[
    dict[str, Any],
    BeforeValidator(lambda v: v if isinstance(v, dict) else {}),
]

# ---------------------------------------------------------------------------
# Node + Neighbors  (GET /ontology/node/{node_id})
# ---------------------------------------------------------------------------


class NodeDetail(BaseModel):
    """Detailed view of a single knowledge-graph node."""

    model_config = ConfigDict(from_attributes=True)

    id: UuidStr = Field(min_length=1)
    node_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: AliasesList = Field(default_factory=list)
    properties: PropertiesDict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    corpus_id: str | None = None


class NeighborEdge(BaseModel):
    """Edge connecting the focal node to a neighbor."""

    relation_type: str = Field(min_length=1)
    direction: Literal["outgoing", "incoming"]
    confidence: float = Field(ge=0.0, le=1.0)


class NeighborResult(BaseModel):
    """A single neighbor node paired with the edge that connects it."""

    node: NodeDetail
    edge: NeighborEdge


class NodeNeighborsResponse(BaseModel):
    """Response for GET /ontology/node/{node_id}."""

    node: NodeDetail
    neighbors: list[NeighborResult] = Field(default_factory=list)
    total_neighbors: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Fuzzy search  (GET /ontology/search)
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """One match from a fuzzy ontology search."""

    id: UuidStr = Field(min_length=1)
    node_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    match_field: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)


class SearchResponse(BaseModel):
    """Response for GET /ontology/search."""

    results: list[SearchResult] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Shortest path  (GET /ontology/path)
# ---------------------------------------------------------------------------


class PathNode(BaseModel):
    """Lightweight node representation inside a path."""

    model_config = ConfigDict(from_attributes=True)

    id: UuidStr = Field(min_length=1)
    label: str = Field(min_length=1)
    node_type: str = Field(min_length=1)


class PathEdge(BaseModel):
    """Lightweight edge representation inside a path."""

    relation_type: str = Field(min_length=1)


class PathStep(BaseModel):
    """One hop in a shortest-path result: (edge, destination node)."""

    node: PathNode
    edge: PathEdge


class ShortestPathResponse(BaseModel):
    """Response for GET /ontology/path."""

    model_config = ConfigDict(populate_by_name=True)

    from_: PathNode = Field(alias="from")
    to: PathNode = Field(alias="to")
    path: list[PathStep] = Field(default_factory=list)
    length: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Sync / rebuild  (POST /ontology/sync)
# ---------------------------------------------------------------------------


class SyncResponse(BaseModel):
    """Response for POST /ontology/sync."""

    corpus_id: str = Field(min_length=1)
    graph_name: str = Field(min_length=1)
    nodes_synced: int = Field(ge=0)
    edges_synced: int = Field(ge=0)
    duration_ms: float = Field(ge=0.0)


# Back-compat aliases used by the ontology router
NodeResponse = NodeDetail
SearchResultItem = SearchResult
