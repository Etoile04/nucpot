"""NFM-4593: regression for ``MappingResult can't be used in await expression``.

NFM-4539 round 2 (PR #1285) routed ``/api/v1/lightrag/query`` through
:class:`RAGProviderSelector`, which invokes
:class:`RuleBasedFallbackProvider.query` when the primary LightRAG
sidecar raises :class:`LightRAGClientError`. The fallback implementation
predates NFM-4539 and contained a latent bug:

    rows = (await result.mappings()).all()

In SQLAlchemy 2.0 async, :meth:`AsyncResult.mappings` is a **synchronous**
method that returns a :class:`MappingResult` (a regular sync iterator
wrapper). Awaiting it raises::

    TypeError: object MappingResult can't be used in 'await' expression

The bug was masked in the test suite because:

* The SQLite test environment has no ``ts_rank`` / ``tsvector``, so
  contract tests for the *timeout* path mock
  :class:`RuleBasedFallbackProvider.query` directly with an
  ``AsyncMock`` — bypassing the real implementation.
* The QA route-mock visual verification (``DISABLE_API_REWRITE``)
  exercised the React layer, not the FastAPI provider chain.

Production hit the bug because the prod LightRAG sidecar started
returning 5xx (NFM-4525 qwen3.5 thinking-budget regression), the
selector transparently fell back, and the buggy ``await`` raised the
TypeError on every prod query — anonymous de-wall worked but the
§3.2 honest-fallback path crashed before delivering references.

AC:

* Regression test must exercise the real
  :meth:`RuleBasedFallbackProvider.query` against the production code
  path (not a mock of the method itself).
* Test must FAIL on ``origin/main`` with the original TypeError.
* Test must PASS after the fix (sync ``.mappings().all()``).

This file lives next to ``test_lightrag_response_contract.py`` because
the bug surfaces through the same route, but is split out so a future
NFM-4593 follow-up can extend coverage without bloating the contract
file (the contract file is mock-heavy for SQLite compat).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.rag_provider import (
    RAGQueryResult,
    RuleBasedFallbackProvider,
)


def _make_async_session_with_mappings_result(rows: list[dict]) -> AsyncSession:
    """Return an ``AsyncSession`` whose ``.execute()`` mimics SQLAlchemy 2.0.

    The mock returns an object whose ``.mappings()`` is a **sync** method
    (it returns a ``MagicMock`` directly, not a coroutine). This mirrors
    the real SQLAlchemy 2.0 contract documented at
    https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.engine.Result.mappings
    — awaiting ``.mappings()`` on the async result is a programmer error
    that raises ``TypeError: object MappingResult can't be used in
    'await' expression`` at runtime.
    """
    session = MagicMock(spec=AsyncSession)
    mappings_result = MagicMock()
    mappings_result.all.return_value = rows
    async_result = MagicMock()
    async_result.mappings.return_value = mappings_result
    execute_coro = AsyncMock(return_value=async_result)
    session.execute = execute_coro
    return session


@pytest.mark.asyncio
async def test_rule_based_fallback_query_does_not_await_mappings() -> None:
    """Regression for NFM-4593.

    ``AsyncResult.mappings()`` is sync. Awaiting it (the bug) raises
    ``TypeError: object MappingResult can't be used in 'await' expression``
    which surfaces to the user as ``500-in-200`` ``Query failed: ...``.

    On the buggy code the test fails with the TypeError; on the fixed
    code it returns a :class:`RAGQueryResult` carrying the rescued
    references (or an empty list when no rows match).
    """
    rows = [
        {
            "source_type": "data_source",
            "source_id": "ds-001",
            "snippet_text": "UO2 thermal conductivity 10 W/mK at 300 K.",
            "rank": 0.87,
        },
        {
            "source_type": "material",
            "source_id": "mat-042",
            "snippet_text": "UO2 fuel pellet density 10.97 g/cm^3.",
            "rank": 0.71,
        },
    ]
    session = _make_async_session_with_mappings_result(rows)
    fallback = RuleBasedFallbackProvider(session)

    result = await fallback.query(query="UO2 thermal conductivity", limit=10)

    assert isinstance(result, RAGQueryResult)
    assert result.fallback is True
    assert result.provider == "rule-based-fallback"
    assert len(result.references) == 2
    assert result.references[0]["source_type"] == "data_source"
    assert result.references[0]["source_id"] == "ds-001"
    assert "UO2 thermal conductivity 10 W/mK" in result.response


@pytest.mark.asyncio
async def test_rule_based_fallback_query_empty_result_set() -> None:
    """Empty row set still must not raise — guards against the regression
    even when the SQL returns no matches (no snippet payload).
    """
    session = _make_async_session_with_mappings_result([])
    fallback = RuleBasedFallbackProvider(session)

    result = await fallback.query(query="nonexistent-keyword-xyz", limit=10)

    assert isinstance(result, RAGQueryResult)
    assert result.fallback is True
    assert result.references == []
    # NFM-4736 AC-6: rule-based fallback empty-state must surface honest
    # Chinese copy rather than passthrough the English "No results found".
    assert "未找到与查询" in result.response
    assert "No results found" not in result.response


@pytest.mark.asyncio
async def test_rule_based_fallback_query_no_tokens_returns_empty() -> None:
    """A query with no extractable tokens short-circuits before SQL;
    ensure the early-return path is unaffected by the mappings() fix.
    """
    session = MagicMock(spec=AsyncSession)
    fallback = RuleBasedFallbackProvider(session)

    result = await fallback.query(query="... !!! ???", limit=10)

    assert isinstance(result, RAGQueryResult)
    assert result.fallback is True
    assert result.response == ""
    # SQL must not have been executed for a no-token query.
    session.execute.assert_not_called()
