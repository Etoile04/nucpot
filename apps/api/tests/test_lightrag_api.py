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
        """Should return error when LightRAG service fails."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=LightRAGClientError("Query failed"))

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                payload = {"query": "test query"}
                response = await ac.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False


class TestQueryErrorPropagation:
    """NFM-3407 — Path A (auth'd) error propagation contract.

    The LightRAG ``/query`` endpoint sits behind ``require_editor``, so
    upstream diagnostic detail is allowed to surface to the caller.

    Contract (AC-1, AC-2, AC-4):
      * error string contains exception type + message — ConnectTimeout,
        ReadTimeout, and HTTP 500 are mutually distinguishable
      * every request carries a UUID4 ``request_id`` that appears in
        both the server log and the response payload (cross-reference
        key)
      * logger call uses ``exc_info=True`` so the operator gets the
        real traceback
    """

    @pytest.mark.asyncio
    async def test_query_connect_error_message_contains_type_and_request_id(
        self, caplog
    ) -> None:
        """ConnectTimeout surfaces as a distinguishable error string with request_id."""
        import re

        from nfm_db.services.lightrag_client import LightRAGClientError

        exc = LightRAGClientError(
            "LightRAG query failed: [ConnectTimeout] Connection refused",
            original_type="ConnectTimeout",
            original_message="Connection refused",
        )

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=exc)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                with caplog.at_level("ERROR", logger="nfm_db.api.v1.lightrag"):
                    response = await ac.post(
                        "/api/v1/lightrag/query", json={"query": "test query"}
                    )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        # AC-1: error string must contain the exception type for distinguishability.
        assert "ConnectTimeout" in data["error"]
        # AC-2: response payload carries a request_id matching a UUID4.
        assert data.get("request_id") is not None
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            data["request_id"],
        ), f"request_id is not UUID4: {data['request_id']!r}"
        # AC-4: logger was called with exc_info so operator gets the traceback.
        err_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert err_records, "expected an ERROR-level log record"
        assert any(r.exc_info is not None for r in err_records), (
            "logger.error must be called with exc_info=True for operator debugging"
        )
        # request_id should appear in the log so log↔response correlation works.
        assert any(data["request_id"] in r.getMessage() for r in err_records)

    @pytest.mark.asyncio
    async def test_query_read_timeout_distinguishable_string(self) -> None:
        """ReadTimeout produces a different error string from ConnectError."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        exc = LightRAGClientError(
            "LightRAG query failed: [ReadTimeout] Timed out",
            original_type="ReadTimeout",
            original_message="Timed out",
        )

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=exc)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/lightrag/query", json={"query": "test query"}
                )

        data = response.json()
        assert data["success"] is False
        assert "ReadTimeout" in data["error"]
        # Distinguishability: must NOT look like ConnectError.
        assert "ConnectError" not in data["error"]

    @pytest.mark.asyncio
    async def test_query_http_500_includes_status_and_type(self) -> None:
        """HTTP 500 surfaces with both status code and exception type."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        # Format mirrors what the client raises on a real HTTPStatusError
        # (NFM-3407 enrichment template — see lightrag_client.py:266).
        exc = LightRAGClientError(
            "LightRAG query failed: [HTTPStatusError] HTTP 500 - upstream body",
            original_type="HTTPStatusError",
            original_message="Server Error",
            response_body="upstream body",
            status_code=500,
        )

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=exc)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/lightrag/query", json={"query": "test query"}
                )

        data = response.json()
        assert data["success"] is False
        assert "HTTPStatusError" in data["error"]
        # Path A is auth'd — full upstream detail allowed in the error string.
        assert "500" in data["error"]

    @pytest.mark.asyncio
    async def test_query_error_includes_unique_request_id_per_request(self) -> None:
        """Each request gets a fresh UUID4 request_id (not a shared constant)."""
        from nfm_db.services.lightrag_client import LightRAGClientError

        exc = LightRAGClientError(
            "LightRAG query failed: [ConnectTimeout]",
            original_type="ConnectTimeout",
            original_message="Connection refused",
        )

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.query = AsyncMock(side_effect=exc)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                r1 = await ac.post(
                    "/api/v1/lightrag/query", json={"query": "test query"}
                )
                r2 = await ac.post(
                    "/api/v1/lightrag/query", json={"query": "test query"}
                )

        rid1 = r1.json().get("request_id")
        rid2 = r2.json().get("request_id")
        assert rid1 is not None and rid2 is not None
        assert rid1 != rid2, "request_id must be unique per request"


class TestIngestErrorPropagation:
    """NFM-3407 — Path A (auth'd) ingest error propagation contract."""

    @pytest.mark.asyncio
    async def test_ingest_error_message_contains_type_and_request_id(
        self, caplog
    ) -> None:
        """Ingest failures carry exception type + request_id in both response and log."""
        import re

        from nfm_db.services.lightrag_client import LightRAGClientError

        exc = LightRAGClientError(
            "LightRAG ingest failed: [ConnectTimeout] Connection refused",
            original_type="ConnectTimeout",
            original_message="Connection refused",
        )

        with patch("nfm_db.api.v1.lightrag.LightRAGClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.ingest = AsyncMock(side_effect=exc)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                with caplog.at_level("ERROR", logger="nfm_db.api.v1.lightrag"):
                    response = await ac.post(
                        "/api/v1/lightrag/ingest",
                        json={"text": "some content"},
                    )

        data = response.json()
        assert data["success"] is False
        assert "ConnectTimeout" in data["error"]
        assert data.get("request_id") is not None
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            data["request_id"],
        )
        err_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert err_records
        assert any(r.exc_info is not None for r in err_records)
