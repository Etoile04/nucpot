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

import json
import logging
import os
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
# BUG-24 / NFM-3875 — real-API endpoints
# ---------------------------------------------------------------------------


# Materials Project — see https://docs.materialsproject.org/downloading-data/
MATERIALS_PROJECT_API_KEY_ENV = "MATERIALS_PROJECT_API_KEY"
MATERIALS_PROJECT_API_BASE = os.getenv(
    "MATERIALS_PROJECT_API_BASE",
    "https://api.materialsproject.org",
)
MATERIALS_PROJECT_DEFAULT_FIELDS = (
    "material_id,formula,elements,energy_per_atom,formation_energy_per_atom,"
    "band_gap,density,material_type,crystal_system,spacegroup"
)
MATERIALS_PROJECT_DEFAULT_LIMIT = 20

# OpenKIM — see https://openkim.org/doc/usage/kim-query/ and OPENKIM_API.md
OPENKIM_API_BASE = os.getenv("OPENKIM_API_BASE", "https://query.openkim.org/api")
OPENKIM_DEFAULT_LIMIT = 50


def _map_openkim_kim_id(kim_id: str) -> dict[str, Any]:
    """Map a single KIM ID (long name) → dict for the dispatcher's ``potentials``.

    The dispatcher only needs a list-shaped payload with enough metadata for
    downstream consumers to look up the model; deeper parsing happens in
    ``providers/openkim.py`` on the detail path.
    """
    return {
        "kim_id": kim_id,
        "source": "openkim",
        "raw": kim_id,
    }


