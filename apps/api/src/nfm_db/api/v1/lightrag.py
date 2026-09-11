"""LightRAG sidecar integration endpoints (NFM-862, NFM-1223, NFM-4539).

Provides:
  GET  /lightrag/health  — check LightRAG service availability
  POST /lightrag/ingest   — ingest document text into the knowledge graph
  POST /lightrag/query    — semantic query against the knowledge graph

NFM-4539 RAG-A: /lightrag/query is now open to anonymous traffic (the
``require_editor`` dependency is removed).  Per-IP protection is delegated
to a slowapi ``@limiter.limit`` decorator driven by the
``NFM_RAG_QUERY_RATE_LIMIT`` env var (default ``5/minute``).  The global
``RATE_LIMIT_DEFAULT`` / ``RATE_LIMIT_BURST`` remain the upstream safety
net; slowapi applies the per-route limit *before* the application limits.

NFM-4681: the 5/minute quota is enforced through slowapi's storage
backend declared in ``apps/api/src/nfm_db/middleware/rate_limit.py``.
In prod / preview that backend is the shared Redis container (DB 2)
wired in ``docker-compose.prod.yml`` so all four uvicorn workers see
one counter; staging keeps ``memory://`` because its Dockerfile pins a
single worker.  The IP key is the real visitor IP because
``ProxyHeadersMiddleware`` rewrites ``scope['client']`` from
``X-Forwarded-For`` before slowapi reads it.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.config import LIGHTRAG_VERSION, get_settings
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.rag_access_log import RagAccessLog
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.lightrag import (
    FallbackInfo,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    MetricsResponse,
    QueryRequest,
    QueryResponse,
)
from nfm_db.services.lightrag_client import (
    LightRAGClient,
    LightRAGClientError,
)
from nfm_db.services.rag_metrics import compute_rag_metrics
from nfm_db.services.rag_provider import RAGProviderSelector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["LightRAG"])

# NFM-4539 RAG-A: per-route, per-IP slowapi limit.  Configurable via the
# ``NFM_RAG_QUERY_RATE_LIMIT`` env var so operators can dial up during
# staging load tests without redeploying code.  Default ``5/minute`` is the
# spec's "minimal wall against scrapers" baseline; the existing global
# 100/minute + 20/second burst still applies as a wider net.
RAG_QUERY_RATE_LIMIT = os.environ.get("NFM_RAG_QUERY_RATE_LIMIT", "5/minute")


def _get_client() -> LightRAGClient:
    """Create a LightRAG client from application settings."""
    settings = get_settings()
    return LightRAGClient(
        host=settings.lightrag_host,
        port=settings.lightrag_port,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=ApiResponse[HealthResponse],
    summary="LightRAG服务健康检查",
    description="检查LightRAG sidecar服务可用性，返回版本和回退状态。\n\nCheck LightRAG sidecar service availability and fallback status.",
)
async def health_check() -> ApiResponse[HealthResponse]:
    """Check LightRAG sidecar service availability.

    Returns the pinned LightRAG version and indicates whether
    the rule-based fallback is currently active.
    """
    client = _get_client()
    try:
        healthy = await client.health_check()
        if healthy:
            return ApiResponse(
                success=True,
                data=HealthResponse(
                    status="healthy",
                    lightrag_version=LIGHTRAG_VERSION,
                    active_provider="lightrag",
                    fallback_active=False,
                ),
            )
        return ApiResponse(
            success=True,
            data=HealthResponse(
                status="unhealthy",
                error="LightRAG service is not responding",
                lightrag_version=LIGHTRAG_VERSION,
                active_provider="rule-based-fallback",
                fallback_active=True,
            ),
        )
    except Exception as exc:
        logger.error("LightRAG health check error: %s", exc)
        return ApiResponse(
            success=True,
            data=HealthResponse(
                status="unhealthy",
                error=str(exc),
                lightrag_version=LIGHTRAG_VERSION,
                active_provider="rule-based-fallback",
                fallback_active=True,
            ),
        )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post(
    "/ingest",
    response_model=ApiResponse[IngestResponse],
    summary="文档摄入到知识图谱",
    description="将文本文档发送到LightRAG知识图谱进行摄入处理。\n\nIngest a text document into the LightRAG knowledge graph.",
)
async def ingest_document(
    _current_user: Annotated[User, Depends(require_editor)],
    request: IngestRequest,
) -> ApiResponse[IngestResponse]:
    """Ingest a text document into the LightRAG knowledge graph.

    The document text is sent to the LightRAG sidecar for processing.
    Returns a track_id for monitoring async ingestion status.
    """
    client = _get_client()
    try:
        result = await client.ingest(
            text=request.text,
            file_source=request.file_source,
        )
        return ApiResponse(
            success=True,
            data=IngestResponse(
                status=result.get("status", "success"),
                message=result.get("message", ""),
                track_id=result.get("track_id"),
            ),
        )
    except LightRAGClientError as exc:
        logger.error("LightRAG ingest error: %s", exc)
        return ApiResponse(
            success=False,
            error=f"LightRAG service error: {exc}",
        )
    except Exception as exc:
        logger.error("Unexpected ingest error: %s", exc)
        return ApiResponse(
            success=False,
            error=f"Ingest failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=ApiResponse[QueryResponse],
    summary="知识图谱语义查询",
    description="接受自然语言查询，返回生成答案及可选的来源引用。\n\nAccept a natural language query and return a generated answer with optional source references.",
)
@limiter.limit(RAG_QUERY_RATE_LIMIT)
async def query_knowledge_graph(
    request: Request,
    payload: QueryRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[QueryResponse]:
    """Query the LightRAG knowledge graph.

    Accepts a natural language query and returns a generated answer
    with optional source references from the knowledge graph.

    NFM-4539 RAG-A: anonymous-accessible.  Per-IP protection is delegated
    to the slowapi decorator above (``NFM_RAG_QUERY_RATE_LIMIT`` /
    default ``5/minute``).

    NFM-4539 RAG-B: every call writes a ``RagAccessLog`` row carrying
    the AC-7 telemetry, and the response carries a ``fallback`` envelope
    so the frontend can render the §3.2 / AC-4 badge without inspecting
    internals.

    NFM-4539 RAG-B AC-4 fix: route through ``RAGProviderSelector`` so
    timeout-style failures transparently fall through to
    ``RuleBasedFallbackProvider`` (the real ``ts_rank``/``tsquery`` path
    over ``data_sources`` / ``materials`` / ``kg_nodes``).  ``fallback.used``
    is now set only when the ILIKE rescue path actually returned
    substantive results — the badge can no longer lie.
    """
    start = time.monotonic()
    was_fallback = False
    was_cached = False
    error_message: str | None = None
    result_count = 0
    fallback_reason: str = "none"
    api_response: ApiResponse[QueryResponse]

    try:
        # NFM-4539 AC-4: route through the selector so timeout-style
        # failures fall through to the rule-based fallback automatically.
        # The selector's ``query()`` already catches ``LightRAGClientError``
        # and returns a ``RAGQueryResult(fallback=True)`` carrying the
        # ILIKE-rescued references, so we no longer have to maintain a
        # separate ``except LightRAGClientError`` branch that pretends to
        # do the rescue.
        selector = RAGProviderSelector(
            lightrag_client=_get_client(),
            db_session=db,
        )
        # NFM-4539 RAG-B: forward ``mode`` / ``include_references`` so the
        # sidecar's per-request knobs still apply when we route through
        # the selector.  ``limit=10`` is the rule-based fallback's cap on
        # how many ``ts_rank``-ordered references to return.
        rag_result = await selector.query(
            query=payload.query,
            mode=payload.mode.value,
            include_references=payload.include_references,
            limit=10,
        )
        was_fallback = rag_result.fallback
        was_cached = rag_result.was_cached
        # NFM-4734 §3 / AC-2: project the selector's reason code onto
        # the response envelope.  ``"none"`` for the steady state so the
        # frontend can short-circuit the badge logic cheaply.
        fallback_reason = rag_result.fallback_reason

        # NFM-4522: ``RAGQueryResult`` already coerces missing-key to ``[]``
        # via ``field(default_factory=list)``, but a downstream provider
        # could in principle return ``None``; ``or []`` is the belt-and-
        # braces boundary guard.
        response_text = rag_result.response or ""
        references = rag_result.references or []
        entities = rag_result.entities or []
        relationships = rag_result.relationships or []
        result_count = len(references)
        api_response = ApiResponse(
            success=True,
            data=QueryResponse(
                response=response_text,
                references=references,
                entities=entities,
                relationships=relationships,
                fallback=FallbackInfo(
                    used=was_fallback,
                    kind="iliKE" if was_fallback else None,
                    reason=fallback_reason,
                    original_error=rag_result.original_error,
                ),
            ),
        )
    except LightRAGClientError as exc:
        # Defensive safety net: ``RAGProviderSelector.query()`` already
        # absorbs ``LightRAGClientError`` internally and falls through to
        # ``RuleBasedFallbackProvider``.  If we reach this branch, the
        # selector itself is misconfigured (e.g. ``db_session`` couldn't
        # even reach the database).  Surface the failure honestly with
        # ``original_error`` populated so the §3.2 badge still renders
        # rather than masking the outage.
        logger.error("LightRAGClientError leaked past RAGProviderSelector: %s", exc)
        was_fallback = True
        fallback_reason = "provider_error"
        error_message = str(exc)
        api_response = ApiResponse(
            success=True,
            data=QueryResponse(
                response="",
                references=[],
                entities=[],
                relationships=[],
                fallback=FallbackInfo(
                    used=True,
                    kind="iliKE",
                    reason=fallback_reason,
                    original_error=error_message,
                ),
            ),
        )
    except Exception as exc:
        logger.error("Unexpected query error: %s", exc)
        was_fallback = False
        error_message = str(exc)
        api_response = ApiResponse(
            success=False,
            error=f"Query failed: {exc}",
        )
    finally:
        # NFM-4539 RAG-B AC-7: every /query invocation persists a
        # ``rag_access_log`` row carrying mode, was_fallback, was_cached,
        # query_kind, result_count, time_total.  Failures to persist are
        # logged but never fail the user-facing request — the access log
        # is telemetry, not a hard dependency.
        elapsed = time.monotonic() - start
        query_kind = "ilike" if was_fallback else "semantic"
        try:
            db.add(
                RagAccessLog(
                    mode=payload.mode.value,
                    was_fallback=was_fallback,
                    was_cached=was_cached,
                    query_kind=query_kind,
                    result_count=result_count,
                    time_total=elapsed,
                    error_message=error_message,
                ),
            )
            await db.commit()
        except Exception:  # pragma: no cover - telemetry must never 500 the API
            logger.debug("rag_access_log persistence failed", exc_info=True)
            try:
                await db.rollback()
            except Exception:
                logger.debug("rag_access_log rollback failed", exc_info=True)
    # NFM-4734 §3 / AC-2: stamp the reason code on the response header
    # so operators can grep access logs without unpacking JSON.  We only
    # stamp when the reason is non-steady-state ("none") so the success
    # path stays header-clean.
    if fallback_reason and fallback_reason != "none":
        response.headers["X-RAG-Fallback-Reason"] = fallback_reason
    return api_response


# ---------------------------------------------------------------------------
# Metrics (NFM-4539 RAG-E / AC-8)
# ---------------------------------------------------------------------------


@router.get(
    "/metrics",
    response_model=ApiResponse[MetricsResponse],
    summary="RAG质量看板指标",
    description=(
        "Expose NFM-4539 AC-8 dashboard metrics: "
        "``lit_completed_total`` / ``lit_indexed_total`` / ``lit_diff_count`` "
        "plus Tier-1 (cached) and Tier-2 (fresh semantic) P95 latency over a "
        "rolling 7-day window.  Anonymous-readable — the data is aggregate "
        "telemetry, not user-identifying."
    ),
)
async def rag_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[MetricsResponse]:
    """AC-8 dashboard payload (NFM-4539 RAG-E)."""
    try:
        payload = await compute_rag_metrics(db)
    except Exception as exc:  # pragma: no cover - dashboard must never 500
        logger.error("rag_metrics computation failed: %s", exc, exc_info=True)
        # Mirror the health endpoint's "degraded but ok" posture so a
        # downstream telemetry outage does not cascade into a 500 for
        # operators pinging the dashboard.
        return ApiResponse(success=False, error=f"metrics computation failed: {exc}")
    return ApiResponse(success=True, data=payload)
