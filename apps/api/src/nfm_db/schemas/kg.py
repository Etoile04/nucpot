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
