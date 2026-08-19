"""Tests for RAG provider abstraction and auto-fallback (NFM-1223).

All external services are mocked:
  - LightRAG sidecar → mock LightRAGClient
  - PostgreSQL → mock AsyncSession
"""

from __future__ import annotations

import logging
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
        )
        assert status.lightrag_healthy is True
        assert status.active_provider == "lightrag"

    def test_immutability(self) -> None:
        status = HealthStatus(
            lightrag_healthy=True,
            active_provider="lightrag",
        )
        with pytest.raises(AttributeError):
            status.active_provider = "fallback"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RAGProviderSelector
# ---------------------------------------------------------------------------


class TestRAGProviderSelector:
    """Tests for the stateless RAG provider with try/except fallback."""

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
    async def test_check_health_reflects_lightrag_state(self) -> None:
        """check_health returns lightrag when healthy, fallback when not."""
        client = _make_mock_lightrag_client(healthy=True)
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        status = await selector.check_health()
        assert status.active_provider == "lightrag"
        assert status.lightrag_healthy is True

        client.health_check = AsyncMock(return_value=False)
        status = await selector.check_health()
        assert status.active_provider == "rule-based-fallback"
        assert status.lightrag_healthy is False

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
    async def test_ingest_falls_back_on_client_error(self) -> None:
        """If LightRAG raises during ingest, fallback kicks in."""
        client = _make_mock_lightrag_client(healthy=True)
        client.ingest = AsyncMock(side_effect=LightRAGClientError("timeout"))
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        await selector.ingest(text="some text", source="doc.pdf")
        # fallback ingest is a no-op, but should not raise

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

    # ------------------------------------------------------------------
    # NFM-3368 — Broad exception fallback (AC-1, AC-2, AC-4)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_query_falls_back_on_unexpected_exception(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-1 + AC-2: unexpected exceptions must trigger PG fallback (not propagate).

        LightRAG may raise exceptions that are not LightRAGClientError subclasses
        (e.g. JSONDecodeError, RuntimeError, asyncio.TimeoutError). The selector
        must catch these broadly and fall back to the rule-based provider, while
        logging the full exception detail at WARNING level server-side.
        """
        client = _make_mock_lightrag_client(healthy=True)
        client.query = AsyncMock(
            side_effect=RuntimeError("upstream crashed: schema validation failed")
        )
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING):
            result = await selector.query(query="test")

        # Fallback must succeed (returns fallback=True) — no exception leaks
        assert result.fallback is True
        assert result.provider == "rule-based-fallback"

        # AC-1: full exception detail must be preserved server-side at WARNING
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "upstream crashed: schema validation failed" in r.getMessage()
            for r in warning_records
        ), (
            "Expected WARNING log to contain full exception message, "
            f"got: {[r.getMessage() for r in warning_records]}"
        )

    @pytest.mark.asyncio
    async def test_query_raises_when_both_providers_fail(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-4: when LightRAG and PG fallback both fail, raise HTTPException 503.

        A clean 503 with a user-friendly message (no internal exception text)
        must be raised.  The full exception chain must be logged at WARNING
        server-side so operators can debug the outage.
        """
        from fastapi import HTTPException

        client = _make_mock_lightrag_client(healthy=True)
        client.query = AsyncMock(
            side_effect=LightRAGClientError(
                "LightRAG query failed: HTTP 503 - upstream down"
            )
        )
        # DB fallback also fails
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("database connection lost"))

        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(HTTPException) as exc_info:
                await selector.query(query="test")

        # AC-4: 503 status
        assert exc_info.value.status_code == 503

        # AC-2: response body must NOT contain raw LightRAG or DB exception text
        detail = str(exc_info.value.detail)
        assert "LightRAG" not in detail, (
            f"User-facing message leaked 'LightRAG': {detail!r}"
        )
        assert "database connection lost" not in detail, (
            f"User-facing message leaked DB exception: {detail!r}"
        )

        # AC-1: full exception chain logged at WARNING server-side
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        messages = " ".join(r.getMessage() for r in warning_records)
        assert "LightRAG" in messages or "HTTP 503" in messages, (
            f"Expected WARNING log to preserve LightRAG detail, got: {messages!r}"
        )

    @pytest.mark.asyncio
    async def test_query_logs_full_exception_detail_on_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC-1: server-side WARNING log must contain the full original exception.

        When LightRAG fails with an httpx-style error like
        'HTTP 500 - {"detail":"internal error"}', the WARNING record must
        contain both the status code and the body snippet — not a truncated
        version like 'LightRAG query failed:'.
        """
        client = _make_mock_lightrag_client(healthy=True)
        full_error_msg = (
            "LightRAG query failed: HTTP 500 - "
            '{"detail":"upstream timeout after 30s"}'
        )
        client.query = AsyncMock(side_effect=LightRAGClientError(full_error_msg))
        db = _make_mock_db()

        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING):
            await selector.query(query="test")

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        joined = " ".join(r.getMessage() for r in warning_records)

        # AC-1: full status code AND body snippet preserved (not truncated)
        assert "HTTP 500" in joined, (
            f"Expected 'HTTP 500' in WARNING log, got: {joined!r}"
        )
        assert "upstream timeout" in joined, (
            f"Expected body snippet in WARNING log, got: {joined!r}"
        )
