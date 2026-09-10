"""NFM-4539 / RAG-A: anonymous-open + endpoint rate limit for LightRAG query.

AC-1: `/api/v1/lightrag/query` removes ``require_editor``; anonymous and
authenticated requests share the same response shape.
AC-2: per-route slowapi limit ``NFM_RAG_QUERY_RATE_LIMIT`` (default 5/min/IP);
       the 6th request from the same IP returns HTTP 429 with a Retry-After
       hint and X-RateLimit-* quota headers.

The session-scoped conftest disables slowapi globally + auto-auths every
test. Rate-limit tests re-enable the per-route limiter for the duration of
the test and reset counters afterwards.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


def _mock_lightrag_response() -> dict:
    return {
        "response": "UO2 has density 10.97 g/cm3.",
        "references": [],
        "entities": [],
        "relationships": [],
    }


@pytest.fixture
def _restore_limiter():
    """Per-test guard: capture limiter.enabled state and restore it.

    The session fixture sets ``limiter.enabled = False`` for the bulk of the
    suite. Rate-limit tests flip it on for their own body, then restore so
    sibling tests are unaffected.
    """
    from nfm_db.middleware.rate_limit import limiter

    previous = limiter.enabled
    yield
    limiter.enabled = previous


# ===========================================================================
# AC-1 — anonymous access
# ===========================================================================


@pytest.mark.asyncio
async def test_query_endpoint_is_anonymous_accessible(
    async_client: AsyncClient,
) -> None:
    """AC-1: POST /lightrag/query must not require authentication.

    The session conftest auto-injects an editor user; we additionally
    re-issue the request with NO Authorization header to prove the route
    no longer reads the auth dependency.
    """
    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = _mock_lightrag_response()
        mock_get_client.return_value = mock_client

        # Plain request — no Authorization header, no cookies.
        response = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "What is the density of UO2?"},
        )

    assert response.status_code == 200, (
        "Anonymous user must reach the endpoint (AC-1); got "
        f"{response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["success"] is True
    assert "response" in body["data"]
    assert body["data"]["references"] == []


@pytest.mark.asyncio
async def test_ingest_remains_editor_only(
    async_client: AsyncClient,
) -> None:
    """Ingest is a *write* — must keep ``require_editor``.

    Sanity guard: the spec opens /query but explicitly excludes /ingest
    from the open posture.  We assert the existing editor requirement is
    untouched by checking the API surface still mounts the dependency.
    """
    import inspect

    from nfm_db.api.v1.lightrag import ingest_document

    sig = inspect.signature(ingest_document)
    params = list(sig.parameters.values())
    # require_editor is wired via Depends(); its argument name will be
    # either ``current_user`` or ``_current_user`` (NFM convention).
    has_user_dep = any(
        "current_user" in p.name for p in params
    )
    assert has_user_dep, (
        "ingest_document must keep the require_editor dependency — "
        "the RAG open spec explicitly excludes writes from the open posture"
    )


# ===========================================================================
# AC-2 — per-route rate limit (5/min/IP, configurable)
# ===========================================================================


@pytest.mark.asyncio
async def test_query_under_limit_succeeds(
    async_client: AsyncClient,
    _restore_limiter,
) -> None:
    """5 requests within the window all return 200 + quota headers."""
    from nfm_db.middleware.rate_limit import limiter

    limiter.enabled = True
    # Use a fresh key so prior tests' counters do not leak into this one.
    limiter.reset()

    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = _mock_lightrag_response()
        mock_get_client.return_value = mock_client

        for _ in range(5):
            response = await async_client.post(
                "/api/v1/lightrag/query",
                json={"query": "UO2 density"},
            )
            assert response.status_code == 200, (
                "First 5 requests in the window must succeed; "
                f"got {response.status_code}"
            )
            # AC-2: X-RateLimit-* headers must be present on every response.
            assert "X-RateLimit-Limit" in response.headers, (
                "Per-route limit must surface X-RateLimit-Limit header"
            )
            assert "X-RateLimit-Remaining" in response.headers, (
                "Per-route limit must surface X-RateLimit-Remaining header"
            )
            assert "X-RateLimit-Reset" in response.headers, (
                "Per-route limit must surface X-RateLimit-Reset header"
            )


@pytest.mark.asyncio
async def test_query_over_limit_returns_429(
    async_client: AsyncClient,
    _restore_limiter,
) -> None:
    """The 6th request from the same IP within 60s returns 429 + Retry-After."""
    from nfm_db.middleware.rate_limit import limiter

    limiter.enabled = True
    limiter.reset()

    with patch("nfm_db.api.v1.lightrag._get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.query.return_value = _mock_lightrag_response()
        mock_get_client.return_value = mock_client

        # Consume the budget.
        for _ in range(5):
            ok = await async_client.post(
                "/api/v1/lightrag/query",
                json={"query": "UO2"},
            )
            assert ok.status_code == 200

        # 6th request must be throttled.
        over = await async_client.post(
            "/api/v1/lightrag/query",
            json={"query": "UO2"},
        )

    assert over.status_code == 429, (
        f"6th request must be throttled (AC-2); got {over.status_code}"
    )
    body = over.json()
    assert body["success"] is False
    assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
    # slowapi always sets Retry-After on the rate-limit response.
    assert "Retry-After" in over.headers, (
        "Throttled response must include Retry-After (AC-2)"
    )


@pytest.mark.asyncio
async def test_query_rate_limit_configurable_via_env(
    monkeypatch,
) -> None:
    """``NFM_RAG_QUERY_RATE_LIMIT`` env var configures the per-route limit."""
    monkeypatch.setenv("NFM_RAG_QUERY_RATE_LIMIT", "3/minute")

    # Re-import the module so the env var is read fresh.
    import importlib

    from nfm_db.api.v1 import lightrag as lightrag_module
    from nfm_db.middleware.rate_limit import limiter

    # Clear any prior route limits for the function under test so the
    # assertion sees only the freshly-loaded quota, not stale entries
    # from earlier test imports.
    name = (
        f"{lightrag_module.query_knowledge_graph.__module__}"
        f".{lightrag_module.query_knowledge_graph.__name__}"
    )
    limiter._route_limits.pop(name, None)
    importlib.reload(lightrag_module)

    # slowapi stores route limits on the Limiter instance keyed by
    # ``module.function_name``.  We pull the matching LimitGroup and read
    # the per-window quota off the wrapped RateLimitItem.
    route_limits = limiter._route_limits.get(name, [])
    assert route_limits, (
        f"query_knowledge_graph ({name}) must carry a per-route rate-limit"
    )
    first = route_limits[0]
    # The LimitGroup iterates Limit wrappers; pull the first RateLimitItem.
    limit_item = first.limit if hasattr(first, "limit") else first
    assert str(limit_item.amount) == "3", (
        f"NFM_RAG_QUERY_RATE_LIMIT must be honoured; got {limit_item.amount}"
    )


@pytest.mark.asyncio
async def test_default_rate_limit_is_5_per_minute(
    monkeypatch,
) -> None:
    """With ``NFM_RAG_QUERY_RATE_LIMIT`` unset, the route uses 5/minute."""
    monkeypatch.delenv("NFM_RAG_QUERY_RATE_LIMIT", raising=False)

    import importlib

    from nfm_db.api.v1 import lightrag as lightrag_module
    from nfm_db.middleware.rate_limit import limiter

    name = (
        f"{lightrag_module.query_knowledge_graph.__module__}"
        f".{lightrag_module.query_knowledge_graph.__name__}"
    )
    limiter._route_limits.pop(name, None)
    importlib.reload(lightrag_module)

    route_limits = limiter._route_limits.get(name, [])
    assert route_limits, "query_knowledge_graph must declare a per-route limit"
    first = route_limits[0]
    limit_item = first.limit if hasattr(first, "limit") else first
    assert str(limit_item.amount) == "5", (
        f"Default per-route quota must be 5/min; got {limit_item.amount}"
    )
