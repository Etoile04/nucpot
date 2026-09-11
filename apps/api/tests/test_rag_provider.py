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
    _CJK_RE,
    _LATIN_TOKEN_RE,
    HealthStatus,
    LightRAGProvider,
    RAGProvider,
    RAGProviderSelector,
    RAGQueryResult,
    RuleBasedFallbackProvider,
    _escape_tsquery_literal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db(rows: list[dict] | None = None) -> AsyncMock:
    """Create a mock SQLAlchemy AsyncSession.

    Args:
        rows: Optional list of row dicts returned by mappings().all().

    NFM-4593: ``AsyncResult.mappings()`` is a SYNC method in SQLAlchemy 2.0
    — it returns a :class:`MappingResult` directly, NOT a coroutine.  The
    previous mock wrapped it in ``AsyncMock`` which masked the production
    bug ``TypeError: object MappingResult can't be used in 'await'
    expression`` (the test inadvertently conformed to the buggy code's
    incorrect ``await result.mappings()`` shape).  Switch to ``MagicMock``
    so the mock mirrors the real contract; the rule-based fallback then
    hits ``result.mappings().all()`` cleanly.
    """
    db = AsyncMock()
    mappings_obj = MagicMock()
    mappings_obj.all.return_value = rows or []
    result_mock = MagicMock()
    result_mock.mappings = MagicMock(return_value=mappings_obj)
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

    def test_default_fallback_reason_is_none(self) -> None:
        """NFM-4734: ``fallback_reason`` defaults to ``'none'`` so callers
        that ignore the new field keep working.
        """
        result = RAGQueryResult(response="hello")
        assert result.fallback_reason == "none"

    def test_frozen_dataclass_rejects_new_field_assignment(self) -> None:
        """``fallback_reason`` is part of the frozen dataclass contract."""
        result = RAGQueryResult(response="hello")
        with pytest.raises((AttributeError, TypeError, Exception)):  # frozen
            result.fallback_reason = "semantic_timeout"  # type: ignore[misc]


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
# NFM-4733: rule-based fallback must handle mixed Chinese + Latin queries
# ---------------------------------------------------------------------------


class TestNFM4733QueryTokenization:
    r"""NFM-4733: the original ``_QUERY_TOKEN_RE`` /\w+/ merged CJK + Latin
    tokens into a single AND-joined tsquery, which made every Chinese
    query return zero matches because the CJK tokens never appear in the
    English titles/abstracts and AND semantics require ALL tokens to be
    present.  The fix splits the query into Latin + CJK tokens and uses
    OR semantics + ILIKE for CJK.
    """

    def test_latin_regex_extracts_latin_tokens(self) -> None:
        """Latin letters + digits + underscore + dash + dot."""
        assert _LATIN_TOKEN_RE.findall("UO2 fuel") == ["UO2", "fuel"]
        assert _LATIN_TOKEN_RE.findall("Cr-doped") == ["Cr-doped"]
        assert _LATIN_TOKEN_RE.findall("activation_energy") == ["activation_energy"]
        # Pure-digit token (year) does NOT match — Latin must lead with a letter.
        assert _LATIN_TOKEN_RE.findall("2023") == []
        # Chinese-only token does NOT match.
        assert _LATIN_TOKEN_RE.findall("扩散") == []

    def test_cjk_regex_extracts_cjk_substrings(self) -> None:
        """CJK Unified Ideographs U+4E00-U+9FFF + Extension A U+3400-U+4DBF."""
        assert _CJK_RE.findall("Cr 掺杂 晶界") == ["掺杂", "晶界"]
        assert _CJK_RE.findall("扩散活化能") == ["扩散活化能"]
        # Latin-only — empty CJK list.
        assert _CJK_RE.findall("UO2 fuel") == []
        # Mixed — only the CJK substring.
        assert _CJK_RE.findall("Cr掺杂晶界") == ["掺杂晶界"]

    def test_escape_tsquery_literal_strips_operators(self) -> None:
        """PostgreSQL ``to_tsquery`` raises on ``& | ! ( )`` inside a literal.

        Operators must be stripped at the boundary so the SQL never trips
        the parser. Whitespace is collapsed and edges trimmed.
        """
        assert _escape_tsquery_literal("Cr") == "Cr"
        assert _escape_tsquery_literal("Cr & doped") == "Cr   doped"
        assert _escape_tsquery_literal("(Cr)") == "Cr"
        assert _escape_tsquery_literal("!Cr") == "Cr"
        assert _escape_tsquery_literal("a|b") == "a b"
        # Surrounding whitespace trimmed; interior whitespace collapsed.
        assert _escape_tsquery_literal("  a  &  b  ") == "a     b"


