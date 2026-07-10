"""Global rate limiting middleware using slowapi (NFM-1087).

Uses the ``slowapi`` library with the ``limits`` storage backend.  Only
``/api/`` routes are checked; ``/docs``, ``/redoc``, ``/openapi.json`` etc.
pass through unrestricted.  The health endpoint is exempt via
``@limiter.exempt``.

**Production note:** the default ``memory://`` storage is suitable for
single-instance deployments.  For multi-instance or long-running
deployments, set ``RATE_LIMIT_STORAGE_URI=redis://localhost:6379`` so that
counters are shared across workers and don't grow unboundedly in process
memory.

Env vars
--------
``RATE_LIMIT_DEFAULT``    default ``100/minute``
``RATE_LIMIT_BURST``       default ``20/second``
``RATE_LIMIT_STORAGE_URI`` default ``memory://``
"""

from __future__ import annotations

import os
from typing import Any

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_DEFAULT_LIMIT = os.environ.get("RATE_LIMIT_DEFAULT", "100/minute")
_BURST_LIMIT = os.environ.get("RATE_LIMIT_BURST", "20/second")
_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")

limiter = Limiter(
    key_func=get_remote_address,
    application_limits=[_DEFAULT_LIMIT, _BURST_LIMIT],
    storage_uri=_STORAGE_URI,
    headers_enabled=True,
)


class NFMRateLimitMiddleware(SlowAPIMiddleware):
    """SlowAPIMiddleware scoped to ``/api/`` routes only.

    Non-API routes (``/docs``, ``/redoc``, ``/openapi.json``) are never
    rate-limited.  Health is exempt via ``@limiter.exempt``.
    """

    async def dispatch(
        self, request: Request, call_next: Any
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        return await super().dispatch(request, call_next)


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Custom 429 handler with NFM standard error envelope.

    **Must be sync** — ``SlowAPIMiddleware`` calls via
    ``sync_check_limits`` which falls back to the default handler for
    async callables.
    """
    response_body = {
        "success": False,
        "error": "Rate limit exceeded",
        "error_code": "RATE_LIMIT_EXCEEDED",
    }
    try:
        view_rate_limit = getattr(request.state, "view_rate_limit", None)
        if view_rate_limit is not None:
            response = limiter._inject_headers(
                JSONResponse(status_code=429, content=response_body),
                view_rate_limit,
            )
            return response
    except Exception:
        pass  # never let header injection crash the 429 response
    return JSONResponse(status_code=429, content=response_body)
