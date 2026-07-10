"""Tests for global rate limiting middleware — NFM-1087.

Tests the production ``nfm_db.middleware.rate_limit`` module (slowapi-based).
Uses the same self-contained app factory pattern as ``test_global_rate_limit.py``
to avoid importing the full app.

Complements ``test_global_rate_limit.py`` which covers the AC matrix.
This file adds: backward compatibility, window expiry, health exemption
details, non-API route bypass, and real-module env-var import tests.

NOTE: Tests are function-based (not class-based) because BaseHTTPMiddleware
closures interact badly with ``self`` in pytest class-scoped tests.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


# ---------------------------------------------------------------------------
# App factory — inline middleware to avoid SlowAPIMiddleware/BaseHTTPMiddleware
# issues with httpx ASGITransport in pytest.
# ---------------------------------------------------------------------------


def _build_app(limiter_instance: Limiter) -> FastAPI:
    """Create a minimal FastAPI app with inline rate-limit middleware."""
    from slowapi.middleware import _find_route_handler, _should_exempt, sync_check_limits
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI()
    app.state.limiter = limiter_instance

    def _rate_limit_handler(
        request: Request, exc: RateLimitExceeded,
    ) -> JSONResponse:
        response = JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": "Rate limit exceeded",
                "error_code": "RATE_LIMIT_EXCEEDED",
            },
        )
        try:
            view_rate_limit = getattr(request.state, "view_rate_limit", None)
            if view_rate_limit is not None:
                response = limiter_instance._inject_headers(response, view_rate_limit)
        except Exception:
            pass
        return response

    app.exception_handlers[RateLimitExceeded] = _rate_limit_handler

    class APIOnlyRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            path = request.url.path
            if not path.startswith("/api/"):
                return await call_next(request)
            limiter: Limiter = app.state.limiter
            if not limiter.enabled:
                return await call_next(request)
            handler = _find_route_handler(app.routes, request.scope)
            if _should_exempt(limiter, handler):
                return await call_next(request)
            error_response, should_inject = sync_check_limits(
                limiter, request, handler, app,
            )
            if error_response is not None:
                return error_response
            response = await call_next(request)
            if should_inject:
                response = limiter._inject_headers(
                    response, request.state.view_rate_limit,
                )
            return response

    app.add_middleware(APIOnlyRateLimitMiddleware)

    @app.get("/api/v1/test")
    async def test_endpoint() -> dict:
        return {"data": "ok"}

    @app.get("/api/v1/health")
    @limiter_instance.exempt
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/docs")
    async def docs() -> dict:
        return {"swagger": "ok"}

    return app


@pytest.fixture()
def _limiter_3_per_min() -> Limiter:
    """Limiter allowing 3 requests per minute."""
    return Limiter(
        key_func=get_remote_address,
        default_limits=["3/minute"],
        storage_uri="memory://",
        headers_enabled=True,
    )


@pytest.fixture()
def _app_3(_limiter_3_per_min: Limiter) -> FastAPI:
    """App with 3/minute limiter."""
    return _build_app(_limiter_3_per_min)


@pytest.fixture()
def _limiter_2_per_sec() -> Limiter:
    """Limiter allowing 2 requests per second (for expiry tests)."""
    return Limiter(
        key_func=get_remote_address,
        default_limits=["2/second"],
        storage_uri="memory://",
        headers_enabled=True,
    )


@pytest.fixture()
def _app_2s(_limiter_2_per_sec: Limiter) -> FastAPI:
    """App with 2/second limiter."""
    return _build_app(_limiter_2_per_sec)


# ---------------------------------------------------------------------------
# Env-var integration: import from real module
# ---------------------------------------------------------------------------


def test_default_limit_reads_env() -> None:
    """Module-level _DEFAULT_LIMIT matches RATE_LIMIT_DEFAULT env var."""
    from nfm_db.middleware.rate_limit import _DEFAULT_LIMIT

    assert _DEFAULT_LIMIT == os.environ.get("RATE_LIMIT_DEFAULT", "100/minute")


def test_burst_limit_reads_env() -> None:
    """Module-level _BURST_LIMIT matches RATE_LIMIT_BURST env var."""
    from nfm_db.middleware.rate_limit import _BURST_LIMIT

    assert _BURST_LIMIT == os.environ.get("RATE_LIMIT_BURST", "20/second")


def test_custom_default_via_env() -> None:
    """Setting RATE_LIMIT_DEFAULT changes the parsed limit string."""
    with patch.dict(os.environ, {"RATE_LIMIT_DEFAULT": "5/minute"}, clear=False):
        import importlib

        import nfm_db.middleware.rate_limit as rl

        importlib.reload(rl)
        assert rl._DEFAULT_LIMIT == "5/minute"


def test_custom_burst_via_env() -> None:
    """Setting RATE_LIMIT_BURST changes the parsed burst string."""
    with patch.dict(os.environ, {"RATE_LIMIT_BURST": "50/second"}, clear=False):
        import importlib

        import nfm_db.middleware.rate_limit as rl

        importlib.reload(rl)
        assert rl._BURST_LIMIT == "50/second"


# ---------------------------------------------------------------------------
# Backward compatibility: per-endpoint limiters still work
# ---------------------------------------------------------------------------


def test_existing_limiter_still_works() -> None:
    """InProcessRateLimiter from services/rate_limit.py still functions."""
    from nfm_db.services.rate_limit import InProcessRateLimiter

    limiter = InProcessRateLimiter(max_requests=3, window_seconds=60)
    limiter.check("key1")
    limiter.check("key1")
    limiter.check("key1")
    with pytest.raises(Exception):
        limiter.check("key1")


def test_different_keys_are_independent() -> None:
    """Different client keys should have independent buckets."""
    from nfm_db.services.rate_limit import InProcessRateLimiter

    limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-b")  # Different key, should be fine


# ---------------------------------------------------------------------------
# Window expiry: old requests expire from the window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_resets_after_expiry(_app_2s: FastAPI) -> None:
    """After window expires, requests should be allowed again."""
    transport = ASGITransport(app=_app_2s)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Exhaust 2/second limit
        await client.get("/api/v1/test")
        await client.get("/api/v1/test")
        resp = await client.get("/api/v1/test")
        assert resp.status_code == 429, f"Expected 429, got {resp.status_code}"

        # Wait for 1-second window to expire
        time.sleep(1.1)

        resp = await client.get("/api/v1/test")
        assert resp.status_code == 200, f"Expected 200 after expiry, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Health exemption: detailed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_unlimited_burst(_app_3: FastAPI) -> None:
    """Burst well above limit — health must always return 200."""
    transport = ASGITransport(app=_app_3)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(20):
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200, f"health returned {resp.status_code} on request {i+1}"


@pytest.mark.asyncio
async def test_health_does_not_consume_limit(_app_3: FastAPI) -> None:
    """Health requests should not consume the rate limit bucket."""
    transport = ASGITransport(app=_app_3)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Burn health requests
        for _ in range(10):
            await client.get("/api/v1/health")

        # Should still have all 3 requests available for non-health
        for _ in range(3):
            resp = await client.get("/api/v1/test")
            assert resp.status_code == 200

        # 4th should be 429
        resp = await client.get("/api/v1/test")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Non-API routes: /docs should not be rate-limited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_not_rate_limited(_app_3: FastAPI) -> None:
    """/docs should not be rate-limited even after API limit exhausted."""
    transport = ASGITransport(app=_app_3)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Exhaust the limit
        for _ in range(3):
            await client.get("/api/v1/test")

        # API should be blocked
        resp = await client.get("/api/v1/test")
        assert resp.status_code == 429

        # /docs is not rate-limited
        resp = await client.get("/docs")
        assert resp.status_code == 200
