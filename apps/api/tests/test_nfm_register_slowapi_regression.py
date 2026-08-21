"""Regression tests for NFM-3352 — slowapi middleware 500 on register endpoint.

POST /api/v1/auth/register succeeded in DB but its response path crashed with
HTTP 500 because ``slowapi.Limiter._inject_headers`` failed its
``isinstance(response, starlette.responses.Response)`` check on ``None``.

Root cause
----------
slowapi's ``@limiter.limit`` decorator wraps the endpoint in
``async_wrapper`` (see ``slowapi/extension.py`` ~ line 737).  When the
endpoint returns a non-``Response`` (e.g. a Pydantic model), the wrapper
falls back to ``kwargs.get("response")`` — the FastAPI-injected
``Response`` parameter — to find the response object for header
injection.  If the endpoint has *no* ``response: Response`` parameter,
that ``kwargs.get`` returns ``None`` and ``_inject_headers(None, ...)``
crashes:

    raise Exception(
        "parameter `response` must be an instance of "
        "starlette.responses.Response"
    )

The /auth/register endpoint returned a Pydantic ``User`` model and did
not declare a ``response: Response`` parameter, while the sibling
``/login`` and ``/refresh`` endpoints did.  Adding ``response: Response``
to the register signature restores parity and resolves the crash.

These tests guard against regression in two layers:

1. *Signature guard* — the production ``register`` function MUST declare
   a ``response: Response`` parameter (otherwise the crash returns).
2. *Behaviour guard* — a slowapi ``@limiter.limit`` route that DOES
   declare ``response: Response`` returns 201 with X-RateLimit-* headers
   and a 429 (not 500) after the per-minute limit is exhausted.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from nfm_db.middleware.rate_limit import NFMRateLimitMiddleware

# ---------------------------------------------------------------------------
# Layer 1: signature guard — production register() must declare
#          ``response: Response`` so slowapi's async_wrapper has an actual
#          Response object to inject headers into.
# ---------------------------------------------------------------------------


def test_register_endpoint_declares_response_parameter() -> None:
    """Regression guard: register() MUST have a ``response: Response`` parameter.

    slowapi's @limiter.limit decorator wrapper does::

        response = await func(*args, **kwargs)
        if not isinstance(response, Response):
            self._inject_headers(kwargs.get("response"), ...)  # may be None

    Without a ``response`` parameter, ``kwargs.get("response")`` is None
    and ``_inject_headers`` raises — crashing the response with HTTP 500
    even though the DB row was already committed.
    """
    from nfm_db.api.v1.auth_endpoints import register

    sig = inspect.signature(register)
    assert "response" in sig.parameters, (
        "register() must accept `response: Response` — slowapi's "
        "@limiter.limit decorator uses it to inject X-RateLimit-* headers"
    )
    response_param = sig.parameters["response"]
    # Starlette Response is the expected annotation.  Accept either the
    # concrete class or a string annotation equivalent.
    annotation = (
        response_param.annotation
        if response_param.annotation is not inspect.Parameter.empty
        else None
    )
    assert annotation in (Response, "Response"), (
        f"`response` parameter must be annotated as starlette.Response, "
        f"got {annotation!r}"
    )


# ---------------------------------------------------------------------------
# Layer 2: behaviour guard — slowapi @limiter.limit + response: Response
#          returns 201 with X-RateLimit-* headers and 429 after exhaustion.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _enable_production_limiter(monkeypatch: pytest.MonkeyPatch) -> Limiter:
    """Replace the production slowapi Limiter with a fresh one.

    The session-scoped conftest disables rate-limiting for the whole suite;
    these regression tests need it enabled.  We swap the production
    singleton for a fresh ``Limiter`` per test so the in-memory bucket
    starts empty and is isolated from other tests — the production
    ``limiter.reset()`` clears the bucket but only after prior tests have
    consumed slots against the same client key (``testclient:anonymous``).

    Restoring ``monkeypatch.undo()`` in the teardown puts the production
    singleton back so other tests see the disabled-by-conftest state.
    """
    from slowapi.util import get_remote_address

    from nfm_db.middleware import rate_limit as rl_mod

    fresh = Limiter(
        key_func=get_remote_address,
        application_limits=["100/minute", "20/second"],
        storage_uri="memory://",
        headers_enabled=True,
    )
    monkeypatch.setattr(rl_mod, "limiter", fresh)
    return fresh


@pytest.fixture()
def _register_app(_enable_production_limiter: Limiter) -> FastAPI:
    """Minimal app that mirrors the FIXED production register shape.

    Unlike the buggy original, this stub DOES declare ``response: Response``
    so slowapi's ``async_wrapper`` finds a real ``Response`` for header
    injection instead of falling through to ``kwargs.get("response")``
    returning ``None``.
    """
    app = FastAPI()
    app.state.limiter = _enable_production_limiter

    def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": "Rate limit exceeded",
                "error_code": "RATE_LIMIT_EXCEEDED",
            },
        )

    app.exception_handlers[RateLimitExceeded] = _rate_limit_handler

    @app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
    @_enable_production_limiter.limit("3/minute")
    async def register(request: Request, response: Response) -> dict[str, str]:
        """Stub register endpoint that mirrors the FIXED production shape."""
        return {"username": "stub", "email": "stub@example.com"}

    app.add_middleware(NFMRateLimitMiddleware)
    return app


@pytest.mark.asyncio
async def test_register_with_response_param_returns_201(
    _register_app: FastAPI,
) -> None:
    """With ``response: Response`` declared, /register returns 201 (not 500)."""
    transport = ASGITransport(app=_register_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/register", json={})

    assert resp.status_code == 201, (
        f"register must succeed (201), got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_register_response_includes_ratelimit_headers(
    _register_app: FastAPI,
) -> None:
    """Successful register response must carry X-RateLimit-* headers."""
    transport = ASGITransport(app=_register_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/register", json={})

    assert resp.status_code == 201
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert "x-ratelimit-limit" in headers, (
        "X-RateLimit-Limit missing — slowapi header injection failed"
    )
    assert "x-ratelimit-remaining" in headers
    assert "x-ratelimit-reset" in headers


@pytest.mark.asyncio
async def test_register_429_after_exhausting_limit(
    _register_app: FastAPI,
) -> None:
    """3/minute limit — 4th request must return 429, never 500."""
    transport = ASGITransport(app=_register_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            ok = await client.post("/api/v1/auth/register", json={})
            assert ok.status_code == 201

        rejected = await client.post("/api/v1/auth/register", json={})
        assert rejected.status_code == 429, (
            f"4th register must return 429, got {rejected.status_code}: {rejected.text}"
        )
        body = rejected.json()
        assert body["success"] is False
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
