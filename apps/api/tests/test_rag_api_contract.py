"""Contract validation tests for RAG API response shapes (NFM-1848).

Validates that the actual API response field names and types match the
canonical contract defined in both:
  - Backend:  apps/api/src/nfm_db/schemas/lightrag.py
  - Frontend: apps/web/src/lib/rag-contract.ts

LightRAG client calls are mocked. These tests ensure that any future
schema drift between frontend and backend is caught by CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

# Canonical field names — single source of truth.
# MUST match QueryResponse in lightrag.py AND RagContractQueryResponse
# in rag-contract.ts. If you change these, update all three locations.
QUERY_RESPONSE_FIELDS = {"response", "references", "entities", "relationships"}
QUERY_RESPONSE_FIELD_TYPES = {
    "response": str,
    "references": list,
    "entities": list,
    "relationships": list,
}

INGEST_RESPONSE_FIELDS = {"status", "message", "track_id"}
INGEST_RESPONSE_FIELD_TYPES = {
    "status": str,
    "message": str,
    "track_id": (str, type(None)),
}

HEALTH_RESPONSE_FIELDS = {
    "status",
    "error",
    "active_provider",
    "fallback_active",
    "lightrag_version",
}
HEALTH_RESPONSE_FIELD_TYPES = {
    "status": str,
    "error": (str, type(None)),
    "active_provider": str,
    "fallback_active": bool,
    "lightrag_version": (str, type(None)),
}


def _assert_contract_shape(
    data: dict,
    required_fields: set[str],
    field_types: dict[str, tuple[type, ...]],
) -> None:
    """Assert that a response payload matches the canonical contract shape."""
    # All required fields must be present.
    missing = required_fields - set(data.keys())
    assert not missing, f"Missing contract fields: {missing}"

    # No unknown top-level fields (prevents silent drift).
    extra = set(data.keys()) - required_fields
    assert not extra, f"Unexpected contract fields: {extra}"

    # Each field must have the correct type.
    for field, expected_types in field_types.items():
        value = data[field]
        assert isinstance(value, expected_types), (
            f"Field '{field}' has type {type(value).__name__}, "
            f"expected one of {[t.__name__ for t in expected_types]}"
        )


# ===========================================================================
# Query contract — POST /api/v1/lightrag/query
# ===========================================================================


@pytest.mark.asyncio
async def test_query_response_contract_shape(async_client: AsyncClient) -> None:
    """POST /lightrag/query response matches the canonical QueryResponse contract."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Test answer",
            "references": [
                {
                    "reference_id": "1",
                    "file_path": "/docs/test.pdf",
                    "content": "Excerpt text",
                }
            ],
            "entities": [{"name": "UO2", "type": "Material"}],
            "relationships": [
                {"source": "UO2", "target": "fuel", "description": "is a"}
            ],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={
                "query": "What is UO2?",
                "mode": "mix",
                "include_references": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    _assert_contract_shape(
        body["data"], QUERY_RESPONSE_FIELDS, QUERY_RESPONSE_FIELD_TYPES
    )


@pytest.mark.asyncio
async def test_query_response_contract_empty_collections(
    async_client: AsyncClient,
) -> None:
    """POST /lightrag/query with empty collections still satisfies the contract."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "Minimal answer",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "Simple question"},
        )

    assert response.status_code == 200
    body = response.json()
    _assert_contract_shape(
        body["data"], QUERY_RESPONSE_FIELDS, QUERY_RESPONSE_FIELD_TYPES
    )


# ===========================================================================
# Ingest contract — POST /api/v1/lightrag/ingest
# ===========================================================================


@pytest.mark.asyncio
async def test_ingest_response_contract_shape(async_client: AsyncClient) -> None:
    """POST /lightrag/ingest response matches the canonical IngestResponse contract."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.ingest.return_value = {
            "status": "success",
            "message": "Document ingested",
            "track_id": "track-abc-123",
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/ingest",
            json={"text": "Test document content."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    _assert_contract_shape(
        body["data"], INGEST_RESPONSE_FIELDS, INGEST_RESPONSE_FIELD_TYPES
    )


# ===========================================================================
# Health contract — GET /api/v1/lightrag/health
# ===========================================================================


@pytest.mark.asyncio
async def test_health_response_contract_shape(async_client: AsyncClient) -> None:
    """GET /lightrag/health response matches the canonical HealthResponse contract."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.return_value = True
        mock_get_client.return_value = mock_client

        response = await async_client.get("/api/v1/lightrag/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    _assert_contract_shape(
        body["data"], HEALTH_RESPONSE_FIELDS, HEALTH_RESPONSE_FIELD_TYPES
    )


@pytest.mark.asyncio
async def test_health_response_contract_unhealthy(
    async_client: AsyncClient,
) -> None:
    """GET /lightrag/health unhealthy still satisfies the contract."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.health_check.return_value = False
        mock_get_client.return_value = mock_client

        response = await async_client.get("/api/v1/lightrag/health")

    assert response.status_code == 200
    body = response.json()
    _assert_contract_shape(
        body["data"], HEALTH_RESPONSE_FIELDS, HEALTH_RESPONSE_FIELD_TYPES
    )
    assert body["data"]["fallback_active"] is True
