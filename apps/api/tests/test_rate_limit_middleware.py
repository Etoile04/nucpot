"""Tests for global rate limiting middleware — NFM-1073.

RED phase: tests written before implementation.
Middleware uses existing InProcessRateLimiter (no slowapi/external deps).
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers: build a minimal FastAPI app with the middleware
# ---------------------------------------------------------------------------


def _make_app(
    max_requests: int = 60,
    window_seconds: int = 60,
    enabled: bool = True,
):
    """Create a minimal FastAPI app with the global rate limit middleware."""
    from fastapi import APIRouter, FastAPI

    from nfm_db.middleware.rate_limit import (
        GlobalRateLimitMiddleware,
        create_global_rate_limiter,
    )

    app = FastAPI()

    limiter = create_global_rate_limiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
        enabled=enabled,
    )
    app.add_middleware(
        GlobalRateLimitMiddleware,
        limiter=limiter,
    )

    # Health router
    health_router = APIRouter()

    @health_router.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    # Test router
    test_router = APIRouter()

    @test_router.get("/api/v1/test")
    async def test_endpoint():
        return {"data": "ok"}

    app.include_router(health_router)
    app.include_router(test_router)

    return app, limiter


@pytest.fixture
async def client():
    """Async test client with default rate limit (60/min)."""
    app, limiter = _make_app(max_requests=60, window_seconds=60)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    limiter.reset()


@pytest.fixture
async def tight_client():
    """Async test client with tight rate limit (5/min) for 429 testing."""
    app, limiter = _make_app(max_requests=5, window_seconds=60)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    limiter.reset()


@pytest.fixture
async def disabled_client():
    """Async test client with rate limiting disabled."""
    app, limiter = _make_app(max_requests=5, window_seconds=60, enabled=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    limiter.reset()


# ---------------------------------------------------------------------------
# AC: Global middleware returns 429 with Retry-After header when exceeded
# ---------------------------------------------------------------------------


class TestRateLimit429Response:
    """When limit exceeded, response must be 429 with Retry-After header."""

    @pytest.mark.asyncio
    async def test_429_after_limit_exceeded(self, tight_client: AsyncClient) -> None:
        """After hitting the limit, next request must return 429."""
        for i in range(5):
            resp = await tight_client.get("/api/v1/test")
            assert resp.status_code == 200, f"request {i+1} returned {resp.status_code}"

        # 6th request should be 429
        resp = await tight_client.get("/api/v1/test")
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}"

    @pytest.mark.asyncio
    async def test_429_includes_retry_after_header(
        self, tight_client: AsyncClient,
    ) -> None:
        """429 response must include Retry-After header."""
        for _ in range(5):
            await tight_client.get("/api/v1/test")

        resp = await tight_client.get("/api/v1/test")
        assert resp.status_code == 429
        headers_lower = {k.lower(): v for k, v in resp.headers.items()}
        assert "retry-after" in headers_lower
        retry_after = int(headers_lower["retry-after"])
        assert retry_after >= 1

    @pytest.mark.asyncio
    async def test_429_json_body(self, tight_client: AsyncClient) -> None:
        """429 response body must be JSON with error details."""
        for _ in range(5):
            await tight_client.get("/api/v1/test")

        resp = await tight_client.get("/api/v1/test")
        assert resp.status_code == 429
        body = resp.json()
        assert body.get("success") is False
        assert "rate limit" in body.get("error", "").lower()


# ---------------------------------------------------------------------------
# AC: Health endpoint is exempt from rate limiting
# ---------------------------------------------------------------------------


class TestHealthExempt:
    """Health endpoint must never receive a 429."""

    @pytest.mark.asyncio
    async def test_health_unlimited(self, tight_client: AsyncClient) -> None:
        """Burst well above limit — health must always return 200."""
        for i in range(150):
            resp = await tight_client.get("/api/v1/health")
            assert resp.status_code == 200, f"health returned {resp.status_code} on request {i+1}"

    @pytest.mark.asyncio
    async def test_health_does_not_consume_limit(
        self, tight_client: AsyncClient,
    ) -> None:
        """Health requests should not consume the rate limit bucket."""
        # Burn health requests
        for _ in range(50):
            await tight_client.get("/api/v1/health")

        # Should still have all 5 requests available
        for _ in range(5):
            resp = await tight_client.get("/api/v1/test")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC: RATE_LIMIT_MAX_REQUESTS and RATE_LIMIT_WINDOW_SECONDS env vars
# ---------------------------------------------------------------------------


class TestEnvVarConfig:
    """Rate limits configurable via environment variables."""

    def test_env_var_max_requests(self) -> None:
        """RATE_LIMIT_MAX_REQUESTS env var controls the limit."""
        with patch.dict(os.environ, {"RATE_LIMIT_MAX_REQUESTS": "3"}, clear=False):
            from nfm_db.middleware.rate_limit import create_global_rate_limiter

            limiter = create_global_rate_limiter()
            assert limiter._max == 3

    def test_env_var_window_seconds(self) -> None:
        """RATE_LIMIT_WINDOW_SECONDS env var controls the window."""
        with patch.dict(
            os.environ, {"RATE_LIMIT_WINDOW_SECONDS": "120"}, clear=False,
        ):
            from nfm_db.middleware.rate_limit import create_global_rate_limiter

            limiter = create_global_rate_limiter()
            assert limiter._window == 120

    def test_default_values_without_env_vars(self) -> None:
        """Default values when env vars not set."""
        with patch.dict(os.environ, {}, clear=True):
            from nfm_db.middleware.rate_limit import create_global_rate_limiter

            limiter = create_global_rate_limiter()
            assert limiter._max == 60
            assert limiter._window == 60

    @pytest.mark.asyncio
    async def test_limiter_respects_configured_limit(self) -> None:
        """Middleware with max_requests=2 should 429 on 3rd request."""
        app, limiter = _make_app(max_requests=2, window_seconds=60)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/api/v1/test")
            await c.get("/api/v1/test")
            resp = await c.get("/api/v1/test")
            assert resp.status_code == 429
        limiter.reset()


# ---------------------------------------------------------------------------
# AC: Rate limit headers present on all API responses
# ---------------------------------------------------------------------------


class TestRateLimitHeaders:
    """Rate-limited responses must include X-RateLimit headers."""

    @pytest.mark.asyncio
    async def test_headers_on_successful_request(
        self, tight_client: AsyncClient,
    ) -> None:
        """X-RateLimit headers should be present on 200 responses."""
        resp = await tight_client.get("/api/v1/test")
        assert resp.status_code == 200
        headers_lower = {k.lower(): v for k, v in resp.headers.items()}
        assert "x-ratelimit-limit" in headers_lower
        assert "x-ratelimit-remaining" in headers_lower
        assert "x-ratelimit-reset" in headers_lower

    @pytest.mark.asyncio
    async def test_remaining_decreases(self, tight_client: AsyncClient) -> None:
        """X-RateLimit-Remaining should decrease with each request."""
        resp1 = await tight_client.get("/api/v1/test")
        remaining1 = int(resp1.headers.get("x-ratelimit-remaining", "-1"))

        resp2 = await tight_client.get("/api/v1/test")
        remaining2 = int(resp2.headers.get("x-ratelimit-remaining", "-1"))

        assert remaining2 < remaining1

    @pytest.mark.asyncio
    async def test_remaining_zero_on_429(
        self, tight_client: AsyncClient,
    ) -> None:
        """X-RateLimit-Remaining should be 0 when 429 returned."""
        for _ in range(5):
            await tight_client.get("/api/v1/test")

        resp = await tight_client.get("/api/v1/test")
        assert resp.status_code == 429
        remaining = int(resp.headers.get("x-ratelimit-remaining", "-1"))
        assert remaining == 0


# ---------------------------------------------------------------------------
# AC: Existing per-endpoint rate limits still apply as tighter bounds
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing per-endpoint limiters must work alongside global middleware."""

    def test_existing_limiter_still_works(self) -> None:
        """InProcessRateLimiter from services/rate_limit.py still functions."""
        from nfm_db.services.rate_limit import InProcessRateLimiter

        limiter = InProcessRateLimiter(max_requests=3, window_seconds=60)
        limiter.check("key1")
        limiter.check("key1")
        limiter.check("key1")
        with pytest.raises(Exception):  # HTTPException
            limiter.check("key1")

    def test_different_keys_are_independent(self) -> None:
        """Different client keys should have independent buckets."""
        from nfm_db.services.rate_limit import InProcessRateLimiter

        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("client-a")
        limiter.check("client-b")  # Different key, should be fine


