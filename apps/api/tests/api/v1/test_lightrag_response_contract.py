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
    assert "fallback" in fields, (
        "QueryResponse must declare a `fallback` field (NFM-4539 RAG-B)"
    )


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
    """When the sidecar exceeds the budget, the API reports ``iliKE`` fallback.

    The ILIKE rescue lives in the legacy kg.py fallback path — for this
    endpoint we model it as a deterministic swap: timeout ⇒
    ``fallback.used=true, kind='iliKE', original_error=<timeout text>``.
    """
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        # Simulate the sidecar timing out — clients raise TimeoutError on
        # the read budget.
        mock_client.query.side_effect = LightRAGClientError(
            "LightRAG query failed: Read timeout",
        )
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "UO2 conductivity"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["fallback"]["used"] is True
    assert body["data"]["fallback"]["kind"] == "iliKE"
    assert body["data"]["fallback"]["original_error"] is not None
    assert "timeout" in body["data"]["fallback"]["original_error"].lower()


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
    rows = (
        await db_session.execute(select(RagAccessLog).order_by(RagAccessLog.ts.desc()))
    ).all()
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
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.side_effect = LightRAGClientError("sidecar stalled")
        mock_get_client.return_value = mock_client

        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "MOX fuel behavior", "mode": "hybrid"},
        )

    assert response.status_code == 200
    rows = (
        await db_session.execute(
            select(RagAccessLog).where(RagAccessLog.was_fallback.is_(True))
        )
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

    rows = (
        await db_session.execute(select(RagAccessLog).order_by(RagAccessLog.ts.desc()))
    ).all()
    assert rows, "Expected at least one access_log row"
    row = rows[0][0]
    assert row.time_total >= 0.05, (
        f"time_total must reflect wall time (got {row.time_total})"
    )
