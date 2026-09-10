"""Tests for RAG-A: anonymous + per-IP rate limit on POST /api/v1/lightrag/query (NFM-4539).

Covers:
* AC-1: 移除 `require_editor`;匿名 + 已登录响应一致
* AC-2: 端点限流 5/min/IP 生效;超限 429 + `Retry-After`
* AC-7: `X-RateLimit-*` headers 暴露在正常响应与 429 上

The conftest auto-disables slowapi and auto-injects an admin user.
For the anonymous path we use ``@pytest.mark.no_auto_auth`` to bypass the
auto-auth override and verify the endpoint works without credentials.

For the rate-limit path we re-enable slowapi on the REAL ``nfm_db.main:app``
(``limiter.enabled = True`` + reset ``middleware_stack`` so
``NFMRateLimitMiddleware`` can be re-added) and exercise the real
``/api/v1/lightrag/query`` router that has the
``@limiter.limit("5/minute")`` decorator. The same MemoryStorage backend
ensures counters do not bleed across tests because slowapi's ``Limiter``
hashes on the route + key by default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from nfm_db.api.v1 import lightrag as lightrag_module
from nfm_db.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_lightrag_client():
    """Patch LightRAGClient so /query returns a deterministic body."""
    with patch.object(lightrag_module, "LightRAGClient") as mock_cls:
        mock_instance = mock_cls.return_value
        mock_instance.query = AsyncMock(
            return_value={
                "response": "UO2 is a ceramic nuclear fuel material.",
                "references": [
                    {
                        "reference_id": "1",
                        "file_path": "/docs/fuel.pdf",
                        "content": ["UO2 properties chunk."],
                    }
                ],
            }
        )
        yield mock_instance


@pytest.fixture
def enable_rag_rate_limit(mock_lightrag_client):
    """Re-enable slowapi and the per-route 5/minute limit for this test.

    The conftest strips ``NFMRateLimitMiddleware`` and disables the limiter
    between tests. We restore both here so the ``@limiter.limit("5/minute")``
    on the real ``/api/v1/lightrag/query`` endpoint is enforced.

    Resetting ``app.middleware_stack = None`` lets us re-add the middleware
    even when the main app has already served a request (which builds the
    stack and blocks ``app.add_middleware`` thereafter).
    """
    from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware, limiter

    limiter.enabled = True
    # Clear any counters leaked by previous tests in this session.
    limiter.reset()
    has_mw = any(mw.cls is NFMRateLimitMiddleware for mw in app.user_middleware)
    if not has_mw:
        # Reset the built stack so add_middleware does not raise.
        app.middleware_stack = None
        app.add_middleware(NFMRateLimitMiddleware)
    yield
    limiter.enabled = False
    limiter.reset()
    app.user_middleware = [
        mw for mw in app.user_middleware if mw.cls is not NFMRateLimitMiddleware
    ]
    app.middleware_stack = None


# ---------------------------------------------------------------------------
# AC-1: 匿名访问 / 已登录响应一致
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
class TestAnonymousQueryAllowed:
    """POST /api/v1/lightrag/query must work without any auth header."""

    @pytest.mark.asyncio
    async def test_anonymous_query_returns_200(self, mock_lightrag_client) -> None:
        """No Authorization header → 200, identical shape to authenticated."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            payload = {"query": "What are the properties of UO2?", "mode": "mix"}
            response = await ac.post("/api/v1/lightrag/query", json=payload)

        assert response.status_code == 200, (
            f"anonymous POST /query must succeed (got {response.status_code}: {response.text})"
        )
        body = response.json()
        assert body["success"] is True
        assert "UO2" in body["data"]["response"]
        assert isinstance(body["data"]["references"], list)


# ---------------------------------------------------------------------------
# AC-2: 端点限流 5/min/IP, 超限 429 + Retry-After
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
class TestQueryEndpointRateLimit:
    """POST /api/v1/lightrag/query limited to 5/minute per remote IP."""

    @pytest.mark.asyncio
    async def test_sixth_request_within_minute_returns_429(
        self, mock_lightrag_client, enable_rag_rate_limit
    ) -> None:
        """5 hits succeed; 6th hit from the same IP returns 429."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            statuses = []
            for _ in range(6):
                resp = await ac.post(
                    "/api/v1/lightrag/query",
                    json={"query": "UO2 thermal conductivity"},
                )
                statuses.append(resp.status_code)

        assert statuses[:5] == [200, 200, 200, 200, 200], (
            f"first 5 requests must succeed; got {statuses[:5]}"
        )
        assert statuses[5] == 429, f"6th request must be rate-limited (got {statuses[5]})"

    @pytest.mark.asyncio
    async def test_429_includes_retry_after(
        self, mock_lightrag_client, enable_rag_rate_limit
    ) -> None:
        """The 429 envelope must surface a Retry-After header."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for _ in range(5):
                await ac.post("/api/v1/lightrag/query", json={"query": "x"})
            sixth = await ac.post(
                "/api/v1/lightrag/query", json={"query": "x"}
            )

        assert sixth.status_code == 429
        assert "retry-after" in {k.lower() for k in sixth.headers}


# ---------------------------------------------------------------------------
# AC-7: X-RateLimit-* headers
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
class TestQueryEndpointRateLimitHeaders:
    """Successful 200 responses expose X-RateLimit-Limit=5, X-RateLimit-Remaining."""

    @pytest.mark.asyncio
    async def test_first_response_has_rate_limit_headers(
        self, mock_lightrag_client, enable_rag_rate_limit
    ) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/lightrag/query", json={"query": "UO2"}
            )

        assert response.status_code == 200
        limit = response.headers.get("X-RateLimit-Limit")
        assert limit == "5", f"X-RateLimit-Limit must be 5, got {limit!r}"
        remaining = response.headers.get("X-RateLimit-Remaining")
        assert remaining is not None
        assert int(remaining) >= 0 and int(remaining) <= 4
