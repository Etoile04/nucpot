"""Pydantic schemas for LightRAG sidecar integration (NFM-862).

Request/response models for the LightRAG document ingestion,
semantic query, and health check endpoints.

LightRAG API surface (default port 9621):
  POST /documents/text  — ingest text document
  POST /query           — semantic query against the knowledge graph
  GET  /health          — service health check
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QueryMode(str, Enum):
    """Supported LightRAG query modes."""

    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"
    MIX = "mix"
    NAIVE = "naive"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """Request body for POST /api/v1/lightrag/ingest.

    Wraps LightRAG's POST /documents/text endpoint.
    """

    text: str = Field(
        ...,
        min_length=1,
        description="Document text content to ingest into the knowledge graph",
    )
    file_source: str | None = Field(
        None,
        description="Optional source identifier for the document",
    )

    @field_validator("text", mode="after")
    @classmethod
    def strip_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text must not be blank or whitespace-only")
        return stripped


class IngestResponse(BaseModel):
    """Response from the LightRAG ingestion pipeline.

    Maps the track_id for monitoring async processing status.
    """

    status: str = Field(
        description="Operation status (success, pending, error)",
    )
    message: str = Field(
        default="",
        description="Human-readable status message",
    )
    track_id: str | None = Field(
        None,
        description="LightRAG track ID for monitoring processing status",
    )

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Request body for POST /api/v1/lightrag/query.

    Wraps LightRAG's POST /query endpoint.
    """

    query: str = Field(
        ...,
        min_length=1,
        description="Natural language query against the knowledge graph",
    )
    mode: QueryMode = Field(
        QueryMode.MIX,
        description="Query retrieval mode (local, global, hybrid, mix, naive)",
    )
    include_references: bool = Field(
        False,
        description="Whether to include source references in the response",
    )

    @field_validator("query", mode="after")
    @classmethod
    def strip_query(cls, v: str) -> str:
        return v.strip()


class QueryResponse(BaseModel):
    """Response from the LightRAG semantic query.

    Maps LightRAG's POST /query response with structured KG data.
    """

    response: str = Field(
        description="Generated answer to the query",
    )
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Source references from the knowledge graph",
    )
    entities: list[dict[str, Any]] = Field(
        default_factory=list,
        description="KG entities related to the query",
    )
    relationships: list[dict[str, Any]] = Field(
        default_factory=list,
        description="KG relationships related to the query",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response from the LightRAG health check endpoint."""

    status: str = Field(
        description="Service health status (healthy, unhealthy, degraded)",
    )
    error: str | None = Field(
        None,
        description="Error message if service is unhealthy",
    )
    active_provider: str = Field(
        "lightrag",
        description="Name of the currently active RAG provider",
    )
    fallback_active: bool = Field(
        False,
        description="Whether the rule-based fallback provider is active",
    )
    lightrag_version: str | None = Field(
        None,
        description="Pinned LightRAG version from config",
    )
