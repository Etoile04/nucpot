"""Tests for LightRAG API endpoints (NFM-862).

RED phase — these tests define the expected behavior of the LightRAG router.
Uses httpx.AsyncClient with ASGITransport; LightRAGClient is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_lightrag_client():
    """Patch LightRAGClient to avoid real HTTP calls."""
    with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.health_check = AsyncMock(return_value=True)
        mock_instance.ingest = AsyncMock(
            return_value={
                "status": "success",
                "message": "Text inserted successfully",
                "track_id": "track-test-123",
            }
        )
        mock_instance.query = AsyncMock(
            return_value={
                "response": "UO2 is a ceramic nuclear fuel material.",
                "references": [
                    {
                        "reference_id": "1",
                        "file_path": "/docs/fuel.pdf",
                        "content": ["UO2 properties chunk."],
                    }
                ],
            }
        )
        yield mock_instance


@pytest.fixture
async def client(mock_lightrag_client):
    """HTTP test client with mocked LightRAG service."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /api/v1/lightrag/health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for the LightRAG health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_healthy(self, client: AsyncClient) -> None:
        """Should return healthy status when LightRAG is available."""
        response = await client.get("/api/v1/lightrag/health")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_unhealthy(self, client: AsyncClient) -> None:
        """Should return unhealthy status when LightRAG is unavailable."""
        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.health_check = AsyncMock(return_value=False)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.get("/api/v1/lightrag/health")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "unhealthy"
        assert data["data"]["error"] is not None


# ---------------------------------------------------------------------------
# POST /api/v1/lightrag/ingest
# ---------------------------------------------------------------------------


class TestIngestEndpoint:
    """Tests for the document ingestion endpoint."""

    @pytest.mark.asyncio
    async def test_successful_ingest(self, client: AsyncClient) -> None:
        """Should ingest text and return track_id."""
        payload = {
            "text": "UO2 is a nuclear fuel material.",
            "file_source": "handbook.pdf",
        }
        response = await client.post("/api/v1/lightrag/ingest", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["track_id"] == "track-test-123"

    @pytest.mark.asyncio
    async def test_ingest_minimal(self, client: AsyncClient) -> None:
        """Should accept ingest with just text."""
        payload = {"text": "Some document text."}
        response = await client.post("/api/v1/lightrag/ingest", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_ingest_empty_text_rejected(self, client: AsyncClient) -> None:
        """Should reject empty text."""
        payload = {"text": ""}
        response = await client.post("/api/v1/lightrag/ingest", json=payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ingest_missing_text_rejected(self, client: AsyncClient) -> None:
        """Should reject request without text field."""
        payload = {"file_source": "doc.pdf"}
        response = await client.post("/api/v1/lightrag/ingest", json=payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ingest_service_error(self, client: AsyncClient) -> None:
        """Should return error when LightRAG service fails."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.ingest = AsyncMock(side_effect=LightRAGClientError("Service unavailable"))

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                payload = {"text": "test content"}
                response = await ac.post("/api/v1/lightrag/ingest", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "LightRAG" in data["error"]


# ---------------------------------------------------------------------------


class TestQueryEndpoint:
    """Tests for the semantic query endpoint."""

    @pytest.mark.asyncio
    async def test_successful_query(self, client: AsyncClient) -> None:
        """Should query LightRAG and return response."""
        payload = {
            "query": "What are the properties of UO2?",
            "mode": "mix",
        }
        response = await client.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "UO2" in data["data"]["response"]
        assert len(data["data"]["references"]) == 1

    @pytest.mark.asyncio
    async def test_query_minimal(self, client: AsyncClient) -> None:
        """Should accept query with defaults."""
        payload = {"query": "test query"}
        response = await client.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_query_empty_rejected(self, client: AsyncClient) -> None:
        """Should reject empty query."""
        payload = {"query": ""}
        response = await client.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_query_missing_rejected(self, client: AsyncClient) -> None:
        """Should reject request without query field."""
        payload = {"mode": "global"}
        response = await client.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_query_invalid_mode(self, client: AsyncClient) -> None:
        """Should reject invalid query mode."""
        payload = {"query": "test", "mode": "invalid"}
        response = await client.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_query_service_error(self, client: AsyncClient) -> None:
        """LightRAG service error → AC-4 transparent ILIKE fallback.

        NFM-4539 RAG-B AC-4: a ``LightRAGClientError`` raised inside the
        sidecar must surface as a degraded-but-successful response
        (``success=True``, ``fallback.used=True``, ``kind='iliKE'``) with
        the rule-based fallback's references, NOT a hard ``success=False``
        error — that's the §3.2 / AC-4 contract.  This test parallels
        ``tests/api/v1/test_lightrag.py::test_query_client_error`` and was
        missed during the initial RAG-B rollout (caught by Code Review).
        """
        from nfm_db.services.lightrag_client import LightRAGClientError
        from nfm_db.services.rag_provider import RAGQueryResult

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=LightRAGClientError("Query failed"))
            transport = ASGITransport(app=app)
            with patch(
                "nfm_db.services.rag_provider.RuleBasedFallbackProvider.query",
                new_callable=AsyncMock,
            ) as mock_fallback_query:
                mock_fallback_query.return_value = RAGQueryResult(
                    response="rule-based: stub for test_query_service_error",
                    references=[
                        {
                            "source_type": "data_source",
                            "source_id": "ds-fallback",
                            "score": 0.42,
                        },
                    ],
                    provider="rule-based-fallback",
                    fallback=True,
                )

                async with AsyncClient(transport=transport, base_url="http://test") as ac:
                    payload = {"query": "test query"}
                    response = await ac.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        # AC-4 contract: the user gets a transparent degraded answer, not a hard error.
        assert data["success"] is True
        assert data["data"]["fallback"]["used"] is True
        assert data["data"]["fallback"]["kind"] == "iliKE"
        # The rescue path actually ran — references are populated.
        assert len(data["data"]["references"]) == 1
        assert data["data"]["references"][0]["source_type"] == "data_source"
        mock_fallback_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_with_null_list_fields_succeeds(self, client: AsyncClient) -> None:
        """NFM-4522: LightRAG may return `references/entities/relationships: null`
        (JSON null) when include_references=false or in certain modes.

        The wrapper must coerce null → [] so the Pydantic QueryResponse
        validation passes and the response returns success:true with empty
        list fields rather than raising 422 from the boundary.
        """
        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            # LightRAG sidecar returns explicit nulls (not missing keys)
            mock_instance.query = AsyncMock(
                return_value={
                    "response": "Some answer text.",
                    "references": None,
                    "entities": None,
                    "relationships": None,
                }
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                payload = {"query": "test query", "mode": "hybrid"}
                response = await ac.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True, f"Expected success, got error: {data.get('error')}"
        assert data["data"]["response"] == "Some answer text."
        assert data["data"]["references"] == []
        assert data["data"]["entities"] == []
        assert data["data"]["relationships"] == []

    @pytest.mark.asyncio
    async def test_query_with_null_response_succeeds(self, client: AsyncClient) -> None:
        """NFM-4522: defensive — if LightRAG ever returns `response: null`,
        wrapper coerces to "" instead of passing None to QueryResponse."""
        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(
                return_value={
                    "response": None,
                    "references": None,
                    "entities": None,
                    "relationships": None,
                }
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                payload = {"query": "test query"}
                response = await ac.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["response"] == ""
        assert data["data"]["references"] == []