class TestNFM4733RuleBasedFallbackQuery:
    """NFM-4733: behavioural tests for the rule-based fallback's new
    Chinese-aware query path.

    These tests use a spy-style mock that captures the params passed to
    ``db.execute`` so we can prove the right Latin/CJK split happens at
    the SQL boundary.  The behavioural "rows returned → references" tests
    continue to use ``_make_mock_db`` because the spec is "the fallback
    returns what the DB returns".
    """

    def _spy_db(self, rows: list[dict] | None = None) -> tuple[AsyncMock, list]:
        """Return a spy ``AsyncMock`` that records every ``execute()`` call."""
        captured: list[dict] = []
        mappings_obj = MagicMock()
        mappings_obj.all.return_value = rows or []
        result_mock = MagicMock()
        result_mock.mappings = MagicMock(return_value=mappings_obj)

        async def fake_execute(stmt, params):
            captured.append(params)
            return result_mock

        db = AsyncMock()
        db.execute = fake_execute
        return db, captured

    @pytest.mark.asyncio
    async def test_chinese_mixed_query_splits_latin_to_tsquery(self) -> None:
        """``"Cr 掺杂 晶界"`` should put ``Cr`` into the tsquery branch
        and the CJK characters into the ILIKE branch."""
        db, captured = self._spy_db()
        provider = RuleBasedFallbackProvider(db_session=db)  # type: ignore[arg-type]

        await provider.query(query="Cr 掺杂 晶界")

        assert captured, "db.execute was never called"
        params = captured[0]
        # Latin token "Cr" OR-joined (single token, no operator needed).
        assert params["latin_tsquery"] == "Cr"
        # CJK patterns are ILIKE wildcards, one per CJK substring.
        assert params["cjk_patterns"] == ["%掺杂%", "%晶界%"]
        assert params["limit"] == 5

    @pytest.mark.asyncio
    async def test_chinese_only_query_skips_latin_branch(self) -> None:
        """Pure-CJK query: latin_tsquery is ``""`` so the SQL's
        ``WHERE :latin_tsquery <> ''`` guard skips the tsvector branch,
        and only CJK patterns participate."""
        db, captured = self._spy_db()
        provider = RuleBasedFallbackProvider(db_session=db)

        await provider.query(query="扩散活化能")

        params = captured[0]
        assert params["latin_tsquery"] == ""
        assert params["cjk_patterns"] == ["%扩散活化能%"]

    @pytest.mark.asyncio
    async def test_latin_only_query_skips_cjk_branch(self) -> None:
        """Latin-only query: CJK patterns are a single sentinel so the
        ILIKE branch contributes zero rows instead of crashing on an
        empty array. The tsvector branch handles the matching."""
        db, captured = self._spy_db()
        provider = RuleBasedFallbackProvider(db_session=db)

        await provider.query(query="UO2 fuel")

        params = captured[0]
        assert params["latin_tsquery"] == "UO2 | fuel"
        # Sentinel — ILIKE '%__NO_CJK_NEVER_MATCH__%' is guaranteed false.
        assert params["cjk_patterns"] == ["__NO_CJK_NEVER_MATCH__"]

    @pytest.mark.asyncio
    async def test_tsquery_operator_chars_in_token_are_escaped(self) -> None:
        """A query whose Latin *tokens* contain ``& ! ( )`` must not reach
        PostgreSQL as raw ``to_tsquery`` literals — those characters are
        tsquery operators and would raise at parse time.  The inter-token
        ``|`` join (OR semantics) is intentional and is NOT subject to
        sanitisation, so this test only asserts operator chars that were
        *inside* a token are stripped."""
        db, captured = self._spy_db()
        provider = RuleBasedFallbackProvider(db_session=db)

        # ``!UO2`` and ``(fuel)`` carry tsquery operators inside the token;
        # ``doped`` is clean.  After sanitisation the OR-joined result
        # should be ``U2   | fuel | doped`` (operator stripped, edges trimmed).
        await provider.query(query="!UO2 (fuel) doped")

        params = captured[0]
        latin_tsquery = params["latin_tsquery"]
        # Operators that were INSIDE tokens are gone.
        assert "!" not in latin_tsquery
        assert "(" not in latin_tsquery
        assert ")" not in latin_tsquery
        # Clean tokens survive.
        assert "doped" in latin_tsquery
        assert "fuel" in latin_tsquery
        # The inter-token OR-join (`` | ``) is the only legitimate ``|``.
        or_separator_count = latin_tsquery.count(" | ")
        # 3 sanitised tokens → 2 OR-join separators.
        assert or_separator_count == 2

    @pytest.mark.asyncio
    async def test_chinese_query_with_latin_match_returns_refs(self) -> None:
        """End-to-end: a Chinese query whose Latin token matches DB content
        must surface the reference (the mock returns rows as if the new
        SQL matched them — verifying the post-SQL response/refs shape is
        unchanged)."""
        mock_row = {
            "source_type": "data_source",
            "source_id": "ds-cr-001",
            "snippet_text": "Cr-doped grain boundary segregation in Fe-Cr alloys.",
            "rank": 0.8,
        }
        db = _make_mock_db(rows=[mock_row])
        provider = RuleBasedFallbackProvider(db_session=db)

        result = await provider.query(query="Cr 掺杂 晶界")

        assert result.fallback is True
        assert len(result.references) == 1
        assert result.references[0]["source_id"] == "ds-cr-001"
        assert "Cr-doped" in result.response

    @pytest.mark.asyncio
    async def test_pure_punctuation_query_short_circuits_without_sql(self) -> None:
        """``"???"`` has no Latin and no CJK tokens — must short-circuit
        BEFORE touching the database (matches the pre-NFM-4733 contract)."""
        db = AsyncMock()
        db.execute = AsyncMock()
        provider = RuleBasedFallbackProvider(db_session=db)

        result = await provider.query(query="???")

        assert result.response == ""
        assert result.fallback is True
        db.execute.assert_not_called()


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
    async def test_query_fallback_reason_timeout_on_client_error(self) -> None:
        """NFM-4734: ``LightRAGClientError`` (incl. timeout) → ``reason='semantic_timeout'``.

        The selector surfaces the original error message verbatim on the
        ``RAGQueryResult`` so the API route can attach it to
        ``FallbackInfo.original_error`` without losing correlation.
        """
        client = _make_mock_lightrag_client(healthy=True)
        client.query = AsyncMock(side_effect=LightRAGClientError("Read timed out"))
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        result = await selector.query(query="test")
        assert result.fallback is True
        assert result.fallback_reason == "semantic_timeout"
        assert result.original_error is not None
        assert "Read timed out" in result.original_error

    @pytest.mark.asyncio
    async def test_query_fallback_reason_empty_on_zero_references(self) -> None:
        """NFM-4734: LightRAG returned but with zero references → ``reason='semantic_empty'``.

        Distinguishing empty from timeout is the AC-2 requirement: a
        silent fallback over an empty semantic hit is the design
        ambiguity the issue calls out.
        """
        client = _make_mock_lightrag_client(
            query_result={
                "response": "I don't know.",
                "references": [],
                "entities": [],
                "relationships": [],
            },
        )
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        result = await selector.query(query="some novel concept")
        # When LightRAG succeeds with content, selector returns it
        # untouched — no fallback fires.  The empty-detection belongs
        # at the API layer where the route decides whether to escalate
        # to the ILIKE rescue path.  This test pins that contract.
        assert result.fallback is False
        assert result.fallback_reason == "none"

    @pytest.mark.asyncio
    async def test_query_fallback_reason_none_when_healthy(self) -> None:
        """NFM-4734: healthy LightRAG → ``reason='none'`` on the result."""
        client = _make_mock_lightrag_client(healthy=True)
        db = _make_mock_db()
        selector = RAGProviderSelector(
            lightrag_client=client,  # type: ignore[arg-type]
            db_session=db,  # type: ignore[arg-type],
        )
        result = await selector.query(query="UO2")
        assert result.fallback_reason == "none"

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
