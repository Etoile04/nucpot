"""Per-IP rate limiting for the ontology NVL endpoint (T5).

A small, deterministic, in-process sliding-window limiter exposed as a FastAPI
dependency scoped to the ontology route. The NFM-266 plan names ``slowapi`` "or
equivalent"; this equivalent is chosen because:

* zero new dependencies (no env churn), and
* fully deterministic in tests (the limiter instance is injectable/overridable,
  avoiding the flaky in-memory-state behaviour that would otherwise make the
  429 gate non-reproducible in CI).

For a multi-instance / HA deploy, swap this for slowapi backed by Redis — the
dependency seam (``ontology_rate_limit``) is the only integration point.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

# Phase 1 policy (per the NFM-266 plan): ~30 requests / minute per client.
DEFAULT_MAX_REQUESTS = 30
DEFAULT_WINDOW_SECONDS = 60


class InProcessRateLimiter:
    """Sliding-window counter keyed by client identity.

    Single-instance only. ``check`` raises an HTTP 429 when ``key`` has already
    consumed ``max_requests`` within the trailing ``window_seconds``.
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = [ts for ts in self._hits[key] if ts > now - self._window]
        self._hits[key] = bucket

        if len(bucket) >= self._max:
            retry_after = max(1, int(bucket[0] + self._window - now) + 1)
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)

    def reset(self) -> None:
        """Clear all buckets — used by tests to isolate state."""
        self._hits.clear()


def client_key(request: Request, *, route: str = "ontology") -> str:
    """Stable per-client key (falls back to 'anonymous' under TestClient)."""
    host = request.client.host if request.client else "anonymous"
    return f"{route}:{host}"


# Production singleton — overridden or reset in tests.
ontology_limiter = InProcessRateLimiter()

# MD verification: tighter limit (5/min) since jobs dispatch expensive SSH+SLURM ops.
MD_VERIFICATION_MAX_REQUESTS = 5
MD_VERIFICATION_WINDOW_SECONDS = 60

md_verification_limiter = InProcessRateLimiter(
    max_requests=MD_VERIFICATION_MAX_REQUESTS,
    window_seconds=MD_VERIFICATION_WINDOW_SECONDS,
)


def make_rate_limit_dependency(
    limiter: InProcessRateLimiter,
    *,
    route: str = "ontology",
) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency bound to a specific limiter instance."""

    async def _rate_limit(request: Request) -> None:
        limiter.check(client_key(request, route=route))

    return _rate_limit


# Production dependency — enforces the per-IP limit on the ontology route.
ontology_rate_limit = make_rate_limit_dependency(ontology_limiter)

# Production dependency — enforces the per-IP limit on MD verification job
# submission (NFM-401). Tighter than ontology because each request dispatches
# an SSH+SLURM job on shared HPC hardware.
md_verification_rate_limit = make_rate_limit_dependency(
    md_verification_limiter,
    route="md-verification",
)