def _map_mp_record(record: dict[str, Any]) -> dict[str, Any]:
    """Map a Materials Project summary record → dict for ``materials``."""
    return {
        "material_id": record.get("material_id"),
        "formula": record.get("formula"),
        "elements": record.get("elements", []),
        "material_type": record.get("material_type"),
        "crystal_system": record.get("crystal_system"),
        "spacegroup": record.get("spacegroup"),
        "band_gap": record.get("band_gap"),
        "energy_per_atom": record.get("energy_per_atom"),
        "formation_energy_per_atom": record.get("formation_energy_per_atom"),
        "density": record.get("density"),
        "source": "materials_project",
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

    def __init__(
        self,
        timeout: float = 30.0,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize HTTP client.

        Args:
            timeout: Request timeout in seconds.
            api_key: Optional Materials Project API key override. When ``None``
                the key is read from the ``MATERIALS_PROJECT_API_KEY`` env
                var at query time (BUG-24 / NFM-3875).
            client: Optional pre-built ``httpx.AsyncClient`` for testability.
                When ``None``, the client constructs its own. Injecting a
                client with a ``httpx.MockTransport`` lets unit tests run
                without real network calls.
        """
        self._timeout = timeout
        self._client = client if client is not None else httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        # Resolve MP key lazily — env may change between construction and query.
        self._api_key = api_key

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
        """Perform real OpenKIM query.

        Posts to ``https://query.openkim.org/api/get_available_models``
        (anonymous; no API key required). Returns a dispatcher-shaped dict
        with ``potentials`` populated from the live response. Any failure
        degrades to an empty ``potentials`` list — never raises.

        Args:
            species: Chemical species filter (e.g. "U", "O", "Zr").
            property_name: Optional property filter (currently unused;
                OpenKIM does not filter by property on this endpoint).

        Returns:
            Dispatcher-shaped result dict, or ``None`` if the request could
            not be attempted at all (e.g. base URL misconfigured).
        """
        _ = DATASOURCE_CONFIGS[ExternalDataSource.OPENKIM]

        # OpenKIM requires species to be a JSON-encoded list per the API
        # spec (see providers/OPENKIM_API.md). ``species_logic`` defaults to
        # "and" upstream; we omit it to use the server default.
        try:
            form_data = {
                "model_interface": json.dumps(["mo"]),
                "species": json.dumps([species]),
            }
        except (TypeError, ValueError) as exc:
            logger.error("OpenKIM species %r could not be encoded: %s", species, exc)
            return self._empty_openkim_result(species, property_name)

        try:
            response = await self._client.post(
                f"{OPENKIM_API_BASE}/get_available_models",
                data=form_data,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenKIM HTTP %d for species=%s: %s",
                exc.response.status_code,
                species,
                exc,
            )
            return self._empty_openkim_result(species, property_name)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenKIM request failed for species=%s: %s", species, exc)
            return self._empty_openkim_result(species, property_name)

        if not isinstance(payload, list):
            # OpenKIM returns ``{"error": "..."}`` on bad input; treat as empty
            # so callers never have to special-case the error shape.
            logger.info(
                "OpenKIM returned non-list payload for species=%s: %r",
                species,
                payload if isinstance(payload, (dict, str)) else type(payload).__name__,
            )
            return self._empty_openkim_result(species, property_name)

        potentials = [_map_openkim_kim_id(kim) for kim in payload if isinstance(kim, str) and kim]

        result: dict[str, Any] = {
            "source": "openkim",
            "species": species,
            "property": property_name,
            "potentials": potentials,
        }
        if not potentials:
            result["note"] = "no matching OpenKIM models"
        return result

    def _empty_openkim_result(self, species: str, property_name: str | None) -> dict[str, Any]:
        """Build the structured-empty OpenKIM result used for graceful degradation."""
        return {
            "source": "openkim",
            "species": species,
            "property": property_name,
            "potentials": [],
            "note": "OpenKIM query did not return results",
        }

    async def _materials_project_query(
        self,
        formula: str,
        property_name: str | None,
    ) -> dict[str, Any] | None:
        """Perform real Materials Project query.

        GETs ``https://api.materialsproject.org/materials/summary/`` with
        ``X-API-KEY`` from the ``MATERIALS_PROJECT_API_KEY`` env var (or the
        override passed to ``__init__``). 401/403 trigger a clear log hint
        to regenerate the key at materialsproject.org.

        Args:
            formula: Chemical formula (e.g. "UO2").
            property_name: Optional property filter — used as a hint to
                narrow the requested ``_fields`` set when provided.

        Returns:
            Dispatcher-shaped result dict, or ``None`` when the API key is
            missing or the request was rejected as unauthorized.
        """
        _ = DATASOURCE_CONFIGS[ExternalDataSource.MATERIALS_PROJECT]

        api_key = self._api_key or os.getenv(MATERIALS_PROJECT_API_KEY_ENV)
        if not api_key:
            logger.error(
                "Materials Project query skipped: %s env var is not set.",
                MATERIALS_PROJECT_API_KEY_ENV,
            )
            return None

        fields = MATERIALS_PROJECT_DEFAULT_FIELDS
        if property_name:
            # When the caller asks for a specific property, ask MP only for
            # the columns that include it (and the always-needed identifiers)
            # to keep payload size sane.
            field_list = [f.strip() for f in fields.split(",") if f.strip()]
            if property_name in field_list:
                narrowed = ["material_id", "formula", "elements", property_name]
                fields = ",".join(narrowed)

        params: dict[str, str] = {
            "formula": formula,
            "_limit": str(MATERIALS_PROJECT_DEFAULT_LIMIT),
            "_fields": fields,
        }

        headers = {"X-API-KEY": api_key}

        try:
            response = await self._client.get(
                f"{MATERIALS_PROJECT_API_BASE}/materials/summary/",
                params=params,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("Materials Project request failed for %s: %s", formula, exc)
            return self._empty_mp_result(formula, property_name)

        if response.status_code in (401, 403):
            logger.error(
                "Materials Project rejected API key (HTTP %d). "
                "Regenerate the key at https://materialsproject.org/api "
                "(free) and update %s in ~/Projects/nucpot/.env.",
                response.status_code,
                MATERIALS_PROJECT_API_KEY_ENV,
            )
            return None

        try:
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Materials Project HTTP %d for %s: %s",
                exc.response.status_code,
                formula,
                exc,
            )
            return self._empty_mp_result(formula, property_name)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Materials Project parse failed for %s: %s", formula, exc)
            return self._empty_mp_result(formula, property_name)

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            logger.info(
                "Materials Project returned no 'data' array for %s: %r",
                formula,
                payload if isinstance(payload, (dict, str)) else type(payload).__name__,
            )
            return self._empty_mp_result(formula, property_name)

        materials = [_map_mp_record(record) for record in data if isinstance(record, dict)]

        result: dict[str, Any] = {
            "source": "materials_project",
            "formula": formula,
            "property": property_name,
            "materials": materials,
        }
        if isinstance(payload, dict) and "meta" in payload:
            result["meta"] = payload["meta"]
        if not materials:
            result["note"] = "no matching Materials Project records"
        return result

    def _empty_mp_result(self, formula: str, property_name: str | None) -> dict[str, Any]:
        """Build the structured-empty MP result used for graceful degradation."""
        return {
            "source": "materials_project",
            "formula": formula,
            "property": property_name,
            "materials": [],
            "note": "Materials Project query did not return results",
        }

    async def close(self) -> None:
        """Close HTTP client.

        Only closes the underlying client when this ``ExternalDataSourceClient``
        constructed it. When the client was injected via the ``client=``
        parameter (e.g. by tests), the caller owns the underlying
        ``httpx.AsyncClient`` and is responsible for closing it.
        """
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def aclose(self) -> None:
        """Async alias for :meth:`close`, matching ``providers/openkim.py``."""
        await self.close()

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
