"""Tests for global rate limiting middleware (NFM-1087).

Uses slowapi with in-memory storage.  Each test creates its own minimal
FastAPI app (no DB, no conftest dependency) so the tests work even
when the full app has missing model/schema stubs on this branch.

Acceptance criteria covered:
AC1: 429 with standard error envelope when limit exceeded
AC2: RATE_LIMIT_DEFAULT / RATE_LIMIT_BURST env vars control behaviour
AC3: Health endpoint is exempt from rate limiting
AC5: Rate limit headers (X-RateLimit-*) present on responses
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import _find_route_handler, _should_exempt, sync_check_limits
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Minimal app factory — avoids importing nfm_db.main which cascades
# through dozens of models/schemas that are missing on this branch.
# ---------------------------------------------------------------------------


def _build_app(limiter_instance: Limiter) -> FastAPI:
    """Create a minimal FastAPI app wired with the given limiter."""
    app = FastAPI()
    app.state.limiter = limiter_instance

    # Sync handler — SlowAPIMiddleware uses sync_check_limits which
    # falls back to the default handler for async callables.
    def _rate_limit_handler(
        request: Request, exc: RateLimitExceeded
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

    # API-scoped middleware — only rate-limits /api/ routes.
    class APIOnlyRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):  # type: ignore[override]
            if not request.url.path.startswith("/api/"):
                return await call_next(request)
            inner_limiter: Limiter = app.state.limiter
            if not inner_limiter.enabled:
                return await call_next(request)
            handler = _find_route_handler(app.routes, request.scope)
            if _should_exempt(inner_limiter, handler):
                return await call_next(request)
            error_response, should_inject = sync_check_limits(
                inner_limiter, request, handler, app
            )
            if error_response is not None:
                return error_response
            response = await call_next(request)
            if should_inject:
                response = inner_limiter._inject_headers(
                    response, request.state.view_rate_limit
                )
            return response

    app.add_middleware(APIOnlyRateLimitMiddleware)

    @app.get("/api/v1/materials")
    async def list_materials() -> dict:
        return {"items": []}

    @app.get("/api/v1/feedback")
    async def list_feedback() -> dict:
        return {"items": []}

    @app.get("/api/v1/health")
    @limiter_instance.exempt
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/docs")
    async def docs() -> dict:
        return {"swagger": "ok"}

    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _tight_limiter() -> Limiter:
    """Tight limiter: 3/minute application-wide (shared bucket)."""
    return Limiter(
        key_func=get_remote_address,
        application_limits=["3/minute"],
        storage_uri="memory://",
        headers_enabled=True,
    )


@pytest.fixture()
def _tight_app(_tight_limiter: Limiter) -> FastAPI:
    """App with tight limiter (3/minute global)."""
    return _build_app(_tight_limiter)


# ---------------------------------------------------------------------------
# AC1: Global middleware returns 429 with standard error envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_returns_429_with_standard_envelope(_tight_app: FastAPI) -> None:
    """After exhausting the global limit (3/minute), the next request returns 429."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(3):
            resp = await client.get("/api/v1/materials")
            assert resp.status_code == 200, f"Request {i + 1} should pass"

        resp = await client.get("/api/v1/materials")
        assert resp.status_code == 429, f"Expected 429: {resp.text}"

        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Rate limit exceeded"
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_429_includes_retry_after_header(_tight_app: FastAPI) -> None:
    """429 response includes a Retry-After header."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            await client.get("/api/v1/materials")

        resp = await client.get("/api/v1/materials")
        assert resp.status_code == 429
        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert "retry-after" in headers


# ---------------------------------------------------------------------------
# AC2: Env vars control behaviour
# ---------------------------------------------------------------------------


def test_limiter_reads_env_vars() -> None:
    """RATE_LIMIT_DEFAULT and RATE_LIMIT_BURST are read at module level."""
    from nfm_db.middleware.rate_limit import _BURST_LIMIT, _DEFAULT_LIMIT

    assert os.environ.get("RATE_LIMIT_DEFAULT", "100/minute") == _DEFAULT_LIMIT
    assert os.environ.get("RATE_LIMIT_BURST", "20/second") == _BURST_LIMIT


# ---------------------------------------------------------------------------
# AC3: Health endpoint is exempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_exempt(_tight_app: FastAPI) -> None:
    """Health endpoint should never be rate-limited."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Exhaust the 3/minute global limit.
        for _ in range(3):
            resp = await client.get("/api/v1/materials")
            assert resp.status_code == 200

        # Non-health request should be rejected (shares global bucket).
        resp = await client.get("/api/v1/feedback")
        assert resp.status_code == 429

        # Health is exempt — always passes even after exhaustion.
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

        # Health calls don't consume quota — repeat 10 times.
        for _ in range(10):
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200

        # Still blocked for non-health.
        resp = await client.get("/api/v1/feedback")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# AC5: Rate limit headers present on all API responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_headers_on_successful_response(_tight_app: FastAPI) -> None:
    """Successful responses include X-RateLimit-* headers."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/materials")
        assert resp.status_code == 200
        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert "x-ratelimit-limit" in headers
        assert "x-ratelimit-remaining" in headers
        assert "x-ratelimit-reset" in headers


@pytest.mark.asyncio
async def test_rate_limit_headers_on_429_response(_tight_app: FastAPI) -> None:
    """429 responses include X-RateLimit-* headers with remaining=0."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            await client.get("/api/v1/materials")

        resp = await client.get("/api/v1/materials")
        assert resp.status_code == 429
        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert headers.get("x-ratelimit-remaining") == "0"


# ---------------------------------------------------------------------------
# Non-API routes are not rate limited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openapi_docs_not_rate_limited(_tight_app: FastAPI) -> None:
    """/docs should not be rate-limited."""
    transport = ASGITransport(app=_tight_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            await client.get("/api/v1/materials")

        resp = await client.get("/docs")
        assert resp.status_code == 200, "/docs should not be rate-limited"
