"""Tests for RAG provider abstraction and auto-fallback (NFM-1223).

All external services are mocked:
  - LightRAG sidecar → mock LightRAGClient
  - PostgreSQL → mock AsyncSession
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.services.lightrag_client import LightRAGClientError
from nfm_db.services.rag_provider import (
    HealthStatus,
    LightRAGProvider,
    RAGProvider,
    RAGProviderSelector,
    RAGQueryResult,
    RuleBasedFallbackProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db(rows: list[dict] | None = None) -> AsyncMock:
    """Create a mock SQLAlchemy AsyncSession.

    Args:
        rows: Optional list of row dicts returned by mappings().all().
    """
    db = AsyncMock()
    mappings_obj = MagicMock()
    mappings_obj.all.return_value = rows or []
    result_mock = MagicMock()
    result_mock.mappings = AsyncMock(return_value=mappings_obj)
    db.execute = AsyncMock(return_value=result_mock)
    return db


def _make_mock_lightrag_client(
    *,
    healthy: bool = True,
    query_result: dict | None = None,
) -> AsyncMock:
    """Create a mock LightRAGClient."""
    client = AsyncMock()
    client.health_check = AsyncMock(return_value=healthy)

    default_result = query_result or {
        "response": "LightRAG answer",
        "references": [{"source": "doc1.pdf"}],
        "entities": [],
        "relationships": [],
    }
    client.query = AsyncMock(return_value=default_result)
    client.ingest = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_rag_provider_module_importable() -> None:
    """The rag_provider module should be importable."""
    assert RAGProvider is not None
    assert RAGQueryResult is not None
    assert LightRAGProvider is not None
    assert RuleBasedFallbackProvider is not None
    assert RAGProviderSelector is not None
    assert HealthStatus is not None


# ---------------------------------------------------------------------------
# RAGQueryResult
# ---------------------------------------------------------------------------


class TestRAGQueryResult:
    """Tests for the frozen RAGQueryResult dataclass."""

    def test_default_values(self) -> None:
        result = RAGQueryResult(response="hello")
        assert result.response == "hello"
        assert result.references == []
        assert result.entities == []
        assert result.relationships == []
        assert result.provider == ""
        assert result.fallback is False

    def test_immutability(self) -> None:
        result = RAGQueryResult(response="hello")
        with pytest.raises(AttributeError):
            result.response = "changed"  # type: ignore[misc]

    def test_with_fallback_flag(self) -> None:
        result = RAGQueryResult(response="fallback answer", fallback=True)
        assert result.fallback is True


# ---------------------------------------------------------------------------
# LightRAGProvider
# ---------------------------------------------------------------------------


class TestLightRAGProvider:
    """Tests for the LightRAGProvider wrapping LightRAGClient."""

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        client = _make_mock_lightrag_client()
        provider = LightRAGProvider(client=client)  # type: ignore[arg-type]
        assert provider.name == "lightrag"

    @pytest.mark.asyncio
    async def test_query_delegates_to_client(self) -> None:
        client = _make_mock_lightrag_client()
        provider = LightRAGProvider(client=client)  # type: ignore[arg-type]
        result = await provider.query(query="What is UO2?")
        assert result.response == "LightRAG answer"
        assert result.provider == "lightrag"
        assert result.fallback is False
        client.query.assert_called_once_with(query="What is UO2?")

    @pytest.mark.asyncio
    async def test_ingest_delegates_to_client(self) -> None:
        client = _make_mock_lightrag_client()
        provider = LightRAGProvider(client=client)  # type: ignore[arg-type]
        await provider.ingest(text="some text", source="doc.pdf")
        client.ingest.assert_called_once_with(text="some text", file_source="doc.pdf")

    @pytest.mark.asyncio
    async def test_health_delegates_to_client(self) -> None:
        client = _make_mock_lightrag_client(healthy=True)
        provider = LightRAGProvider(client=client)  # type: ignore[arg-type]
        assert await provider.health() is True
        client.health_check.assert_called_once()


# ---------------------------------------------------------------------------
# RuleBasedFallbackProvider
# ---------------------------------------------------------------------------


class TestRuleBasedFallbackProvider:
    """Tests for the PG full-text search fallback provider."""

    @pytest.mark.asyncio
    async def test_name(self) -> None:
        db = _make_mock_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        assert provider.name == "rule-based-fallback"

    @pytest.mark.asyncio
    async def test_query_returns_fallback_flag(self) -> None:
        db = _make_mock_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        result = await provider.query(query="UO2 fuel")
        assert result.fallback is True
        assert result.provider == "rule-based-fallback"

    @pytest.mark.asyncio
    async def test_query_empty_query(self) -> None:
        db = _make_mock_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        result = await provider.query(query="???")
        assert result.response == ""

    @pytest.mark.asyncio
    async def test_query_with_results(self) -> None:
        mock_row = {
            "source_type": "data_source",
            "source_id": "abc-123",
            "snippet_text": "UO2 is uranium dioxide fuel.",
            "rank": 0.85,
        }
        db = _make_mock_db(rows=[mock_row])
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        result = await provider.query(query="UO2 fuel")
        assert "found 1 relevant results" in result.response
        assert len(result.references) == 1
        assert result.references[0]["source_type"] == "data_source"
        assert result.references[0]["source_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_query_no_results(self) -> None:
        db = _make_mock_db(rows=[])
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        result = await provider.query(query="nonexistent")
        assert "No results found" in result.response

    @pytest.mark.asyncio
    async def test_ingest_is_noop(self) -> None:
        db = _make_mock_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        await provider.ingest(text="some text", source="doc.pdf")
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_with_working_db(self) -> None:
        db = _make_mock_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        assert await provider.health() is True

    @pytest.mark.asyncio
    async def test_health_with_broken_db(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]
        assert await provider.health() is False


# ---------------------------------------------------------------------------
# HealthStatus
# ---------------------------------------------------------------------------


class TestHealthStatus:
    """Tests for the frozen HealthStatus dataclass."""

    def test_defaults(self) -> None:
        status = HealthStatus(
            lightrag_healthy=True,
            active_provider="lightrag",
            consecutive_failures=0,
            failure_threshold=3,
        )
        assert status.lightrag_healthy is True
        assert status.active_provider == "lightrag"

    def test_immutability(self) -> None:
        status = HealthStatus(
            lightrag_healthy=True,
            active_provider="lightrag",
            consecutive_failures=0,
            failure_threshold=3,
        )
        with pytest.raises(AttributeError):
            status.active_provider = "fallback"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RAGProviderSelector
# ---------------------------------------------------------------------------


class TestRAGProviderSelector:
    """Tests for the auto-selecting RAG provider with circuit breaker."""

    @pytest.mark.asyncio
    async def test_uses_lightrag_when_healthy(self) -> None:
        """When LightRAG is healthy, selector should use it."""
        client = _make_mock_lightrag_client(healthy=True)
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )
        result = await selector.query(query="What is UO2?")
        assert result.provider == "lightrag"
        assert result.fallback is False

    @pytest.mark.asyncio
    async def test_switches_to_fallback_after_threshold(self) -> None:
        """After failure_threshold consecutive failures, switch to fallback."""
        client = _make_mock_lightrag_client(healthy=False)
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
            failure_threshold=2,
        )

        status1 = await selector.check_health()
        assert status1.active_provider == "lightrag"
        assert status1.consecutive_failures == 1

        status2 = await selector.check_health()
        assert status2.active_provider == "rule-based-fallback"
        assert status2.consecutive_failures == 2

    @pytest.mark.asyncio
    async def test_recovers_when_lightrag_becomes_healthy(self) -> None:
        """When LightRAG recovers, selector should switch back."""
        client = _make_mock_lightrag_client(healthy=False)
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
            failure_threshold=1,
        )

        await selector.check_health()
        assert selector.status.active_provider == "rule-based-fallback"

        client.health_check = AsyncMock(return_value=True)
        await selector.check_health()
        assert selector.status.active_provider == "lightrag"
        assert selector.status.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_query_falls_back_on_client_error(self) -> None:
        """If LightRAG raises during query, fallback kicks in."""
        client = _make_mock_lightrag_client(healthy=True)
        client.query = AsyncMock(side_effect=LightRAGClientError("timeout"))
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        result = await selector.query(query="test")
        assert result.fallback is True
        assert result.provider == "rule-based-fallback"

    @pytest.mark.asyncio
    async def test_status_property_without_check(self) -> None:
        """status property should not trigger a health check."""
        client = _make_mock_lightrag_client()
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        status = selector.status
        assert status.active_provider == "lightrag"
        client.health_check.assert_not_called()