# ---------------------------------------------------------------------------
# AC: Rate limiting can be disabled
# ---------------------------------------------------------------------------


class TestDisabledRateLimiting:
    """When disabled, no requests should be blocked."""

    @pytest.mark.asyncio
    async def test_no_429_when_disabled(
        self, disabled_client: AsyncClient,
    ) -> None:
        """With rate limiting disabled, even 100+ requests should succeed."""
        for i in range(100):
            resp = await disabled_client.get("/api/v1/test")
            assert resp.status_code == 200, f"request {i+1} returned {resp.status_code}"

    @pytest.mark.asyncio
    async def test_no_headers_when_disabled(
        self, disabled_client: AsyncClient,
    ) -> None:
        """Headers should not be added when rate limiting is disabled."""
        resp = await disabled_client.get("/api/v1/test")
        headers_lower = {k.lower() for k in resp.headers}
        assert "x-ratelimit-limit" not in headers_lower


# ---------------------------------------------------------------------------
# Coverage: window expiry
# ---------------------------------------------------------------------------


class TestWindowExpiry:
    """Old requests should expire from the sliding window."""

    @pytest.mark.asyncio
    async def test_window_resets_after_expiry(self) -> None:
        """After window expires, requests should be allowed again."""
        app, limiter = _make_app(max_requests=2, window_seconds=1)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as c:
            await c.get("/api/v1/test")
            await c.get("/api/v1/test")
            resp = await c.get("/api/v1/test")
            assert resp.status_code == 429

            # Wait for window to expire
            time.sleep(1.1)

            resp = await c.get("/api/v1/test")
            assert resp.status_code == 200

        limiter.reset()
