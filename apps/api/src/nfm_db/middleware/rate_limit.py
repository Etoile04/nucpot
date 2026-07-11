"""Global rate limiting middleware — NFM-1073.

Extends the existing ``InProcessRateLimiter`` from ``nfm_db.services.rate_limit``
into a Starlette ``BaseHTTPMiddleware`` applied to all ``/api/`` routes except
``/api/v1/health``.  No new external dependencies.

Env vars:
    ``RATE_LIMIT_MAX_REQUESTS``   default ``60``
    ``RATE_LIMIT_WINDOW_SECONDS`` default ``60``
    ``RATE_LIMIT_ENABLED``        default ``true``
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Default policy (per NFM-1073 spec).
_DEFAULT_MAX_REQUESTS = 60
_DEFAULT_WINDOW_SECONDS = 60

# Paths that bypass the global rate limiter.
_EXEMPT_PATHS = {"/api/v1/health"}


class GlobalRateLimitLimiter:
    """In-process sliding-window counter keyed by client IP.

    Same algorithm as ``nfm_db.services.rate_limit.InProcessRateLimiter``
    but returns structured data instead of raising ``HTTPException``, so
    the middleware can add headers and customise the JSON response.
    """

    def __init__(
        self,
        max_requests: int = _DEFAULT_MAX_REQUESTS,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._enabled = enabled
        self._hits: dict[str, list[float]] = defaultdict(list)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, key: str) -> dict:
        """Check the rate limit for *key*.

        Returns a dict with:
        - ``allowed`` (bool)
        - ``remaining`` (int)
        - ``reset`` (float — monotonic timestamp when oldest hit expires)
        - ``retry_after`` (int | None — seconds to wait, only when not allowed)
        """
        if not self._enabled:
            return {
                "allowed": True,
                "remaining": -1,
                "reset": 0.0,
                "retry_after": None,
            }

        now = time.monotonic()
        bucket = [ts for ts in self._hits[key] if ts > now - self._window]
        self._hits[key] = bucket

        remaining = max(0, self._max - len(bucket))

        if len(bucket) >= self._max:
            oldest = bucket[0]
            retry_after = max(1, int(oldest + self._window - now) + 1)
            return {
                "allowed": False,
                "remaining": 0,
                "reset": oldest + self._window,
                "retry_after": retry_after,
            }

        bucket.append(now)
        oldest = bucket[0]
        return {
            "allowed": True,
            "remaining": remaining - 1,
            "reset": oldest + self._window,
            "retry_after": None,
        }

    def reset(self) -> None:
        """Clear all buckets — used by tests to isolate state."""
        self._hits.clear()


def create_global_rate_limiter(
    max_requests: int | None = None,
    window_seconds: int | None = None,
    enabled: bool | None = None,
) -> GlobalRateLimitLimiter:
    """Factory: build a ``GlobalRateLimitLimiter`` from env vars or overrides."""
    if max_requests is None:
        max_requests = int(
            os.environ.get("RATE_LIMIT_MAX_REQUESTS", _DEFAULT_MAX_REQUESTS)
        )
    if window_seconds is None:
        window_seconds = int(
            os.environ.get("RATE_LIMIT_WINDOW_SECONDS", _DEFAULT_WINDOW_SECONDS)
        )
    if enabled is None:
        enabled = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )

    return GlobalRateLimitLimiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
        enabled=enabled,
    )


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces global per-IP rate limits.

    Applied to all ``/api/`` routes.  The health endpoint (and any path in
    ``_EXEMPT_PATHS``) is completely skipped.
    """

    def __init__(
        self,
        app: Callable,
        limiter: GlobalRateLimitLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or create_global_rate_limiter()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Exempt health and non-API paths.
        if path in _EXEMPT_PATHS or not path.startswith("/api/"):
            return await call_next(request)

        # Determine client key.
        host = request.client.host if request.client else "anonymous"
        key = f"global:{host}"

        result = self._limiter.check(key)

        if not self._limiter.enabled:
            return await call_next(request)

        if not result["allowed"]:
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": "Rate limit exceeded. Please retry later.",
                    "error_code": "RATE_LIMIT_EXCEEDED",
                },
                headers={
                    "Retry-After": str(result["retry_after"]),
                    "X-RateLimit-Limit": str(self._limiter._max),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(result["reset"])),
                },
            )

        # Inject rate-limit headers into the downstream response.
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._limiter._max)
        response.headers["X-RateLimit-Remaining"] = str(result["remaining"])
        response.headers["X-RateLimit-Reset"] = str(int(result["reset"]))
        return response
