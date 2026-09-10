"""API contract tests for GET /api/v1/lightrag/metrics (NFM-4539 RAG-E / AC-8).

Verifies that the metrics endpoint:

  * Returns the documented envelope shape.
  * Is anonymous-accessible (mirrors RAG-A's open posture — the payload
    is aggregate telemetry, not user-identifying).
  * Exposes the three literature totals and the two tier P95s.
"""

from __future__ import annotations

import pytest

NO_AUTO_AUTH = pytest.mark.no_auto_auth


@pytest.mark.asyncio
async def test_metrics_endpoint_anonymous_accessible(
    async_client,
) -> None:
    """The metrics endpoint must NOT require an editor / admin session.

    AC-8 deliberately exposes aggregate telemetry anonymously so the
    public dashboard can poll without a token.  Mirrors RAG-A's
    ``require_editor`` removal on /query.
    """
    response = await async_client.get("/api/v1/lightrag/metrics")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert "data" in body
    payload = body["data"]

    # Literature totals
    assert "lit_completed_total" in payload
    assert "lit_indexed_total" in payload
    assert "lit_diff_count" in payload
    assert "lit_indexed_source" in payload
    assert isinstance(payload["lit_completed_total"], int)
    assert isinstance(payload["lit_indexed_total"], int)
    assert isinstance(payload["lit_diff_count"], int)

    # Tier P95 envelopes
    for tier_key in ("tier_1_p95", "tier_2_p95"):
        tier = payload[tier_key]
        assert "p95_ms" in tier
        assert "sample_size" in tier
        assert "target_ms" in tier
        assert "meets_sla" in tier
        assert isinstance(tier["sample_size"], int)
        assert isinstance(tier["target_ms"], (int, float))

    # Window
    assert payload["window_days"] == 7
    assert "generated_at" in payload


@pytest.mark.asyncio
async def test_metrics_endpoint_404_on_unknown_subpath(
    async_client,
) -> None:
    """The endpoint is mounted at /metrics, not /stats."""
    response = await async_client.get("/api/v1/lightrag/stats")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_zero_state(
    async_client,
) -> None:
    """With no DB rows, the payload still renders with zeros and None P95s."""
    response = await async_client.get("/api/v1/lightrag/metrics")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["lit_completed_total"] == 0
    assert payload["lit_indexed_total"] == 0
    assert payload["lit_diff_count"] == 0
    assert payload["tier_1_p95"]["p95_ms"] is None
    assert payload["tier_2_p95"]["p95_ms"] is None
    assert payload["tier_1_p95"]["sample_size"] == 0
    assert payload["tier_2_p95"]["sample_size"] == 0
