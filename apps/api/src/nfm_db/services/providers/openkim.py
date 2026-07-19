# mypy: ignore-errors
"""OpenKIM query API provider.

API docs: https://openkim.org/doc/usage/kim-query/
Endpoints verified 2026-06-19, documented in `OPENKIM_API.md`.

- List models: POST {OPENKIM_API_BASE}/get_available_models
  → JSON array of KIM ID strings.
- Model detail: GET https://openkim.org/id/<KIM_LONG_ID>
  → HTML page; metadata is in ``<meta name="citation_*">`` tags.

All network calls are wrapped so any failure degrades to ``[]`` (list) or
``None`` (detail) — never raises.  OpenKIM is additive and read-only.
"""

from __future__ import annotations

import logging
import os
import uuid

import httpx
from cachetools import TTLCache

from nfm_db.schemas.potential import PotentialDetail, PotentialSummary
from nfm_db.services.openkim_mapper import (
    extract_kim_id,
    map_openkim_summary,
    openkim_potential_id,
)
from nfm_db.services.providers.base import PotentialFilters

logger = logging.getLogger(__name__)

OPENKIM_API_BASE = os.getenv("OPENKIM_API_BASE", "https://query.openkim.org/api")
OPENKIM_DETAIL_BASE = os.getenv("OPENKIM_DETAIL_BASE", "https://openkim.org/id")
OPENKIM_CACHE_TTL_SECONDS = int(os.getenv("OPENKIM_CACHE_TTL_SECONDS", "300"))
OPENKIM_TIMEOUT = float(os.getenv("OPENKIM_TIMEOUT", "5.0"))


class OpenKIMProvider:
    """Read-only OpenKIM potential provider with a short-TTL in-memory cache."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        detail_base: str | None = None,
        ttl: int | None = None,
        timeout: float | None = None,
        cache_maxsize: int = 1000,
        client: httpx.AsyncClient | None = None,
        client_kwargs: dict | None = None,
        cache_ttl: int | None = None,
    ):
        self.base_url = base_url or OPENKIM_API_BASE
        self.detail_base = detail_base or OPENKIM_DETAIL_BASE
        self.timeout = timeout if timeout is not None else OPENKIM_TIMEOUT
        effective_ttl = (
            cache_ttl
            if cache_ttl is not None
            else (ttl if ttl is not None else OPENKIM_CACHE_TTL_SECONDS)
        )
        self._list_cache: TTLCache = TTLCache(maxsize=cache_maxsize, ttl=effective_ttl)
        self._detail_cache: TTLCache = TTLCache(maxsize=cache_maxsize, ttl=effective_ttl)
        # uuid5(id) → kim_id index, populated from list responses.
        self._id_index: dict[uuid.UUID, str] = {}
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(timeout=self.timeout, **(client_kwargs or {}))

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _list_cache_key(filters: PotentialFilters) -> str:
        elements = tuple(sorted(filters.elements)) if filters.elements else ()
        return f"p{filters.page}:l{filters.limit}:t{filters.type_filter}:e{elements}:q{filters.query}:s{filters.sort}"

    async def _fetch_model_ids(self, filters: PotentialFilters) -> list[str]:
        """Call get_available_models with optional species filter. Never raises."""
        try:
            data = {"model_interface": '["mo"]'}
            if filters.elements:
                import json

                data["species"] = json.dumps(filters.elements)
            resp = await self._client.post(
                f"{self.base_url}/get_available_models",
                data=data,
            )
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, list):
                return []
            return [x for x in payload if isinstance(x, str)]
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenKIM list request failed, degrading to empty: %s", exc)
            return []

    # ── protocol ─────────────────────────────────────────────────────

    async def list_summaries(self, filters: PotentialFilters) -> list[PotentialSummary]:
        key = self._list_cache_key(filters)
        if key in self._list_cache:
            return self._list_cache[key]

        kim_ids = await self._fetch_model_ids(filters)
        summaries: list[PotentialSummary] = []
        for kim in kim_ids:
            try:
                summary = map_openkim_summary(kim)
            except ValueError:
                logger.debug("Skipping unmappable OpenKIM entry %r", kim)
                continue
            # Index for detail lookups
            self._id_index[summary.id] = extract_kim_id(kim) or kim
            # Optional element filter applied client-side
            if filters.elements:
                wanted = {e.strip() for e in filters.elements}
                if summary.elements and not wanted.intersection(summary.elements):
                    continue
            summaries.append(summary)

        self._list_cache[key] = summaries
        return summaries

    async def get_detail(self, potential_id: uuid.UUID) -> PotentialDetail | None:
        if potential_id in self._detail_cache:
            return self._detail_cache[potential_id]

        kim_id = self._id_index.get(potential_id)
        if kim_id is None:
            # Not indexed from a prior list — treat as unknown.
            return None

        detail = await self._fetch_detail(kim_id)
        if detail is not None:
            self._detail_cache[potential_id] = detail
        return detail

    async def _fetch_detail(self, kim_id: str) -> PotentialDetail | None:
        """Fetch + map a single model detail. Never raises."""
        from nfm_db.services.openkim_mapper import map_openkim_model

        try:
            resp = await self._client.get(f"{self.detail_base}/{kim_id}")
            resp.raise_for_status()
            model = _parse_model_detail_html(resp.text, kim_id)
            return map_openkim_model(model)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("OpenKIM detail request failed for %s, degrading: %s", kim_id, exc)
            return None


def _parse_model_detail_html(html: str, kim_id: str) -> dict:
    """Extract a model-detail dict from an OpenKIM id/<KIM_ID> HTML page.

    OpenKIM publishes metadata in ``<meta name="citation_*">`` tags; we parse
    those plus the long-name convention for species/type.
    """
    import re

    def _meta(name: str) -> str | None:
        m = re.search(rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"', html)
        return m.group(1) if m else None

    authors = [
        m.group(1) for m in re.finditer(r'<meta\s+name="citation_author"\s+content="([^"]*)"', html)
    ]
    canon = re.search(r'<link rel="canonical" href="https://openkim\.org/id/([A-Za-z0-9_]+)"', html)
    long_name = canon.group(1) if canon else kim_id

    # Species from long-name convention: _<year>_<Elements>__MO_...
    elems: list[str] = []
    em = re.search(r"_(\d{4})_([A-Z][a-z]*(?:[A-Z][a-z]*)*)__MO_", long_name)
    if em:
        elems = re.findall(r"[A-Z][a-z]*", em.group(2))

    pm = re.match(r"([A-Za-z]+)_", long_name)
    potential_type = pm.group(1).lower() if pm else "unknown"

    return {
        "kim_id": kim_id,
        "long_name": long_name,
        "title": _meta("citation_title") or "",
        "authors": authors,
        "publication_date": _meta("citation_publication_date") or "",
        "publisher": _meta("citation_publisher") or "",
        "doi": _meta("citation_doi") or "",
        "description": _meta("description") or "",
        "species": elems,
        "potential_type": potential_type,
    }


# Suppress unused-import lint for openkim_potential_id (re-exported for tests).
__all__ = ["OpenKIMProvider", "openkim_potential_id"]
