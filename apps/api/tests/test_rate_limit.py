"""Unit tests for rate_limit (T5, NFM-266).

Pure mock-based tests that run with --noconftest.

Tests for:
- InProcessRateLimiter: sliding window, 429 raising, reset
- client_key: key generation from request
- make_rate_limit_dependency: dependency factory
- Module-level singletons: ontology_limiter, md_verification_limiter
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from nfm_db.services.rate_limit import (
    DEFAULT_MAX_REQUESTS,
    DEFAULT_WINDOW_SECONDS,
    InProcessRateLimiter,
    MD_VERIFICATION_MAX_REQUESTS,
    MD_VERIFICATION_WINDOW_SECONDS,
    client_key,
    make_rate_limit_dependency,
    md_verification_limiter,
    md_verification_rate_limit,
    ontology_limiter,
    ontology_rate_limit,
)


# ---------------------------------------------------------------------------
# InProcessRateLimiter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInProcessRateLimiter:
    """Test sliding-window rate limiter."""

    def test_allows_requests_under_limit(self):
        limiter = InProcessRateLimiter(max_requests=3, window_seconds=60)
        limiter.check("client_a")
        limiter.check("client_a")
        limiter.check("client_a")
        # No exception raised

    def test_raises_429_at_limit(self):
        limiter = InProcessRateLimiter(max_requests=2, window_seconds=60)
        limiter.check("client_b")
        limiter.check("client_b")

        with pytest.raises(HTTPException) as exc_info:
            limiter.check("client_b")

        assert exc_info.value.status_code == 429
        assert "rate limit" in exc_info.value.detail.lower()

    def test_429_includes_retry_after_header(self):
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("client_c")

        with pytest.raises(HTTPException) as exc_info:
            limiter.check("client_c")

        assert "Retry-After" in exc_info.value.headers
        retry_after = int(exc_info.value.headers["Retry-After"])
        assert retry_after >= 1

    def test_separate_keys_are_independent(self):
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("client_x")
        # Different key should still be allowed
        limiter.check("client_y")

    def test_expired_hits_removed_from_bucket(self):
        """Hits outside the window are pruned."""
        limiter = InProcessRateLimiter(max_requests=2, window_seconds=1)

        limiter.check("client_d")
        limiter.check("client_d")

        # Wait for window to expire
        time.sleep(1.1)

        # Should be allowed again since old hits expired
        limiter.check("client_d")

    def test_reset_clears_all_buckets(self):
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("client_e")

        with pytest.raises(HTTPException):
            limiter.check("client_e")

        limiter.reset()

        # After reset, should be allowed
        limiter.check("client_e")

    def test_default_max_requests(self):
        limiter = InProcessRateLimiter()
        assert limiter._max == DEFAULT_MAX_REQUESTS
        assert limiter._window == DEFAULT_WINDOW_SECONDS

    def test_retry_after_minimum_is_1(self):
        """Retry-After header is at least 1 second."""
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=1)
        limiter.check("client_f")

        with pytest.raises(HTTPException) as exc_info:
            limiter.check("client_f")

        retry_after = int(exc_info.value.headers["Retry-After"])
        assert retry_after >= 1

    def test_multiple_keys_tracked_independently(self):
        """Many clients can each have their own buckets."""
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)

        for i in range(10):
            limiter.check(f"client_{i}")

        # Each client used 1 of 1 request, so all are at limit
        for i in range(10):
            with pytest.raises(HTTPException):
                limiter.check(f"client_{i}")


# ---------------------------------------------------------------------------
# client_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClientKey:
    """Test client key generation."""

    def test_key_includes_route_and_host(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock()
        request.client.host = "192.168.1.1"

        key = client_key(request, route="ontology")

        assert key == "ontology:192.168.1.1"

    def test_key_defaults_to_anonymous_without_client(self):
        request = MagicMock(spec=Request)
        request.client = None

        key = client_key(request, route="ontology")

        assert key == "ontology:anonymous"

    def test_key_uses_custom_route(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        key = client_key(request, route="md-verification")

        assert key == "md-verification:10.0.0.1"

    def test_key_default_route_is_ontology(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock()
        request.client.host = "1.2.3.4"

        key = client_key(request)

        assert key == "ontology:1.2.3.4"


# ---------------------------------------------------------------------------
# make_rate_limit_dependency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMakeRateLimitDependency:
    """Test dependency factory."""

    def test_returns_callable(self):
        limiter = InProcessRateLimiter()
        dep = make_rate_limit_dependency(limiter)

        assert callable(dep)

    @pytest.mark.asyncio
    async def test_dependency_calls_limiter_check(self):
        limiter = InProcessRateLimiter()
        dep = make_rate_limit_dependency(limiter, route="test-route")

        request = MagicMock(spec=Request)
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        await dep(request)

        # Key should be "test-route:127.0.0.1"
        assert "test-route:127.0.0.1" in limiter._hits

    @pytest.mark.asyncio
    async def test_dependency_propagates_429(self):
        limiter = InProcessRateLimiter(max_requests=1, window_seconds=60)
        dep = make_rate_limit_dependency(limiter)

        request = MagicMock(spec=Request)
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        # First call passes
        await dep(request)

        # Second call should raise 429
        with pytest.raises(HTTPException, match="rate limit"):
            await dep(request)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleSingletons:
    """Test that module-level singletons are properly configured."""

    def test_ontology_limiter_uses_defaults(self):
        assert ontology_limiter._max == DEFAULT_MAX_REQUESTS
        assert ontology_limiter._window == DEFAULT_WINDOW_SECONDS

    def test_md_verification_limiter_uses_tighter_limits(self):
        assert md_verification_limiter._max == MD_VERIFICATION_MAX_REQUESTS
        assert md_verification_limiter._window == MD_VERIFICATION_WINDOW_SECONDS

    def test_ontology_rate_limit_is_callable(self):
        assert callable(ontology_rate_limit)

    def test_md_verification_rate_limit_is_callable(self):
        assert callable(md_verification_rate_limit)

    def test_md_verification_limiter_allows_fewer_requests(self):
        """MD verification allows fewer requests than ontology."""
        assert MD_VERIFICATION_MAX_REQUESTS < DEFAULT_MAX_REQUESTS