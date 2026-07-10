"""Phase 2 query response schemas for AGE-backed ontology endpoints (NFM-832).

Response models for the four new endpoints:
  - GET  /api/v1/ontology/node/{node_id}  -> NodeNeighborsResponse
  - GET  /api/v1/ontology/search         -> SearchResponse
  - GET  /api/v1/ontology/path            -> ShortestPathResponse
  - POST /api/v1/ontology/sync            -> SyncResponse
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Node + Neighbors  (GET /ontology/node/{node_id})
# ---------------------------------------------------------------------------


class NodeDetail(BaseModel):
    """Detailed view of a single knowledge-graph node."""

    id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
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

    id: str = Field(min_length=1)
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

    id: str = Field(min_length=1)
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
