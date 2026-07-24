"""Tests for LightRAG async HTTP client (NFM-741.4).

Tests cover:
- Successful ingest, query, graph_query, health_check
- Circuit breaker triggers after configurable failures
- Graceful degradation returns proper error response
- Timeout handling
- Retry with exponential backoff
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from nfm_db.services.lightrag_client import (
    CircuitState,
    LightRAGClient,
    LightRAGClientError,
    LightRAGUnavailableError,
    RetryPolicy,
    TimeoutConfig,
    is_lightrag_configured,
)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def test_lightrag_client_importable() -> None:
    from nfm_db.services.lightrag_client import (  # noqa: F811
        LightRAGClient,
        LightRAGClientError,
        LightRAGUnavailableError,
        CircuitState,
        RetryPolicy,
        TimeoutConfig,
        is_lightrag_configured,
    )

    assert LightRAGClient is not None
    assert LightRAGClientError is not None
    assert LightRAGUnavailableError is not None
    assert CircuitState is not None
    assert RetryPolicy is not None
    assert TimeoutConfig is not None
    assert callable(is_lightrag_configured)


def test_unavailable_error_is_client_error() -> None:
    """LightRAGUnavailableError should be a subclass of LightRAGClientError."""
    err = LightRAGUnavailableError("test", is_circuit_open=True)
    assert isinstance(err, LightRAGClientError)
    assert err.is_circuit_open is True


def test_unavailable_error_circuit_flag_default() -> None:
    """is_circuit_open defaults to False."""
    err = LightRAGUnavailableError("test")
    assert err.is_circuit_open is False


# ---------------------------------------------------------------------------
# is_lightrag_configured
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_configured_when_base_url_set(self) -> None:
        with patch.dict("os.environ", {"LIGHTRAG_BASE_URL": "http://lightrag:8001"}):
            assert is_lightrag_configured() is True

    def test_configured_when_host_set(self) -> None:
        with patch.dict("os.environ", {"NFM_LIGHTRAG_HOST": "localhost"}):
            assert is_lightrag_configured() is True

    def test_not_configured_when_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_lightrag_configured() is False


# ---------------------------------------------------------------------------
# CircuitState
# ---------------------------------------------------------------------------


class TestCircuitState:
    def test_initially_closed(self) -> None:
        state = CircuitState(failure_threshold=3)
        assert state.is_open is False
        assert state.failure_count == 0

    def test_opens_after_threshold(self) -> None:
        state = CircuitState(failure_threshold=3)
        for _ in range(3):
            state = state.with_failure()
        assert state.is_open is True

    def test_stays_closed_below_threshold(self) -> None:
        state = CircuitState(failure_threshold=5)
        for _ in range(4):
            state = state.with_failure()
        assert state.is_open is False

    def test_resets_on_success(self) -> None:
        state = CircuitState(failure_threshold=2)
        state = state.with_failure()
        state = state.with_success()
        assert state.is_open is False
        assert state.failure_count == 0

    def test_blocks_requests_when_open(self) -> None:
        state = CircuitState(failure_threshold=1, recovery_timeout=999)
        state = state.with_failure()
        assert state.allow_request() is False

    def test_allows_requests_when_closed(self) -> None:
        state = CircuitState(failure_threshold=5)
        assert state.allow_request() is True

    def test_half_open_after_recovery_timeout(self) -> None:
        state = CircuitState(failure_threshold=1, recovery_timeout=0.0)
        state = state.with_failure()
        assert state.is_open is True
        assert state.allow_request() is True

    def test_half_open_limited_attempts(self) -> None:
        state = CircuitState(
            failure_threshold=1,
            recovery_timeout=0.0,
            max_half_open_attempts=1,
        )
        state = state.with_failure()
        assert state.allow_request() is True
        state = state.with_half_open_attempt()
        assert state.allow_request() is False


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_default_retries(self) -> None:
        policy = RetryPolicy()
        assert policy.max_retries == 3

    def test_exponential_backoff(self) -> None:
        policy = RetryPolicy(base_delay=0.5, backoff_factor=2.0, max_delay=10.0)
        assert policy.get_delay(0) == 0.5
        assert policy.get_delay(1) == 1.0
        assert policy.get_delay(2) == 2.0
        assert policy.get_delay(3) == 4.0

    def test_max_delay_cap(self) -> None:
        policy = RetryPolicy(base_delay=1.0, backoff_factor=10.0, max_delay=5.0)
        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 5.0


# ---------------------------------------------------------------------------
# TimeoutConfig
# ---------------------------------------------------------------------------


class TestTimeoutConfig:
    def test_default_timeouts(self) -> None:
        config = TimeoutConfig()
        assert config.health == 5.0
        assert config.ingest == 60.0
        assert config.query == 120.0
        assert config.graph_query == 60.0

    def test_get_by_request_type(self) -> None:
        config = TimeoutConfig()
        assert config.get("health") == 5.0
        assert config.get("query") == 120.0
        assert config.get("unknown") == 30.0


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_response(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            200,
            json={"status": "healthy"},
            request=httpx.Request("GET", "http://localhost:9621/health"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = await client.health_check()
            assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_connection_error_returns_unavailable(self) -> None:
        """health_check returns graceful fallback on connection errors."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = await client.health_check()
            assert result["available"] is False
            assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_circuit_open_returns_unavailable(self) -> None:
        """health_check returns fallback when circuit breaker is open."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            circuit_threshold=1,
            retry_policy=RetryPolicy(max_retries=0),
        )
        client._circuit = CircuitState(
            is_open=True,
            last_failure_time=time.monotonic(),
            recovery_timeout=999,
        )

        result = await client.health_check()
        assert result["available"] is False


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


class TestIngest:
    @pytest.mark.asyncio
    async def test_successful_ingest_document(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            200,
            json={"status": "success", "track_id": "track-abc-123"},
            request=httpx.Request("POST", "http://localhost:9621/documents/text"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_req:
            result = await client.ingest_document(
                text="UO2 is a nuclear fuel material.",
                metadata={"file_source": "handbook.pdf"},
            )
            assert result["track_id"] == "track-abc-123"
            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json"]["text"] == "UO2 is a nuclear fuel material."
            assert call_kwargs["json"]["file_source"] == "handbook.pdf"

    @pytest.mark.asyncio
    async def test_ingest_connection_error_raises_unavailable(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ), pytest.raises(LightRAGUnavailableError):
            await client.ingest_document(text="test")

    @pytest.mark.asyncio
    async def test_ingest_server_error_raises_client_error(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            500,
            json={"detail": "Internal server error"},
            request=httpx.Request("POST", "http://localhost:9621/documents/text"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), pytest.raises(LightRAGClientError):
            await client.ingest_document(text="test")

    @pytest.mark.asyncio
    async def test_legacy_ingest_delegates(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            200,
            json={"status": "success", "track_id": "t-1"},
            request=httpx.Request("POST", "http://localhost:9621/documents/text"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_req:
            await client.ingest(text="test", file_source="doc.pdf")
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json"]["file_source"] == "doc.pdf"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class TestQuery:
    @pytest.mark.asyncio
    async def test_successful_query(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            200,
            json={"response": "UO2 is a ceramic nuclear fuel."},
            request=httpx.Request("POST", "http://localhost:9621/query"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_req:
            result = await client.query(query="What is UO2?", mode="hybrid")
            assert result["response"] == "UO2 is a ceramic nuclear fuel."
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json"]["mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_query_with_fallback_returns_unavailable(self) -> None:
        """query_with_fallback returns {available: false} instead of raising."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await client.query_with_fallback(query="test")
            assert result["available"] is False
            assert "message" in result
            assert result["operation"] == "query"

    @pytest.mark.asyncio
    async def test_query_timeout_raises_unavailable(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Timed out"),
        ), pytest.raises(LightRAGUnavailableError):
            await client.query(query="test")


