"""Integration tests for LightRAG sidecar API endpoints.

Tests all 3 LightRAG endpoints (NFM-862, NFM-1223):
  - GET  /api/v1/lightrag/health  — check LightRAG service availability
  - POST /api/v1/lightrag/ingest   — ingest document text into the KG
  - POST /api/v1/lightrag/query    — semantic query against the KG

LightRAG client calls are mocked via unittest.mock.patch to avoid
requiring a running LightRAG sidecar during tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import httpx
from httpx import AsyncClient

from nfm_db.services.lightrag_client import LightRAGClientError


# ===========================================================================
# Health — GET /api/v1/lightrag/health
# ===========================================================================


@pytest.mark.asyncio
async def test_health_check_healthy(async_client: AsyncClient) -> None:
    """GET /lightrag/health returns healthy when service is available."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.return_value = True
        mock_get_client.return_value = mock_client

        response = await async_client.get("/api/v1/lightrag/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "healthy"
    assert body["data"]["error"] is None
    assert body["data"]["active_provider"] == "lightrag"
    assert body["data"]["fallback_active"] is False
    assert body["data"]["lightrag_version"] is not None


@pytest.mark.asyncio
async def test_health_check_unhealthy_response(
    async_client: AsyncClient,
) -> None:
    """GET /lightrag/health returns unhealthy when service responds false."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.return_value = False
        mock_get_client.return_value = mock_client

        response = await async_client.get("/api/v1/lightrag/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "unhealthy"
    assert body["data"]["active_provider"] == "rule-based-fallback"
    assert body["data"]["fallback_active"] is True


@pytest.mark.asyncio
async def test_health_check_http_error(async_client: AsyncClient) -> None:
    """GET /lightrag/health returns unhealthy when httpx raises HTTPError."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.side_effect = httpx.HTTPError("Connection refused")
        mock_get_client.return_value = mock_client

        response = await async_client.get("/api/v1/lightrag/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "unhealthy"
    assert "Connection refused" in body["data"]["error"]
    assert body["data"]["fallback_active"] is True


# ===========================================================================
# Ingest — POST /api/v1/lightrag/ingest
# ===========================================================================


@pytest.mark.asyncio
async def test_ingest_success(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest ingests a document and returns track_id."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.ingest.return_value = {
            "status": "success",
            "message": "Document ingested",
            "track_id": "track-123",
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/ingest",
            json={
                "text": "UO2 is a nuclear fuel material with density 10.97 g/cm3.",
                "file_source": "paper_001.pdf",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "success"
    assert body["data"]["track_id"] == "track-123"
    mock_client.ingest.assert_awaited_once_with(
        text="UO2 is a nuclear fuel material with density 10.97 g/cm3.",
        file_source="paper_001.pdf",
    )


@pytest.mark.asyncio
async def test_ingest_without_file_source(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest without optional file_source."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.ingest.return_value = {
            "status": "success",
            "message": "",
            "track_id": "track-456",
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/ingest",
            json={"text": "Thermal conductivity of UO2 at 300K."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["track_id"] == "track-456"
    mock_client.ingest.assert_awaited_once_with(
        text="Thermal conductivity of UO2 at 300K.",
        file_source=None,
    )


@pytest.mark.asyncio
async def test_ingest_client_error(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest returns success=False on LightRAGClientError."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.ingest.side_effect = LightRAGClientError(
            "LightRAG ingest failed: HTTP 500 - Internal Server Error",
        )
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/ingest",
            json={"text": "Some material data"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "LightRAG service error" in body["error"]


@pytest.mark.asyncio
async def test_ingest_unexpected_error(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest returns success=False on unexpected exception."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.ingest.side_effect = RuntimeError("Unexpected crash")
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/ingest",
            json={"text": "More material data"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "Ingest failed" in body["error"]


@pytest.mark.asyncio
async def test_ingest_missing_text(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest without text field returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"file_source": "doc.pdf"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_empty_text(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest with empty text returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"text": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_whitespace_only_text(
    async_client: AsyncClient,
) -> None:
    """POST /lightrag/ingest with whitespace-only text returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/ingest",
        json={"text": "   \n\t  "},
    )

    assert response.status_code == 422


# ===========================================================================
# Query — POST /api/v1/lightrag/query
# ===========================================================================


@pytest.mark.asyncio
async def test_query_success(async_client: AsyncClient) -> None:
    """POST /lightrag/query returns a generated answer with references."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "UO2 has a thermal conductivity of approximately 3.5 W/mK at 300K.",
            "references": [
                {
                    "source": "Finkelstein 2001",
                    "page": 42,
                },
            ],
            "entities": [
                {"name": "UO2", "type": "Material"},
            ],
            "relationships": [
                {"source": "UO2", "target": "thermal_conductivity"},
            ],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={
                "query": "What is the thermal conductivity of UO2?",
                "mode": "mix",
                "include_references": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "thermal conductivity" in body["data"]["response"]
    assert len(body["data"]["references"]) == 1
    assert body["data"]["entities"][0]["name"] == "UO2"
    assert len(body["data"]["relationships"]) == 1

    mock_client.query.assert_awaited_once_with(
        query="What is the thermal conductivity of UO2?",
        mode="mix",
        include_references=True,
    )


@pytest.mark.asyncio
async def test_query_default_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with default mode (mix) and no references."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "UN is used as an advanced nuclear fuel.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "What is UN used for?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["references"] == []
    mock_client.query.assert_awaited_once_with(
        query="What is UN used for?",
        mode="mix",
        include_references=False,
    )


@pytest.mark.asyncio
async def test_query_local_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with mode=local."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Local answer about specific entity.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Tell me about UO2", "mode": "local"},
        )

    assert response.status_code == 200
    mock_client.query.assert_awaited_once()
    call_kwargs = mock_client.query.call_args[1]
    assert call_kwargs["mode"] == "local"


@pytest.mark.asyncio
async def test_query_global_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with mode=global."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Global summary answer.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Summarize nuclear fuel properties", "mode": "global"},
        )

    assert response.status_code == 200
    call_kwargs = mock_client.query.call_args[1]
    assert call_kwargs["mode"] == "global"


@pytest.mark.asyncio
async def test_query_hybrid_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with mode=hybrid."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Hybrid answer.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Compare UO2 and UN", "mode": "hybrid"},
        )

    assert response.status_code == 200
    call_kwargs = mock_client.query.call_args[1]
    assert call_kwargs["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_query_naive_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with mode=naive."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Naive retrieval answer.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Basic info about fuel", "mode": "naive"},
        )

    assert response.status_code == 200
    call_kwargs = mock_client.query.call_args[1]
    assert call_kwargs["mode"] == "naive"


@pytest.mark.asyncio
async def test_query_client_error(async_client: AsyncClient) -> None:
    """POST /lightrag/query returns success=False on LightRAGClientError."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.side_effect = LightRAGClientError(
            "LightRAG query failed: HTTP 503 - Service Unavailable",
        )
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "What is UO2?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "LightRAG service error" in body["error"]


@pytest.mark.asyncio
async def test_query_unexpected_error(async_client: AsyncClient) -> None:
    """POST /lightrag/query returns success=False on unexpected exception."""
    with patch(
        "nfm_db.api.v1.lightrag._get_client",
    ) as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.side_effect = RuntimeError("Timeout")
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Tell me about fuels"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "Query failed" in body["error"]


@pytest.mark.asyncio
async def test_query_missing_query_field(async_client: AsyncClient) -> None:
    """POST /lightrag/query without query field returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/query",
        json={"mode": "mix"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_query_empty_query(async_client: AsyncClient) -> None:
    """POST /lightrag/query with empty query returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/query",
        json={"query": ""},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_query_invalid_mode(async_client: AsyncClient) -> None:
    """POST /lightrag/query with invalid mode returns 422."""
    response = await async_client.post(
        "/api/v1/lightrag/query",
        json={"query": "What is UO2?", "mode": "invalid_mode"},
    )

    assert response.status_code == 422
