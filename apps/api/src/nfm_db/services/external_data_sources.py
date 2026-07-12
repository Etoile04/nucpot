"""External Data Source Client (NFM-87.3).

Provides integration with external nuclear materials databases:
- NIST IPR (Thermodynamics Research Center)
- OpenKIM (Open Knowledgebase of Interatomic Models)
- Materials Project

Features:
- Query interface for each source
- In-memory caching with TTL
- Rate limiting per source
- Fallback strategies
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data source enumeration
# ---------------------------------------------------------------------------


class ExternalDataSource(str, Enum):
    """External nuclear materials data sources."""

    NIST_IPR = "nist_ipr"
    OPENKIM = "openkim"
    MATERIALS_PROJECT = "materials_project"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataSourceConfig:
    """Configuration for an external data source."""

    base_url: str
    rate_limit: int  # requests per minute
    timeout: float  # seconds


DATASOURCE_CONFIGS: dict[ExternalDataSource, DataSourceConfig] = {
    ExternalDataSource.NIST_IPR: DataSourceConfig(
        base_url="https://trc.nist.gov/cif",
        rate_limit=60,
        timeout=30.0,
    ),
    ExternalDataSource.OPENKIM: DataSourceConfig(
        base_url="https://openkim.org",
        rate_limit=120,
        timeout=30.0,
    ),
    ExternalDataSource.MATERIALS_PROJECT: DataSourceConfig(
        base_url="https://materialsproject.org",
        rate_limit=60,
        timeout=30.0,
    ),
}


# ---------------------------------------------------------------------------
# Cache implementation
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """Cache entry with TTL."""

    data: dict[str, Any]
    cached_at: datetime
    ttl_seconds: int = 3600  # 1 hour default


class SimpleCache:
    """Simple in-memory cache with TTL.

    NOTE: Replace with Redis in production for distributed caching.
    """

    def __init__(self) -> None:
        """Initialize empty cache."""
        self._cache: dict[str, CacheEntry] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        """Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached data or None if not found/expired
        """
        entry = self._cache.get(key)
        if entry is None:
            return None

        now = datetime.now(UTC)
        if (now - entry.cached_at).total_seconds() > entry.ttl_seconds:
            # Expired
            del self._cache[key]
            return None

        return entry.data

    def set(
        self,
        key: str,
        data: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> None:
        """Set value in cache with TTL.

        Args:
            key: Cache key
            data: Data to cache
            ttl_seconds: Time to live in seconds
        """
        self._cache[key] = CacheEntry(
            data=data,
            cached_at=datetime.now(UTC),
            ttl_seconds=ttl_seconds,
        )

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def size(self) -> int:
        """Return number of cached entries."""
        return len(self._cache)


# Global cache instance
_query_cache = SimpleCache()


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token bucket rate limiter.

    Ensures we don't exceed API rate limits for external sources.
    """

    def __init__(self, rate: int, window_seconds: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            rate: Maximum requests per window
            window_seconds: Time window in seconds (default 60)
        """
        self.rate = rate
        self.window_seconds = window_seconds
        self._tokens: dict[str, list[datetime]] = {}

    async def acquire(self, key: str) -> bool:
        """Try to acquire a token for the given key.

        Args:
            key: Identifier to rate limit (e.g., query fingerprint)

        Returns:
            True if token acquired, False if rate limited
        """
        now = datetime.now(UTC)
        window_start = now.replace(second=0, microsecond=0)

        # Clean old tokens
        if key in self._tokens:
            self._tokens[key] = [ts for ts in self._tokens[key] if ts >= window_start]
        else:
            self._tokens[key] = []

        # Check if rate limit reached
        if len(self._tokens[key]) >= self.rate:
            return False

        # Add token
        self._tokens[key].append(now)
        return True


# Rate limiters per data source
_rate_limiters: dict[ExternalDataSource, RateLimiter] = {
    source: RateLimiter(config.rate_limit) for source, config in DATASOURCE_CONFIGS.items()
}


# ---------------------------------------------------------------------------
# External data source client
# ---------------------------------------------------------------------------


class ExternalDataSourceClient:
    """Client for querying external nuclear materials data sources.

    Provides:
    - Query methods for each source
    - Automatic caching
    - Rate limiting
    - Timeout handling
    """

    def __init__(self, timeout: float = 30.0) -> None:
        """Initialize HTTP client.

        Args:
            timeout: Request timeout in seconds
        """
        self._client = httpx.AsyncClient(timeout=timeout)
        self._timeout = timeout

    async def query_nist_ipr(
        self,
        formula: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Query NIST IPR (Thermodynamics Research Center) database.

        Args:
            formula: Chemical formula (e.g., "UO2")
            property_name: Optional property filter

        Returns:
            Dictionary with query results or None if not found/error
        """
        return await self._query_with_cache(
            source=ExternalDataSource.NIST_IPR,
            cache_key=f"nist:{formula}:{property_name or 'all'}",
            query_fn=lambda: self._nist_ipr_query(formula, property_name),
        )

    async def query_openkim(
        self,
        species: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Query OpenKIM database for interatomic potentials.

        Args:
            species: Chemical species (e.g., "U", "O", "Zr")
            property_name: Optional property filter

        Returns:
            Dictionary with query results or None if not found/error
        """
        return await self._query_with_cache(
            source=ExternalDataSource.OPENKIM,
            cache_key=f"openkim:{species}:{property_name or 'all'}",
            query_fn=lambda: self._openkim_query(species, property_name),
        )

    async def query_materials_project(
        self,
        formula: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Query Materials Project database.

        Args:
            formula: Chemical formula (e.g., "UO2")
            property_name: Optional property filter

        Returns:
            Dictionary with query results or None if not found/error
        """
        return await self._query_with_cache(
            source=ExternalDataSource.MATERIALS_PROJECT,
            cache_key=f"mp:{formula}:{property_name or 'all'}",
            query_fn=lambda: self._materials_project_query(formula, property_name),
        )

    async def _query_with_cache(
        self,
        source: ExternalDataSource,
        cache_key: str,
        query_fn,
    ) -> dict[str, Any] | None:
        """Query with cache and rate limiting.

        Args:
            source: Data source being queried
            cache_key: Cache key for results
            query_fn: Async function that performs the actual query

        Returns:
            Query results or None
        """
        # Check cache first
        cached = _query_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for {cache_key}")
            return cached

        # Rate limit check
        rate_limiter = _rate_limiters[source]
        if not await rate_limiter.acquire(cache_key):
            logger.warning(f"Rate limit exceeded for {source.value}")
            # Return cached stale data if available, or None
            return cached

        # Perform query
        try:
            result = await query_fn()
            if result is not None:
                _query_cache.set(cache_key, result)
            return result
        except httpx.TimeoutException:
            logger.error(f"Query timeout for {source.value}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error for {source.value}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Query failed for {source.value}: {e}")
            return None

    async def _nist_ipr_query(
        self,
        formula: str,
        property_name: str | None,
    ) -> dict[str, Any] | None:
        """Perform NIST IPR query.

        NOTE: This is a placeholder implementation.
        In production, this will call the actual NIST IPR API.

        Args:
            formula: Chemical formula
            property_name: Property name

        Returns:
            Query results or None
        """
        _ = DATASOURCE_CONFIGS[ExternalDataSource.NIST_IPR]

        # TODO: Implement actual API call
        # Example implementation:
        # response = await self._client.get(
        #     f"{config.base_url}/search",
        #     params={"formula": formula, "property": property_name},
        # )
        # response.raise_for_status()
        # return response.json()

        # Placeholder response structure
        logger.info(f"NIST IPR query for {formula} - placeholder implementation")
        return {
            "source": "nist_ipr",
            "formula": formula,
            "property": property_name,
            "values": [],
            "uncertainties": [],
            "note": "Placeholder - API integration pending",
        }

    async def _openkim_query(
        self,
        species: str,
        property_name: str | None,
    ) -> dict[str, Any] | None:
        """Perform OpenKIM query.

        NOTE: This is a placeholder implementation.
        In production, this will call the actual OpenKIM API.

        Args:
            species: Chemical species
            property_name: Property name

        Returns:
            Query results or None
        """
        _ = DATASOURCE_CONFIGS[ExternalDataSource.OPENKIM]

        # TODO: Implement actual API call
        # Example implementation:
        # response = await self._client.get(
        #     f"{config.base_url}/query",
        #     params={"species": species, "property": property_name},
        # )
        # response.raise_for_status()
        # return response.json()

        # Placeholder response structure
        logger.info(f"OpenKIM query for {species} - placeholder implementation")
        return {
            "source": "openkim",
            "species": species,
            "property": property_name,
            "potentials": [],
            "note": "Placeholder - API integration pending",
        }

    async def _materials_project_query(
        self,
        formula: str,
        property_name: str | None,
    ) -> dict[str, Any] | None:
        """Perform Materials Project query.

        NOTE: This is a placeholder implementation.
        In production, this will call the actual Materials Project API
        and will require an API key.

        Args:
            formula: Chemical formula
            property_name: Property name

        Returns:
            Query results or None
        """
        _ = DATASOURCE_CONFIGS[ExternalDataSource.MATERIALS_PROJECT]

        # TODO: Implement actual API call
        # Example implementation:
        # response = await self._client.get(
        #     f"{config.base_url}/materials",
        #     params={
        #         "formula": formula,
        #         "property": property_name,
        #         "API_KEY": os.environ["MATERIALS_PROJECT_API_KEY"],
        #     },
        # )
        # response.raise_for_status()
        # return response.json()

        # Placeholder response structure
        logger.info(f"Materials Project query for {formula} - placeholder implementation")
        return {
            "source": "materials_project",
            "formula": formula,
            "property": property_name,
            "materials": [],
            "note": "Placeholder - API integration pending",
        }

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()

    def get_cache_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dictionary with cache size and source-specific counts
        """
        return {
            "total_entries": _query_cache.size(),
            "nist_ipr": sum(1 for k in _query_cache._cache if k.startswith("nist:")),
            "openkim": sum(1 for k in _query_cache._cache if k.startswith("openkim:")),
            "materials_project": sum(1 for k in _query_cache._cache if k.startswith("mp:")),
        }


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


async def create_external_data_client(
    timeout: float = 30.0,
) -> AsyncGenerator[ExternalDataSourceClient, None]:
    """Factory function for external data source client.

    Args:
        timeout: Request timeout in seconds

    Yields:
        ExternalDataSourceClient instance
    """
    client = ExternalDataSourceClient(timeout=timeout)
    try:
        yield client
    finally:
        await client.close()
