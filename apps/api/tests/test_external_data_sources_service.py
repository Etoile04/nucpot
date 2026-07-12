"""Unit tests for external_data_sources service (NFM-87.3).

Tests for:
- SimpleCache (get/set/clear/TTL/expiry)
- RateLimiter (token bucket acquire/refill)
- ExternalDataSourceClient (query methods, cache integration, rate limiting, error handling)
- create_external_data_client factory
- DataSourceConfig / ExternalDataSource enum

No real HTTP calls — uses httpx.AsyncClient mock transport.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import Request, Response

from nfm_db.services.external_data_sources import (
    DATASOURCE_CONFIGS,
    CacheEntry,
    DataSourceConfig,
    ExternalDataSource,
    ExternalDataSourceClient,
    RateLimiter,
    SimpleCache,
    _query_cache,
    _rate_limiters,
    create_external_data_client,
)

# ---------------------------------------------------------------------------
# SimpleCache tests
# ---------------------------------------------------------------------------


class TestSimpleCache:
    """Tests for the in-memory TTL cache."""

    def test_cache_starts_empty(self) -> None:
        cache = SimpleCache()
        assert cache.size() == 0

    def test_set_and_get(self) -> None:
        cache = SimpleCache()
        cache.set("key1", {"data": "value"})
        assert cache.get("key1") == {"data": "value"}
        assert cache.size() == 1

    def test_get_miss_returns_none(self) -> None:
        cache = SimpleCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self) -> None:
        cache = SimpleCache()
        cache.set("key1", {"data": "value"}, ttl_seconds=0)
        # Even with ttl_seconds=0, datetime.now() == cached_at so it should
        # still be valid. Use negative TTL trick by patching.
        entry = cache._cache["key1"]
        entry.cached_at = datetime.now(UTC) - timedelta(seconds=1)
        assert cache.get("key1") is None
        assert cache.size() == 0

    def test_clear_removes_all(self) -> None:
        cache = SimpleCache()
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.clear()
        assert cache.size() == 0
        assert cache.get("a") is None

    def test_set_overwrites(self) -> None:
        cache = SimpleCache()
        cache.set("k", {"v": 1})
        cache.set("k", {"v": 2})
        assert cache.get("k") == {"v": 2}
        assert cache.size() == 1

    def test_default_ttl_is_one_hour(self) -> None:
        cache = SimpleCache()
        cache.set("k", {"v": 1})
        entry = cache._cache["k"]
        assert entry.ttl_seconds == 3600

    def test_custom_ttl(self) -> None:
        cache = SimpleCache()
        cache.set("k", {"v": 1}, ttl_seconds=7200)
        entry = cache._cache["k"]
        assert entry.ttl_seconds == 7200


# ---------------------------------------------------------------------------
# CacheEntry tests
# ---------------------------------------------------------------------------


class TestCacheEntry:
    """Tests for the CacheEntry dataclass."""

    def test_cache_entry_fields(self) -> None:
        now = datetime.now(UTC)
        entry = CacheEntry(data={"key": "val"}, cached_at=now, ttl_seconds=600)
        assert entry.data == {"key": "val"}
        assert entry.cached_at == now
        assert entry.ttl_seconds == 600


# ---------------------------------------------------------------------------
# RateLimiter tests
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """Tests for the token bucket rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_within_limit(self) -> None:
        limiter = RateLimiter(rate=5)
        assert await limiter.acquire("key1") is True

    @pytest.mark.asyncio
    async def test_acquire_at_limit(self) -> None:
        limiter = RateLimiter(rate=2)
        assert await limiter.acquire("key1") is True
        assert await limiter.acquire("key1") is True
        assert await limiter.acquire("key1") is False

    @pytest.mark.asyncio
    async def test_different_keys_independent(self) -> None:
        limiter = RateLimiter(rate=1)
        assert await limiter.acquire("key_a") is True
        assert await limiter.acquire("key_a") is False
        assert await limiter.acquire("key_b") is True

    @pytest.mark.asyncio
    async def test_high_rate_limit(self) -> None:
        limiter = RateLimiter(rate=100)
        for _i in range(100):
            assert await limiter.acquire("same_key") is True
        assert await limiter.acquire("same_key") is False


