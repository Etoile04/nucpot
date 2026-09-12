"""Global rate limiting middleware using slowapi (NFM-1087).

Uses the ``slowapi`` library with the ``limits`` storage backend.  Only
``/api/`` routes are checked; ``/docs``, ``/redoc``, ``/openapi.json`` etc.
pass through unrestricted.  The health endpoint is exempt via
``@limiter.exempt``.

**Production note (NFM-4681):** the prod API runs with 4 uvicorn workers
(``docker/prod-api.Dockerfile``'s ``--workers 4``).  The default
``memory://`` storage is per-process, so each worker holds its own
counter and a single client can spend 4x the budget before the first
429.  ``docker-compose.prod.yml`` therefore sets
``RATE_LIMIT_STORAGE_URI=redis://nucpot-prod-redis:6379/2`` so all
workers share one counter (DB 2 keeps it isolated from Celery on DBs
0/1).  Staging keeps the default ``memory://`` because its Dockerfile
pins a single uvicorn worker.  The IP key is the real visitor IP —
``ProxyHeadersMiddleware`` (see ``nfm_db.main``) already rewrites
``scope['client']`` from ``X-Forwarded-For`` so slowapi's
``get_remote_address`` reflects the public client behind the CF tunnel.

Env vars
--------
``RATE_LIMIT_DEFAULT``    default ``100/minute``
``RATE_LIMIT_BURST``       default ``20/second``
``RATE_LIMIT_STORAGE_URI`` default ``memory://``
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = os.environ.get("RATE_LIMIT_DEFAULT", "100/minute")
_BURST_LIMIT = os.environ.get("RATE_LIMIT_BURST", "20/second")
_STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")

limiter = Limiter(
    key_func=get_remote_address,
    application_limits=[_DEFAULT_LIMIT, _BURST_LIMIT],
    storage_uri=_STORAGE_URI,
    headers_enabled=True,
)

# NFM-4736 F2: the multi-worker shared-counter wiring was silently inert in
# prod — the env var was present in the container, yet live bursts behaved
# as per-process ``memory://`` counters (24 parallel requests: 14 got 429,
# consistent with 4 workers each enforcing 5/minute independently); zero
# limiter keys existed in any Redis DB. A per-process counter multiplies the budget by the worker count,
# which is exactly what NFM-4681 wired the shared Redis storage to
# prevent, so a silent regression here is a security-relevant drift, not a
# cosmetic one. Log the effective backend at import (once per worker) and
# scream if the configured URI and the materialized storage disagree —
# this turns any recurrence of that failure mode into a one-deploy
# observation instead of a forensic session.
_STORAGE_BACKEND = type(getattr(limiter, "_storage", None)).__name__
logger.info(
    "rate-limit storage backend resolved: %s (RATE_LIMIT_STORAGE_URI=%s)",
    _STORAGE_BACKEND,
    _STORAGE_URI,
)
if _STORAGE_URI.startswith(("redis://", "rediss://")) and _STORAGE_BACKEND in (
    "MemoryStorage",
    "Storage",
):
    logger.error(
        "rate-limit storage drift: RATE_LIMIT_STORAGE_URI=%s but the "
        "materialized backend is %s — limits are being enforced "
        "per-process, multiplying the effective budget by the uvicorn "
        "worker count. Check container env propagation and Redis "
        "reachability from the API container.",
        _STORAGE_URI,
        _STORAGE_BACKEND,
    )


def _inject_global_headers(
    limiter_instance: Limiter, request: Request, response: Response
) -> Response:
    """Inject X-RateLimit-* headers for globally-limited routes.

    slowapi's ``SlowAPIMiddleware`` only injects headers when a per-endpoint
    ``@limiter.limit()`` decorator sets ``request.state.view_rate_limit``.
    Routes that rely solely on ``application_limits`` (e.g. /materials) pass
    through without headers, making quota invisible to clients.

    This helper iterates the first ``application_limits`` ``LimitGroup`` to
    derive window stats so every API response carries transparent rate-limit
    info.
    """
    if not limiter_instance.enabled or not limiter_instance._headers_enabled:
        return response
    app_limit_groups = getattr(limiter_instance, "_application_limits", None)
    if not app_limit_groups:
        return response
    try:
        # _application_limits is a list of LimitGroup objects.  With a request
        # context the group iterates to produce Limit wrappers around
        # RateLimitItem.  We take the first Limit from the first group.
        limit_group = app_limit_groups[0].with_request(request)
        limit_objects = list(limit_group)
        if not limit_objects:
            return response
        first_limit = limit_objects[0]
        rate_limit_item = first_limit.limit
        key = first_limit.key_func(request)
        window_stats = limiter_instance.limiter.get_window_stats(rate_limit_item, key)
        reset_in = 1 + window_stats[0]
        response.headers["X-RateLimit-Limit"] = str(rate_limit_item.amount)
        response.headers["X-RateLimit-Remaining"] = str(window_stats[1])
        response.headers["X-RateLimit-Reset"] = str(reset_in)
    except Exception:
        logger.debug("rate-limit header injection failed", exc_info=True)
    return response


class NFMRateLimitMiddleware(SlowAPIMiddleware):
    """SlowAPIMiddleware scoped to ``/api/`` routes only.

    Non-API routes (``/docs``, ``/redoc``, ``/openapi.json``) are never
    rate-limited.  Health is exempt via ``@limiter.exempt``.

    Additionally injects global rate-limit headers on responses for routes
    that lack a per-endpoint ``@limiter.limit()`` decorator, so clients
    always see quota transparency.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return cast(Response, await call_next(request))
        response = await super().dispatch(request, call_next)
        # If slowapi didn't inject headers (no per-endpoint @limiter.limit),
        # inject global application_limits headers ourselves so every API
        # response carries transparent rate-limit info.  This applies to
        # exempt routes (e.g. health) too — the headers are informational
        # only; the actual rate-limit exemption is unaffected.
        if "X-RateLimit-Limit" not in response.headers:
            _inject_global_headers(limiter, request, response)
        return response


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
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
        logger.debug("rate-limit header injection failed on 429 response", exc_info=True)
    return JSONResponse(status_code=429, content=response_body)
