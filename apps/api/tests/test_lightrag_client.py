"""Tests for LightRAG async HTTP client (NFM-862).

RED phase — these tests define the expected behavior of the LightRAGClient.
httpx.AsyncClient is mocked to avoid requiring a real LightRAG server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Client import guard
# ---------------------------------------------------------------------------


def test_lightrag_client_importable() -> None:
    """The lightrag client module should be importable."""
    from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
        LightRAGClient,
        LightRAGClientError,
        is_lightrag_configured,
    )

    assert LightRAGClient is not None
    assert LightRAGClientError is not None
    assert callable(is_lightrag_configured)


# ---------------------------------------------------------------------------
# is_lightrag_configured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    """Tests for the is_lightrag_configured helper."""

    def test_configured_when_host_set(self) -> None:
        """Should return True when LIGHTRAG_HOST env var is set."""
        from nfm_db.services.lightrag_client import is_lightrag_configured  # type: ignore[import-untyped]

        with patch.dict("os.environ", {"LIGHTRAG_HOST": "localhost"}):
            assert is_lightrag_configured() is True

    def test_not_configured_when_host_missing(self) -> None:
        """Should return False when LIGHTRAG_HOST env var is not set."""
        from nfm_db.services.lightrag_client import is_lightrag_configured  # type: ignore[import-untyped]

        with patch.dict("os.environ", {"LIGHTRAG_HOST": ""}):
            assert is_lightrag_configured() is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for the client health_check method."""

    @pytest.mark.asyncio
    async def test_healthy_response(self) -> None:
        """health_check should return True when LightRAG is healthy."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            200,
            json={"status": "healthy"},
            request=httpx.Request("GET", "http://localhost:9621/health"),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "get",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_unhealthy_response(self) -> None:
        """health_check should return False when LightRAG is unhealthy."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            503,
            json={"status": "unhealthy"},
            request=httpx.Request("GET", "http://localhost:9621/health"),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "get",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_connection_error(self) -> None:
        """health_check should return False on connection errors."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = await client.health_check()
            assert result is False


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class TestIngest:
    """Tests for the client ingest method."""

    @pytest.mark.asyncio
    async def test_successful_ingest(self) -> None:
        """ingest should POST text to LightRAG and return track_id."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            200,
            json={
                "status": "success",
                "message": "Text inserted successfully",
                "track_id": "track-abc-123",
            },
            request=httpx.Request(
                "POST", "http://localhost:9621/documents/text"
            ),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            result = await client.ingest(
                text="UO2 is a nuclear fuel material.",
                file_source="handbook.pdf",
            )
            assert result["track_id"] == "track-abc-123"
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["text"] == "UO2 is a nuclear fuel material."
            assert call_kwargs["json"]["file_source"] == "handbook.pdf"

    @pytest.mark.asyncio
    async def test_ingest_server_error(self) -> None:
        """ingest should raise LightRAGClientError on server errors."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            500,
            json={"detail": "Internal server error"},
            request=httpx.Request(
                "POST", "http://localhost:9621/documents/text"
            ),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), pytest.raises(LightRAGClientError):
            await client.ingest(text="test content")

    @pytest.mark.asyncio
    async def test_ingest_connection_error(self) -> None:
        """ingest should raise LightRAGClientError on connection failure."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ), pytest.raises(LightRAGClientError):
            await client.ingest(text="test content")


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class TestQuery:
    """Tests for the client query method."""

    @pytest.mark.asyncio
    async def test_successful_query(self) -> None:
        """query should POST to LightRAG and return response with references."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            200,
            json={
                "response": "UO2 is a ceramic nuclear fuel.",
                "references": [
                    {
                        "reference_id": "1",
                        "file_path": "/docs/fuel.pdf",
                        "content": ["Chunk about UO2 properties."],
                    }
                ],
            },
            request=httpx.Request(
                "POST", "http://localhost:9621/query"
            ),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            result = await client.query(
                query="What are the properties of UO2?",
                mode="mix",
            )
            assert result["response"] == "UO2 is a ceramic nuclear fuel."
            assert len(result["references"]) == 1
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["query"] == "What are the properties of UO2?"
            assert call_kwargs["json"]["mode"] == "mix"

    @pytest.mark.asyncio
    async def test_query_with_references(self) -> None:
        """query should pass include_references when requested."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            200,
            json={
                "response": "Answer text",
                "references": [],
            },
            request=httpx.Request(
                "POST", "http://localhost:9621/query"
            ),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            await client.query(
                query="test query",
                include_references=True,
            )
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["include_references"] is True

    @pytest.mark.asyncio
    async def test_query_server_error(self) -> None:
        """query should raise LightRAGClientError on server errors."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = httpx.Response(
            500,
            json={"detail": "Query processing failed"},
            request=httpx.Request(
                "POST", "http://localhost:9621/query"
            ),
        )

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), pytest.raises(LightRAGClientError):
            await client.query(query="test query")

    @pytest.mark.asyncio
    async def test_query_timeout(self) -> None:
        """query should raise LightRAGClientError on timeout."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Timed out"),
        ), pytest.raises(LightRAGClientError):
            await client.query(query="test query")
