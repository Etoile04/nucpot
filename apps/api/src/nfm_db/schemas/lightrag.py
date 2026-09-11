"""Pydantic schemas for LightRAG sidecar integration (NFM-862, NFM-1848).

Request/response models for the LightRAG document ingestion,
semantic query, and health check endpoints.

LightRAG API surface (default port 9621):
  POST /documents/text  — ingest text document
  POST /query           — semantic query against the knowledge graph
  GET  /health          — service health check

Cross-language contract:
  These Pydantic models are the authoritative backend definition.
  The TypeScript mirror lives in apps/web/src/lib/rag-contract.ts.
  If you rename/add/remove a field here, update the TypeScript file too.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

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

    NFM-4539 RAG-B: ``fallback`` carries the transparent-degradation
    envelope so the frontend can render the §3.2 / AC-4 badge without
    inspecting internals.  ``fallback.used=false`` is the steady state;
    ``fallback.used=true`` with ``kind='iliKE'`` indicates the
    semantic-search sidecar timed out and the answer was rescued via the
    ILIKE full-text path.
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
    fallback: FallbackInfo = Field(
        default_factory=lambda: FallbackInfo(used=False),
        description=(
            "Transparent degradation envelope (NFM-4539 §3.2 / AC-4). "
            "Set ``used=true`` only when the response was rescued via "
            "ILIKE after the LightRAG sidecar exceeded its budget."
        ),
    )


class FallbackInfo(BaseModel):
    """Fallback envelope (NFM-4539 RAG-B / §3.2 / AC-4 + NFM-4734 §3).

    ``reason`` is the first-class machine-readable code that lets the
    UI distinguish a *semantic timeout* (LightRAG sidecar exceeded its
    10s budget) from a *semantic empty* (LightRAG answered with zero
    references — KG coverage gap) from a clean response (``none``).

    NFM-4734 §3 / AC-2: the previous "silent fallback" surface masked
    outages behind a single ``used=true`` flag.  Operators could not
    tell whether to fix the embedding index (empty case) or scale the
    sidecar (timeout case).  This enum closes that gap.
    """

    used: bool = Field(
        False,
        description="True iff the response was rescued via a fallback path.",
    )
    kind: str | None = Field(
        None,
        description=(
            "Fallback kind; currently ``'iliKE'`` for ILIKE rescue. "
            "``None`` when no fallback fired."
        ),
    )
    reason: Literal["none", "semantic_timeout", "semantic_empty", "provider_error"] = Field(
        "none",
        description=(
            "NFM-4734 first-class reason code: "
            "'none' (steady state), 'semantic_timeout' (LightRAG exceeded its "
            "budget and was rescued), 'semantic_empty' (LightRAG answered with "
            "zero references — KG coverage gap), 'provider_error' (defensive "
            "safety net path)."
        ),
    )
    original_error: str | None = Field(
        None,
        description="Error message that triggered the fallback (if any).",
    )

    model_config = ConfigDict(from_attributes=True)


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


# ---------------------------------------------------------------------------
# Metrics (NFM-4539 RAG-E / AC-8)
# ---------------------------------------------------------------------------


class TierP95(BaseModel):
    """P95 latency for one tier over the AC-8 window.

    ``p95_ms`` is ``None`` when the sample size is below the 5-row floor
    so the dashboard renders an honest "insufficient data" badge instead
    of a misleading percentile from a tiny sample.
    """

    p95_ms: float | None = Field(
        None,
        description="P95 latency in milliseconds; None when sample size is below the floor.",
    )
    sample_size: int = Field(
        0,
        description="Number of access-log rows in the window that fed this tier.",
    )
    target_ms: float = Field(
        description="SLA target in milliseconds (Tier-1 < 1s; Tier-2 < 30s pre-NFM-4525, < 10s after).",
    )
    meets_sla: bool = Field(
        False,
        description="True iff ``p95_ms`` is not None and is at or below ``target_ms``.",
    )


class MetricsResponse(BaseModel):
    """Dashboard payload for NFM-4539 RAG-E / AC-8.

    Drives the weekly RAG quality dashboard.  Three literature totals
    (completed / indexed / diff) plus two tier latencies over a rolling
    7-day window.
    """

    lit_completed_total: int = Field(
        description="Number of DataSource rows with parse_status='completed'.",
    )
    lit_indexed_total: int = Field(
        description=(
            "Number of completed literature rows that the daily "
            "rag_audit_index_coverage confirms are in the LightRAG index "
            "(most recent run_date). "
            "None when the audit has never run."
        ),
    )
    lit_indexed_source: str = Field(
        "rag_index_audit_log",
        description="Provenance of ``lit_indexed_total``.",
    )
    lit_diff_count: int = Field(
        description="completed - indexed; the RAG-D backlog.",
    )
    tier_1_p95: TierP95 = Field(
        description="Cached/hot path latency (was_cached=true).",
    )
    tier_2_p95: TierP95 = Field(
        description="Fresh semantic-search latency (was_cached=false AND was_fallback=false).",
    )
    window_days: int = Field(
        7,
        description="Rolling window in days for the latency tiers.",
    )
    generated_at: datetime = Field(
        description="UTC timestamp at which this payload was computed.",
    )
    # NFM-4734 §3 / AC-3: Tier-2 P95 踩线告警。Operator can pin a single
    # dashboard panel and the boolean flips the row red without any
    # extra Prometheus rule.  Computed in ``compute_rag_metrics`` as
    # ``tier_2_p95.p95_ms is not None and not tier_2_p95.meets_sla``.
    tier_2_breach: bool = Field(
        False,
        description=(
            "NFM-4734 SLA-breach signal: True iff Tier-2 P95 has at "
            "least SAMPLE_FLOOR samples AND exceeds the 10s target."
        ),
    )

    model_config = ConfigDict(from_attributes=True)