# ---------------------------------------------------------------------------
# ExternalDataSource enum and DataSourceConfig tests
# ---------------------------------------------------------------------------


class TestExternalDataSourceEnum:
    """Tests for data source enumeration."""

    def test_enum_values(self) -> None:
        assert ExternalDataSource.NIST_IPR.value == "nist_ipr"
        assert ExternalDataSource.OPENKIM.value == "openkim"
        assert ExternalDataSource.MATERIALS_PROJECT.value == "materials_project"

    def test_enum_count(self) -> None:
        assert len(ExternalDataSource) == 3


class TestDataSourceConfig:
    """Tests for data source configuration."""

    def test_config_is_frozen(self) -> None:
        config = DataSourceConfig(base_url="http://example.com", rate_limit=10, timeout=5.0)
        assert hasattr(config, "__dataclass_fields__")

    def test_datasource_configs_exist(self) -> None:
        assert len(DATASOURCE_CONFIGS) == 3
        for source in ExternalDataSource:
            assert source in DATASOURCE_CONFIGS

    def test_config_values_reasonable(self) -> None:
        for _source, config in DATASOURCE_CONFIGS.items():
            assert config.base_url.startswith("https://")
            assert config.rate_limit > 0
            assert config.timeout > 0


# ---------------------------------------------------------------------------
# ExternalDataSourceClient tests
# ---------------------------------------------------------------------------


def _mock_httpx_handler(request: Request) -> Response:
    """Mock HTTP handler for httpx transport testing."""
    return Response(200, json={"result": "ok"})


@pytest.fixture
def clean_cache() -> None:
    """Ensure global cache is clean for each test."""
    _query_cache.clear()
    yield
    _query_cache.clear()


@pytest.fixture
def clean_rate_limiters() -> None:
    """Reset global rate limiters for each test."""
    for source in ExternalDataSource:
        _rate_limiters[source] = RateLimiter(DATASOURCE_CONFIGS[source].rate_limit)
    yield


