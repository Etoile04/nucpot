"""NFM-4539 / RAG-B: response contract + access_log.

AC-4: timeout (≥30s) → ILIKE fallback + response ``fallback.used=true``
       + UI badge (the UI half is exercised in the frontend tests).
AC-7: ``access_log`` exposes the canonical fields
       ``mode``, ``was_fallback``, ``was_cached``, ``query_kind``,
       ``result_count``, ``time_total``.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from nfm_db.models.rag_access_log import RagAccessLog
from nfm_db.services.lightrag_client import LightRAGClientError

# ===========================================================================
# Schema — fallback envelope
# ===========================================================================


def test_query_response_schema_has_fallback_field() -> None:
    """``QueryResponse`` schema must declare a ``fallback`` field."""
    from nfm_db.schemas.lightrag import QueryResponse

    fields = QueryResponse.model_fields
    assert "fallback" in fields, "QueryResponse must declare a `fallback` field (NFM-4539 RAG-B)"


def test_fallback_envelope_shape() -> None:
    """``fallback`` must carry ``used``, ``kind``, ``original_error``."""
    from nfm_db.schemas.lightrag import FallbackInfo

    fields = FallbackInfo.model_fields
    assert "used" in fields
    assert "kind" in fields
    assert "original_error" in fields


# ===========================================================================
# AC-4 — fallback field on timeout
# ===========================================================================


@pytest.mark.asyncio
async def test_query_success_returns_fallback_unused(
    async_client: AsyncClient,
) -> None:
    """Successful LightRAG query: ``fallback.used=false``."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "UO2 is a nuclear fuel.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "What is UO2?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "fallback" in body["data"]
    assert body["data"]["fallback"]["used"] is False
    assert body["data"]["fallback"]["kind"] is None
    assert body["data"]["fallback"]["original_error"] is None


