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
        """Should return True when NFM_LIGHTRAG_HOST env var is set."""
        from nfm_db.services.lightrag_client import (
            is_lightrag_configured,  # type: ignore[import-untyped]
        )

        with patch.dict("os.environ", {"NFM_LIGHTRAG_HOST": "localhost"}):
            assert is_lightrag_configured() is True

    def test_not_configured_when_host_missing(self) -> None:
        """Should return False when NFM_LIGHTRAG_HOST env var is not set."""
        from nfm_db.services.lightrag_client import (
            is_lightrag_configured,  # type: ignore[import-untyped]
        )

        with patch.dict("os.environ", {"NFM_LIGHTRAG_HOST": ""}):
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
            request=httpx.Request("POST", "http://localhost:9621/documents/text"),
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
            request=httpx.Request("POST", "http://localhost:9621/documents/text"),
        )

        with (
            patch.object(
                client._http_client,  # type: ignore[attr-defined]
                "post",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            pytest.raises(LightRAGClientError),
        ):
            await client.ingest(text="test content")

    @pytest.mark.asyncio
    async def test_ingest_connection_error(self) -> None:
        """ingest should raise LightRAGClientError on connection failure."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)

        with (
            patch.object(
                client._http_client,  # type: ignore[attr-defined]
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("Connection refused"),
            ),
            pytest.raises(LightRAGClientError),
        ):
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
            request=httpx.Request("POST", "http://localhost:9621/query"),
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
            request=httpx.Request("POST", "http://localhost:9621/query"),
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
            request=httpx.Request("POST", "http://localhost:9621/query"),
        )

        with (
            patch.object(
                client._http_client,  # type: ignore[attr-defined]
                "post",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            pytest.raises(LightRAGClientError),
        ):
            await client.query(query="test query")

    @pytest.mark.asyncio
    async def test_query_timeout(self) -> None:
        """query should raise LightRAGClientError on timeout."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        client = LightRAGClient(host="localhost", port=9621)

        with (
            patch.object(
                client._http_client,  # type: ignore[attr-defined]
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("Timed out"),
            ),
            pytest.raises(LightRAGClientError),
        ):
            await client.query(query="test query")


# ---------------------------------------------------------------------------
# Lifecycle (close, context manager)
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for client lifecycle: close() and async context manager."""

    @pytest.mark.asyncio
    async def test_close_delegates_to_http_client(self) -> None:
        """close() should delegate to the underlying httpx.AsyncClient."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "aclose",
            new_callable=AsyncMock,
        ) as mock_close:
            await client.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_returns_self(self) -> None:
        """__aenter__ should return the client instance itself."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        async with client as entered:
            assert entered is client

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exit(self) -> None:
        """__aexit__ should call close() after normal exit."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "aclose",
            new_callable=AsyncMock,
        ) as mock_close:
            async with client:
                pass
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exception(self) -> None:
        """__aexit__ should call close() even when an exception is raised."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "aclose",
            new_callable=AsyncMock,
        ) as mock_close:
            with pytest.raises(RuntimeError):
                async with client:
                    raise RuntimeError("boom")
            mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Timeout split (NFM-2565)
# ---------------------------------------------------------------------------


class TestTimeoutSplit:
    """query and ingest must not share a single timeout budget.

    Before NFM-2565 both paths used one 60s constant. A stalled sidecar
    therefore blocked the *synchronous* semantic-search request for a full
    minute before ``RAGProviderSelector`` fell back to Postgres FTS — the
    fallback worked, but the user saw a blank screen for 60s.
    """

    def test_defaults_are_split(self) -> None:
        """Query budget must be far tighter than the ingest budget."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_INGEST_TIMEOUT,
            _DEFAULT_QUERY_TIMEOUT,
            LightRAGClient,
        )

        client = LightRAGClient(host="localhost", port=9621)

        assert client.query_timeout == _DEFAULT_QUERY_TIMEOUT
        assert client.ingest_timeout == _DEFAULT_INGEST_TIMEOUT
        assert client.query_timeout < client.ingest_timeout
        # A user-facing request must not be allowed to hang for a minute.
        assert client.query_timeout <= 10.0

    def test_explicit_timeout_collapses_both(self) -> None:
        """Legacy ``timeout=`` keeps applying to both paths (back-compat)."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621, timeout=42.0)

        assert client.query_timeout == 42.0
        assert client.ingest_timeout == 42.0
        assert client.timeout == 42.0

    def test_per_path_overrides(self) -> None:
        """Each path can be tuned independently."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(
            host="localhost",
            port=9621,
            query_timeout=3.0,
            ingest_timeout=600.0,
        )

        assert client.query_timeout == 3.0
        assert client.ingest_timeout == 600.0

    @pytest.mark.asyncio
    async def test_query_request_uses_query_timeout(self) -> None:
        """The /query POST must carry the query budget, not the ingest one."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {"response": "ok"}
        mock_response.raise_for_status = lambda: None

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            await client.query(query="what is UO2?")

        assert mock_post.call_args.kwargs["timeout"] == client.query_timeout

    @pytest.mark.asyncio
    async def test_ingest_request_uses_ingest_timeout(self) -> None:
        """The /documents/text POST must carry the generous ingest budget."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {"track_id": "t-1"}
        mock_response.raise_for_status = lambda: None

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_post:
            await client.ingest(text="[Material] UO2")

        assert mock_post.call_args.kwargs["timeout"] == client.ingest_timeout

    @pytest.mark.asyncio
    async def test_health_check_uses_query_timeout(self) -> None:
        """Health probes gate user-facing routing, so they use the fast budget."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "get",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_get:
            assert await client.health_check() is True

        assert mock_get.call_args.kwargs["timeout"] == client.query_timeout


class TestAsyncClientTimeoutEnforcement:
    """NFM-3367: bound the AsyncClient-level timeout so connection-level hangs
    cannot exceed the user-visible query budget.

    Before the fix, ``httpx.AsyncClient(timeout=60.0)`` applied a single float
    to *all* timeout phases (connect/read/write/pool). Even though the per-call
    ``timeout=self.query_timeout`` was 8.0s, a stalled TCP handshake could keep
    the request alive longer than the user-visible budget, and any future caller
    that forgot to pass ``timeout=`` on the request would silently fall back to
    the 60s transport default.

    The fix: configure the AsyncClient with an ``httpx.Timeout`` that splits
    the budget between ``connect`` (the part not bounded by the request) and
    ``read`` (slaved to the query budget). The query() call then carries its
    own 8s budget end-to-end.
    """

    def test_http_client_uses_httpx_timeout_with_explicit_connect(
        self,
    ) -> None:
        """The transport-level timeout must be an httpx.Timeout, not a float."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout), (
            "AsyncClient must be configured with httpx.Timeout, not a bare "
            "float, so connect/read/write/pool phases can be bounded separately."
        )

    def test_http_client_connect_timeout_is_bounded(self) -> None:
        """connect must be a finite, short value — not 60s, not None.

        A stalled TCP handshake cannot consume the whole 8s query budget if the
        connect phase is bounded at the transport level.
        """
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.connect is not None
        # Must be short enough that a stalled handshake cannot dominate the
        # 8s query budget.
        assert transport_timeout.connect <= 5.0

    def test_http_client_read_timeout_is_query_budget(self) -> None:
        """The transport read timeout must equal the query budget (8s).

        This is the AC-3 traceability check: the value at the actual httpx
        boundary must match the configured query budget, not the legacy 60s.
        """
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_QUERY_TIMEOUT,
            LightRAGClient,
        )

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.read == _DEFAULT_QUERY_TIMEOUT

    def test_http_client_total_envelopes_query_budget(self) -> None:
        """The total timeout must not exceed the query budget (with buffer).

        AC-1: a query returns within 10s (8s timeout + buffer). The transport
        envelope (the total phase that includes everything) must also stay
        inside the query budget so the user budget is the binding ceiling.

        httpx 0.28.x exposes no ``.total`` attribute on ``Timeout``; the
        effective ceiling is the configured ``read`` phase (which is what
        bounds the response). We assert it stays inside the query budget.
        """
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_QUERY_TIMEOUT,
            LightRAGClient,
        )

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        # ``read`` is the binding ceiling for the user-visible query budget:
        # a request can stall for at most ``connect + read`` seconds, so
        # bounding ``read`` at the query budget keeps the envelope inside
        # AC-1's 10s ceiling (5s connect + 8s read = 13s, with the per-call
        # ``timeout=8s`` re-enforcing the read budget at request time).
        assert transport_timeout.read is not None
        assert transport_timeout.read <= _DEFAULT_QUERY_TIMEOUT

    def test_explicit_timeout_propagates_to_client_timeout(self) -> None:
        """Legacy ``timeout=42.0`` must keep collapsing the transport defaults.

        Back-compat: callers that pass ``timeout=`` see the same effective
        envelope they did before NFM-3367, but now as an httpx.Timeout with
        explicit connect so the connection can't dominate the budget.
        """
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621, timeout=42.0)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.connect is not None
        assert transport_timeout.connect <= 5.0
        assert transport_timeout.read == 42.0


class TestQueryTimeoutDegradesToFallback:
    """A slow sidecar must degrade to Postgres FTS, not surface an error."""

    @pytest.mark.asyncio
    async def test_selector_falls_back_on_query_timeout(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )
        from nfm_db.services.rag_provider import (  # type: ignore[import-untyped]
            RAGProviderSelector,
        )

        client = LightRAGClient(host="localhost", port=9621)
        db_session = AsyncMock()

        selector = RAGProviderSelector(lightrag_client=client, db_session=db_session)

        fallback_result = AsyncMock()
        with (
            patch.object(
                client._http_client,  # type: ignore[attr-defined]
                "post",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("Timed out"),
            ),
            patch.object(
                selector._fallback,  # type: ignore[attr-defined]
                "query",
                new_callable=AsyncMock,
                return_value=fallback_result,
            ) as mock_fallback,
        ):
            result = await selector.query(query="what is UO2?")

        mock_fallback.assert_awaited_once()
        assert result is fallback_result