# ---------------------------------------------------------------------------
# Graph Query
# ---------------------------------------------------------------------------


class TestGraphQuery:
    @pytest.mark.asyncio
    async def test_successful_graph_query(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            200,
            json={"nodes": [{"id": "UO2"}], "edges": []},
            request=httpx.Request("POST", "http://localhost:9621/graph/query"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_req:
            result = await client.graph_query(cypher="MATCH (n) RETURN n")
            assert len(result["nodes"]) == 1
            call_kwargs = mock_req.call_args[1]
            assert call_kwargs["json"]["query"] == "MATCH (n) RETURN n"

    @pytest.mark.asyncio
    async def test_graph_query_with_fallback(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await client.graph_query_with_fallback(
                cypher="MATCH (n) RETURN n"
            )
            assert result["available"] is False
            assert result["operation"] == "graph_query"

    @pytest.mark.asyncio
    async def test_graph_query_server_error_raises_client_error(self) -> None:
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )
        mock_response = httpx.Response(
            500,
            json={"detail": "Query failed"},
            request=httpx.Request("POST", "http://localhost:9621/graph/query"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ), pytest.raises(LightRAGClientError):
            await client.graph_query(cypher="MATCH (n) RETURN n")


# ---------------------------------------------------------------------------
# Circuit Breaker Integration
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self) -> None:
        """Circuit breaker opens after consecutive failures."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=3,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            for _ in range(3):
                with pytest.raises(LightRAGUnavailableError):
                    await client.query(query="test")

            assert client.circuit_state.is_open is True

    @pytest.mark.asyncio
    async def test_blocks_requests_when_open(self) -> None:
        """When circuit is open, immediately raises without calling the server."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=1,
        )
        client._circuit = CircuitState(
            is_open=True,
            last_failure_time=time.monotonic(),
            recovery_timeout=999,
        )

        with pytest.raises(LightRAGUnavailableError) as exc_info:
            await client.query(query="test")

        assert exc_info.value.is_circuit_open is True

    @pytest.mark.asyncio
    async def test_closes_on_success(self) -> None:
        """A successful call resets the circuit breaker."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=1,
        )
        client._circuit = CircuitState(
            is_open=True,
            last_failure_time=0.0,
            recovery_timeout=0.0,
        )

        mock_response = httpx.Response(
            200,
            json={"response": "ok"},
            request=httpx.Request("POST", "http://localhost:9621/query"),
        )
        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await client.query(query="test")

        assert client.circuit_state.is_open is False
        assert client.circuit_state.failure_count == 0


# ---------------------------------------------------------------------------
# Retry with Exponential Backoff
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        """Client retries the configured number of times."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.01),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ) as mock_req:
            with pytest.raises(LightRAGUnavailableError):
                await client.query(query="test")

            assert mock_req.call_count == 3

    @pytest.mark.asyncio
    async def test_succeeds_on_retry(self) -> None:
        """Client returns successful response on a retry attempt."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.01),
            circuit_threshold=99,
        )

        success_response = httpx.Response(
            200,
            json={"response": "answer"},
            request=httpx.Request("POST", "http://localhost:9621/query"),
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused"),
                success_response,
            ],
        ) as mock_req:
            result = await client.query(query="test")
            assert result["response"] == "answer"
            assert mock_req.call_count == 3


# ---------------------------------------------------------------------------
# Graceful Degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_query_fallback_never_raises(self) -> None:
        """query_with_fallback NEVER raises — always returns a dict."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await client.query_with_fallback(query="test")
            assert isinstance(result, dict)
            assert result["available"] is False

    @pytest.mark.asyncio
    async def test_graph_query_fallback_never_raises(self) -> None:
        """graph_query_with_fallback NEVER raises."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await client.graph_query_with_fallback(
                cypher="MATCH (n) RETURN n"
            )
            assert isinstance(result, dict)
            assert result["available"] is False

    @pytest.mark.asyncio
    async def test_health_check_never_raises(self) -> None:
        """health_check NEVER raises — always returns a dict."""
        client = LightRAGClient(
            base_url="http://localhost:9621",
            retry_policy=RetryPolicy(max_retries=0),
            circuit_threshold=99,
        )

        with patch.object(
            client._http_client,
            "request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("refused"),
        ):
            result = await client.health_check()
            assert isinstance(result, dict)
            assert result["available"] is False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_close_delegates(self) -> None:
        client = LightRAGClient(base_url="http://localhost:9621")
        with patch.object(
            client._http_client, "aclose", new_callable=AsyncMock,
        ) as mock_close:
            await client.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        client = LightRAGClient(base_url="http://localhost:9621")
        with patch.object(
            client._http_client, "aclose", new_callable=AsyncMock,
        ) as mock_close:
            async with client as entered:
                assert entered is client
            mock_close.assert_called_once()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_base_url_from_env(self) -> None:
        with patch.dict("os.environ", {"LIGHTRAG_BASE_URL": "http://custom:9000"}):
            client = LightRAGClient()
            assert client.base_url == "http://custom:9000"

    def test_host_port_fallback(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            client = LightRAGClient(host="myhost", port=7777)
            assert client.base_url == "http://myhost:7777"

    def test_default_base_url(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            client = LightRAGClient()
            assert client.base_url == "http://lightrag:8001"