@pytest.mark.asyncio
async def test_query_timeout_triggers_ilike_fallback(
    async_client: AsyncClient,
) -> None:
    """When the sidecar exceeds the budget, the API actually invokes the
    ILIKE rescue path and returns the rescued references.

    NFM-4539 RAG-B AC-4 fix: the previous implementation set
    ``fallback.used=true`` without performing the ILIKE/tsvector search,
    making the §3.2 badge a lie.  This test now patches
    ``RuleBasedFallbackProvider.query`` to return a known payload and
    asserts the route surfaces it.  If a future regression reverts to the
    "set used=true without rescuing" pattern, the references assertion
    will fail.
    """
    from nfm_db.services.rag_provider import RAGQueryResult

    fallback_references = [
        {
            "source_type": "data_source",
            "source_id": "ds-001",
            "score": 0.93,
        },
        {
            "source_type": "material",
            "source_id": "mat-042",
            "score": 0.71,
        },
    ]
    # NFM-4736 AC-6: rule-based fallback wrapper localized to Chinese.
    fallback_response = "规则回退命中 2 条相关结果(查询:UO2)。"

    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        # Simulate the sidecar timing out — clients raise TimeoutError on
        # the read budget.
        mock_client.query.side_effect = LightRAGClientError(
            "LightRAG query failed: Read timeout",
        )
        mock_get_client.return_value = mock_client

        with patch(
            "nfm_db.services.rag_provider.RuleBasedFallbackProvider.query",
            new_callable=AsyncMock,
        ) as mock_fallback_query:
            mock_fallback_query.return_value = RAGQueryResult(
                response=fallback_response,
                references=fallback_references,
                provider="rule-based-fallback",
                fallback=True,
            )

            response = await async_client.post(
                "/api/v1/lightrag/query",
                json={"query": "UO2 conductivity"},
            )

    assert response.status_code == 200
    body = response.json()
    # AC-4 envelope — fallback.used is set only because the rescue path
    # actually ran (the patch above proves it).
    assert body["data"]["fallback"]["used"] is True
    assert body["data"]["fallback"]["kind"] == "iliKE"
    # NFM-4734 §3 / AC-2: on the degraded path the route projects the
    # selector's ``fallback_reason`` and ``original_error`` onto the
    # envelope so the §3.2 badge carries machine-readable provenance.
    # Previously this asserted ``original_error is None`` (steady-state
    # contract); the NFM-4734 selector now stamps
    # ``fallback_reason='semantic_timeout'`` on ``LightRAGClientError``
    # and threads the exception text verbatim.
    assert body["data"]["fallback"]["reason"] == "semantic_timeout"
    assert body["data"]["fallback"]["original_error"] is not None
    assert "Read timeout" in body["data"]["fallback"]["original_error"]
    # NFM-4734 §3 / AC-2: the route also stamps X-RAG-Fallback-Reason
    # on the degraded path; steady state stays header-clean.
    assert response.headers.get("X-RAG-Fallback-Reason") == "semantic_timeout"
    # AC-4 substance: the response carries the real ILIKE-rescued
    # references, not empty arrays.  This is the assertion the original
    # test was missing — the badge's claim is now grounded in the data.
    assert len(body["data"]["references"]) == len(fallback_references)
    assert body["data"]["references"][0]["source_type"] == "data_source"
    assert body["data"]["references"][1]["source_type"] == "material"
    assert body["data"]["response"] == fallback_response
    # The fallback provider was actually invoked — proof of rescue.
    mock_fallback_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_success_does_not_invoke_fallback(
    async_client: AsyncClient,
) -> None:
    """A successful LightRAG response must NOT invoke RuleBasedFallbackProvider.

    Counterpart to ``test_query_timeout_triggers_ilike_fallback``: when
    the primary provider succeeds, the rescue path stays cold.  Guards
    against a future refactor that double-fires (selector + rescue).
    """
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "UO2 is a nuclear fuel.",
            "references": [],
            "entities": [],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        with patch(
            "nfm_db.services.rag_provider.RuleBasedFallbackProvider.query",
            new_callable=AsyncMock,
        ) as mock_fallback_query:
            response = await async_client.post(
                "/api/v1/lightrag/query",
                json={"query": "What is UO2?"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["fallback"]["used"] is False
    mock_fallback_query.assert_not_called()


# ===========================================================================
# AC-7 — access_log
# ===========================================================================


@pytest.mark.asyncio
async def test_query_persists_access_log_row(
    async_client: AsyncClient,
    db_session,
) -> None:
    """A successful query writes one ``RagAccessLog`` row with all AC-7 fields."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = {
            "response": "UO2 thermal conductivity is 3.5 W/mK.",
            "references": [
                {"source": "Finkelstein 2001", "page": 42},
            ],
            "entities": [{"name": "UO2"}],
            "relationships": [],
        }
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "UO2 thermal conductivity", "mode": "mix"},
        )

    assert response.status_code == 200

    # Read back via the test session.
    rows = (await db_session.execute(select(RagAccessLog).order_by(RagAccessLog.ts.desc()))).all()
    assert len(rows) >= 1, "Query must persist at least one RagAccessLog row"
    row = rows[0][0]
    # AC-7 fields
    assert row.mode == "mix"
    assert row.was_fallback is False
    assert row.was_cached is False
    assert row.query_kind in ("semantic", "text", "ilike", None)
    assert row.result_count == 1
    assert row.time_total >= 0.0


@pytest.mark.asyncio
async def test_query_timeout_persists_was_fallback_true(
    async_client: AsyncClient,
    db_session,
) -> None:
    """When the fallback fires, ``was_fallback`` is True and the kind is logged."""
    from nfm_db.services.rag_provider import RAGQueryResult

    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.side_effect = LightRAGClientError("sidecar stalled")
        mock_get_client.return_value = mock_client

        with patch(
            "nfm_db.services.rag_provider.RuleBasedFallbackProvider.query",
            new_callable=AsyncMock,
        ) as mock_fallback_query:
            mock_fallback_query.return_value = RAGQueryResult(
                response="fallback answer",
                references=[{"source_type": "material", "source_id": "m-1"}],
                provider="rule-based-fallback",
                fallback=True,
            )

            response = await async_client.post(
                "/api/v1/lightrag/query",
                json={"query": "MOX fuel behavior", "mode": "hybrid"},
            )

    assert response.status_code == 200
    rows = (
        await db_session.execute(select(RagAccessLog).where(RagAccessLog.was_fallback.is_(True)))
    ).all()
    assert len(rows) >= 1
    row = rows[0][0]
    assert row.mode == "hybrid"
    assert row.was_fallback is True
    assert row.time_total > 0.0


@pytest.mark.asyncio
async def test_query_records_time_total(
    async_client: AsyncClient,
    db_session,
) -> None:
    """``time_total`` is a non-negative float capturing end-to-end wall time."""
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()

        # Force a measurable wall time.
        async def _slow() -> dict:
            time.sleep(0.05)
            return {
                "response": "ok",
                "references": [],
                "entities": [],
                "relationships": [],
            }

        async def _slow_call(**_kwargs) -> dict:
            time.sleep(0.05)
            return {
                "response": "ok",
                "references": [],
                "entities": [],
                "relationships": [],
            }

        mock_client.query.side_effect = _slow_call
        mock_get_client.return_value = mock_client

        await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "UO2"},
        )

    rows = (await db_session.execute(select(RagAccessLog).order_by(RagAccessLog.ts.desc()))).all()
    assert rows, "Expected at least one access_log row"
    row = rows[0][0]
    assert row.time_total >= 0.05, f"time_total must reflect wall time (got {row.time_total})"