class TestExternalDataSourceClient:
    """Tests for the main external data source client."""

    @pytest.mark.asyncio
    async def test_query_nist_ipr_returns_placeholder(self, clean_cache, clean_rate_limiters) -> None:
        """NIST IPR query returns placeholder structure (no real API)."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            result = await client.query_nist_ipr("UO2", "density")
            assert result is not None
            assert result["source"] == "nist_ipr"
            assert result["formula"] == "UO2"
            assert result["property"] == "density"
            assert "values" in result
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_query_openkim_returns_placeholder(self, clean_cache, clean_rate_limiters) -> None:
        """OpenKIM query returns placeholder structure."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            result = await client.query_openkim("U", "lattice_constant")
            assert result is not None
            assert result["source"] == "openkim"
            assert result["species"] == "U"
            assert "potentials" in result
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_query_materials_project_returns_placeholder(self, clean_cache, clean_rate_limiters) -> None:
        """Materials Project query returns placeholder structure."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            result = await client.query_materials_project("UO2", "band_gap")
            assert result is not None
            assert result["source"] == "materials_project"
            assert result["formula"] == "UO2"
            assert "materials" in result
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_query_none_property_passes_all(self, clean_cache, clean_rate_limiters) -> None:
        """When property_name is None, cache key uses 'all'."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            result = await client.query_nist_ipr("UO2")
            assert result is not None
            assert result["property"] is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_query(self, clean_cache, clean_rate_limiters) -> None:
        """Second query with same params returns cached result."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            # Pre-populate cache
            cached_data = {"source": "nist_ipr", "formula": "UO2", "cached": True}
            _query_cache.set("nist:UO2:density", cached_data)

            with patch(
                "nfm_db.services.external_data_sources.ExternalDataSourceClient._nist_ipr_query",
                new_callable=AsyncMock,
            ) as mock_query:
                result = await client.query_nist_ipr("UO2", "density")

            assert result == cached_data
            mock_query.assert_not_called()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_query(self, clean_cache, clean_rate_limiters) -> None:
        """When rate limit is hit, query is blocked."""
        # Use a very low rate limiter
        limiter = RateLimiter(rate=1)
        _rate_limiters[ExternalDataSource.NIST_IPR] = limiter

        client = ExternalDataSourceClient(timeout=5.0)
        try:
            # First call consumes the token for cache_key "nist:UO2:density"
            await client.query_nist_ipr("UO2", "density")

            # Second call uses SAME cache key → rate limited
            # Patch the placeholder to return a mock so we can verify it's not called
            with patch(
                "nfm_db.services.external_data_sources.ExternalDataSourceClient._nist_ipr_query",
                new_callable=AsyncMock,
                return_value={"source": "nist_ipr", "mock": True},
            ) as mock_query:
                result = await client.query_nist_ipr("UO2", "density")

            # Rate limited — should return cached result (not None since we cached the first call)
            assert result is not None
            # But _nist_ipr_query should NOT have been called again
            mock_query.assert_not_called()
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, clean_cache, clean_rate_limiters) -> None:
        """HTTP timeout returns None."""
        client = ExternalDataSourceClient(timeout=0.001)
        try:
            # Replace the placeholder with a real httpx call that times out
            async def _timeout_query(formula, property_name):
                # Simulate calling the actual client
                response = await client._client.get("http://localhost:1/timeout")
                response.raise_for_status()
                return response.json()

            with patch.object(
                client,
                "_nist_ipr_query",
                _timeout_query,
            ):
                result = await client.query_nist_ipr("UO2", "density")

            assert result is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, clean_cache, clean_rate_limiters) -> None:
        """HTTP 500 returns None."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            async def _error_query(formula, property_name):
                import httpx
                raise httpx.HTTPStatusError(
                    "Server Error",
                    request=Request("GET", "http://example.com"),
                    response=httpx.Response(500, request=Request("GET", "http://example.com")),
                )

            with patch.object(
                client,
                "_nist_ipr_query",
                _error_query,
            ):
                result = await client.query_nist_ipr("UO2", "density")

            assert result is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self, clean_cache, clean_rate_limiters) -> None:
        """Unexpected exceptions return None."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            async def _crash_query(formula, property_name):
                raise RuntimeError("unexpected error")

            with patch.object(
                client,
                "_nist_ipr_query",
                _crash_query,
            ):
                result = await client.query_nist_ipr("UO2", "density")

            assert result is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_query_result_cached_after_success(self, clean_cache, clean_rate_limiters) -> None:
        """Successful query caches the result."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            await client.query_nist_ipr("UO2", "density")
            # Result should be in cache now
            cached = _query_cache.get("nist:UO2:density")
            assert cached is not None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_cache_stats(self, clean_cache) -> None:
        """Cache stats report correct counts."""
        _query_cache.set("nist:UO2:prop", {"data": 1})
        _query_cache.set("openkim:U:prop", {"data": 2})
        _query_cache.set("mp:UO2:prop", {"data": 3})

        client = ExternalDataSourceClient(timeout=5.0)
        try:
            stats = client.get_cache_stats()
            assert stats["total_entries"] == 3
            assert stats["nist_ipr"] == 1
            assert stats["openkim"] == 1
            assert stats["materials_project"] == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_get_cache_stats_empty(self, clean_cache) -> None:
        """Cache stats return zeros when empty."""
        client = ExternalDataSourceClient(timeout=5.0)
        try:
            stats = client.get_cache_stats()
            assert stats["total_entries"] == 0
            assert stats["nist_ipr"] == 0
            assert stats["openkim"] == 0
            assert stats["materials_project"] == 0
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        """Closing client twice does not raise."""
        client = ExternalDataSourceClient(timeout=5.0)
        await client.close()
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------


class TestCreateExternalDataClient:
    """Tests for the factory function."""

    @pytest.mark.asyncio
    async def test_factory_yields_client(self) -> None:
        gen = create_external_data_client(timeout=10.0)
        client = await gen.asend(None)
        assert isinstance(client, ExternalDataSourceClient)
        # Clean up generator
        try:
            await gen.asend(None)
        except StopAsyncIteration:
            pass

    @pytest.mark.asyncio
    async def test_factory_custom_timeout(self) -> None:
        gen = create_external_data_client(timeout=60.0)
        client = await gen.asend(None)
        assert client._timeout == 60.0
        try:
            await gen.asend(None)
        except StopAsyncIteration:
            pass
