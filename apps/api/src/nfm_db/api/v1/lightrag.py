"""LightRAG sidecar integration endpoints (NFM-862).

Provides:
  GET  /lightrag/health  — check LightRAG service availability
  POST /lightrag/ingest   — ingest document text into the knowledge graph
  POST /lightrag/query    — semantic query against the knowledge graph
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter

from nfm_db.config import get_settings
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.lightrag import (
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
)
from nfm_db.services.lightrag_client import (
    LightRAGClient,
    LightRAGClientError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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
    "/lightrag/health",
    response_model=ApiResponse[HealthResponse],
)
async def health_check() -> ApiResponse[HealthResponse]:
    """Check LightRAG sidecar service availability."""
    client = _get_client()
    try:
        healthy = await client.health_check()
        if healthy:
            return ApiResponse(
                success=True,
                data=HealthResponse(status="healthy"),
            )
        return ApiResponse(
            success=True,
            data=HealthResponse(
                status="unhealthy",
                error="LightRAG service is not responding",
            ),
        )
    except httpx.HTTPError as exc:
        logger.error("LightRAG health check error: %s", exc)
        return ApiResponse(
            success=True,
            data=HealthResponse(
                status="unhealthy",
                error=str(exc),
            ),
        )
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post(
    "/lightrag/ingest",
    response_model=ApiResponse[IngestResponse],
)
async def ingest_document(
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
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@router.post(
    "/lightrag/query",
    response_model=ApiResponse[QueryResponse],
)
async def query_knowledge_graph(
    request: QueryRequest,
) -> ApiResponse[QueryResponse]:
    """Query the LightRAG knowledge graph.

    Accepts a natural language query and returns a generated answer
    with optional source references from the knowledge graph.
    """
    client = _get_client()
    try:
        result = await client.query(
            query=request.query,
            mode=request.mode.value,
            include_references=request.include_references,
        )
        return ApiResponse(
            success=True,
            data=QueryResponse(
                response=result.get("response", ""),
                references=result.get("references", []),
                entities=result.get("entities", []),
                relationships=result.get("relationships", []),
            ),
        )
    except LightRAGClientError as exc:
        logger.error("LightRAG query error: %s", exc)
        return ApiResponse(
            success=False,
            error=f"LightRAG service error: {exc}",
        )
    except Exception as exc:
        logger.error("Unexpected query error: %s", exc)
        return ApiResponse(
            success=False,
            error=f"Query failed: {exc}",
        )
    finally:
        await client.close()
