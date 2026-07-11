"""Pydantic schemas for LightRAG proxy endpoints (NFM-1061).

Minimal schema definitions to support the lightrag integration tests.
Full implementation will be expanded when the proxy endpoints are built out.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel


class QueryMode(str, enum.Enum):
    """Supported LightRAG query modes."""

    NAIVE = "naive"
    LOCAL = "local"
    GLOBAL = "global"
    HYBRID = "hybrid"


class QueryResponse(BaseModel):
    """Response from a semantic query."""

    answer: str | None = None
    sources: list[dict[str, Any]] = []


class GraphQueryResponse(BaseModel):
    """Response from a Cypher graph query."""

    results: list[dict[str, Any]] = []


class HealthResponse(BaseModel):
    """Health check response from the LightRAG sidecar."""

    status: str = "ok"
    version: str | None = None
