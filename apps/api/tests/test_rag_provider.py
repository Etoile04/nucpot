"""Tests for RAG provider abstraction and auto-fallback (NFM-1223).

All external services are mocked:
  - LightRAG sidecar → mock LightRAGClient
  - PostgreSQL → mock AsyncSession
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
        # NFM-3407: ``request_id`` is forwarded as None when the caller
        # doesn't supply one (the common case for background ingest).
        client.query.assert_called_once_with(
            query="What is UO2?", request_id=None
        )

    @pytest.mark.asyncio
    async def test_ingest_delegates_to_client(self) -> None:
        client = _make_mock_lightrag_client()
        provider = LightRAGProvider(client=client)  # type: ignore[arg-type]
        await provider.ingest(text="some text", source="doc.pdf")
        # NFM-3407: ``request_id`` forwarded as None by default.
        client.ingest.assert_called_once_with(
            text="some text", file_source="doc.pdf", request_id=None
        )

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


# ---------------------------------------------------------------------------
# NFM-3407: Path B no-swallow + degradation_reason
# ---------------------------------------------------------------------------


class TestRAGProviderSelectorNoSwallow:
    """NFM-3407 AC-5 — Path B (anonymous) must not swallow LightRAG errors.

    Before NFM-3407 the selector had two bare ``except LightRAGClientError``
    blocks (no ``as exc``) that called ``logger.warning("...")`` without
    ``exc_info=True`` and silently fell back to the rule-based provider.
    The caller had no way to tell that LightRAG was degraded.

    Contract:
      * both swallow sites bind ``as exc`` and call ``logger.warning(..., exc_info=True)``
      * a LightRAG failure produces a ``RAGQueryResult`` with
        ``fallback=True`` AND a populated ``degradation_reason`` (a
        bounded token like ``upstream_timeout`` / ``upstream_unavailable``
        / ``upstream_error`` — never ``str(exc)`` and never the upstream body)
      * ``request_id`` (UUID4) is propagated onto the fallback result so
        the operator can correlate the log line with the response
      * the fallback result is a **new** frozen instance (project
        immutability rule — the existing rule-based ``RAGQueryResult``
        must not be mutated)
    """

    @pytest.mark.asyncio
    async def test_query_fallback_includes_degradation_reason_on_timeout(
        self, caplog
    ) -> None:
        """LightRAG ReadTimeout → fallback with degradation_reason=upstream_timeout."""
        from nfm_db.services.rag_provider import RAGProviderSelector

        # LightRAG client raises an enriched LightRAGClientError for ReadTimeout.
        client = _make_mock_lightrag_client()
        client.query = AsyncMock(
            side_effect=LightRAGClientError(
                "LightRAG query failed: [ReadTimeout] Timed out",
                original_type="ReadTimeout",
                original_message="Timed out",
            )
        )
        # Rule-based fallback returns a frozen RAGQueryResult we must NOT mutate.
        original_fallback = RAGQueryResult(
            response="rule-based answer",
            references=[],
            provider="rule-based-fallback",
            fallback=True,
        )
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with (
            patch.object(
                selector._fallback,  # type: ignore[attr-defined]
                "query",
                new_callable=AsyncMock,
                return_value=original_fallback,
            ),
            caplog.at_level("WARNING", logger="nfm_db.services.rag_provider"),
        ):
            result = await selector.query(
                query="test", request_id="req-timeout-1"
            )

        # New instance, not the original fallback.
        assert result is not original_fallback
        assert result.fallback is True
        assert result.degradation_reason == "upstream_timeout"
        assert result.request_id == "req-timeout-1"
        # Original rule-based fields are preserved on the new instance.
        assert result.response == "rule-based answer"
        assert result.provider == "rule-based-fallback"
        # AC-5: log carries the real exception, not just a static string.
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warning_records, "expected a WARNING log record"
        assert any(r.exc_info is not None for r in warning_records), (
            "logger.warning must be called with exc_info=True"
        )
        # request_id appears in the log so operator can correlate.
        assert any("req-timeout-1" in r.getMessage() for r in warning_records)

    @pytest.mark.asyncio
    async def test_query_fallback_includes_degradation_reason_on_connect_error(
        self,
    ) -> None:
        """LightRAG ConnectError → fallback with degradation_reason=upstream_unavailable."""
        from nfm_db.services.rag_provider import RAGProviderSelector

        client = _make_mock_lightrag_client()
        client.query = AsyncMock(
            side_effect=LightRAGClientError(
                "LightRAG query failed: [ConnectError] Connection refused",
                original_type="ConnectError",
                original_message="Connection refused",
            )
        )
        original_fallback = RAGQueryResult(
            response="",
            provider="rule-based-fallback",
            fallback=True,
        )
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with patch.object(
            selector._fallback,  # type: ignore[attr-defined]
            "query",
            new_callable=AsyncMock,
            return_value=original_fallback,
        ):
            result = await selector.query(query="test")

        assert result is not original_fallback
        assert result.degradation_reason == "upstream_unavailable"

    @pytest.mark.asyncio
    async def test_query_fallback_includes_degradation_reason_on_http_error(
        self,
    ) -> None:
        """HTTP 500 upstream → fallback with degradation_reason=upstream_error."""
        from nfm_db.services.rag_provider import RAGProviderSelector

        client = _make_mock_lightrag_client()
        client.query = AsyncMock(
            side_effect=LightRAGClientError(
                "LightRAG query failed: HTTP 500 - Internal server error",
                original_type="HTTPStatusError",
                original_message="Server Error",
                response_body="internal details",
                status_code=500,
            )
        )
        original_fallback = RAGQueryResult(
            response="",
            provider="rule-based-fallback",
            fallback=True,
        )
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with patch.object(
            selector._fallback,  # type: ignore[attr-defined]
            "query",
            new_callable=AsyncMock,
            return_value=original_fallback,
        ):
            result = await selector.query(query="test")

        assert result.degradation_reason == "upstream_error"
        # The upstream body must NEVER appear on the returned result
        # (AC-7 redacts it on the wire; here we prove it never even
        # touches the data layer's domain object).
        assert "internal details" not in result.response

    @pytest.mark.asyncio
    async def test_query_does_not_swallow_without_exc_info(self, caplog) -> None:
        """Regression guard: bare ``logger.warning('...')`` (no exc_info) fails."""
        from nfm_db.services.rag_provider import RAGProviderSelector

        client = _make_mock_lightrag_client()
        client.query = AsyncMock(side_effect=LightRAGClientError("boom"))
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )
        original_fallback = RAGQueryResult(
            response="", provider="rule-based-fallback", fallback=True
        )

        with (
            patch.object(
                selector._fallback,  # type: ignore[attr-defined]
                "query",
                new_callable=AsyncMock,
                return_value=original_fallback,
            ),
            caplog.at_level("WARNING", logger="nfm_db.services.rag_provider"),
        ):
            await selector.query(query="test")

        # The exception must be logged with exc_info — the previous
        # bare-message swallow made it invisible to operators.
        exc_records = [r for r in caplog.records if r.exc_info is not None]
        assert exc_records, (
            "LightRAGClientError swallowed without traceback — must log exc_info=True"
        )

    @pytest.mark.asyncio
    async def test_ingest_fallback_logs_with_exc_info(self, caplog) -> None:
        """Ingest swallow site also logs with exc_info=True."""
        from nfm_db.services.rag_provider import RAGProviderSelector

        client = _make_mock_lightrag_client()
        client.ingest = AsyncMock(side_effect=LightRAGClientError("ingest boom"))
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type]
        )

        with caplog.at_level("WARNING", logger="nfm_db.services.rag_provider"):
            await selector.ingest(text="some text", source="doc.pdf")

        exc_records = [r for r in caplog.records if r.exc_info is not None]
        assert exc_records, (
            "LightRAGClientError on ingest swallowed without traceback — "
            "must log exc_info=True"
        )


class TestRAGQueryResultNewFields:
    """NFM-3407 — RAGQueryResult gains degradation_reason and request_id."""

    def test_default_values(self) -> None:
        """New fields default to None — back-compat for existing callers."""
        result = RAGQueryResult(response="hello")
        assert result.degradation_reason is None
        assert result.request_id is None

    def test_with_new_fields(self) -> None:
        result = RAGQueryResult(
            response="degraded answer",
            fallback=True,
            degradation_reason="upstream_timeout",
            request_id="req-abc",
        )
        assert result.degradation_reason == "upstream_timeout"
        assert result.request_id == "req-abc"

    def test_frozen_immutability_with_new_fields(self) -> None:
        result = RAGQueryResult(
            response="x", degradation_reason="upstream_error", request_id="req-1"
        )
        with pytest.raises(AttributeError):
            result.degradation_reason = "upstream_timeout"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.request_id = "req-2"  # type: ignore[misc]
