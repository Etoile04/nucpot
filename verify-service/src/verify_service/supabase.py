"""Supabase client for reading/writing verification data."""

from __future__ import annotations

import os
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Lightweight Supabase REST API client."""

    def __init__(
        self,
        url: str | None = None,
        service_key: str | None = None,
    ):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = service_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.url}/rest/v1",
                headers=self._headers(),
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---- Generic CRUD ----

    async def select(
        self, table: str, query: dict[str, Any] | None = None
    ) -> list[dict]:
        params = {}
        if query:
            for k, v in query.items():
                if isinstance(v, dict):
                    # e.g. {"eq": "value"} → key=eq.value
                    op, val = next(iter(v.items()))
                    params[k] = f"{op}.{val}"
                else:
                    params[k] = f"eq.{v}"
        resp = await self.client.get(f"/{table}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def insert(self, table: str, data: dict | list[dict]) -> dict | list[dict]:
        resp = await self.client.post(f"/{table}", json=data)
        resp.raise_for_status()
        return resp.json()

    async def update(
        self, table: str, filters: dict[str, Any], data: dict
    ) -> list[dict]:
        params = {}
        for k, v in filters.items():
            params[k] = f"eq.{v}"
        resp = await self.client.patch(f"/{table}", params=params, json=data)
        resp.raise_for_status()
        return resp.json()

    # ---- Domain helpers ----

    async def get_potential(self, potential_id: str) -> dict | None:
        rows = await self.select("potentials", {"id": potential_id})
        return rows[0] if rows else None

    async def get_reference_values(
        self, material: str | None = None, structure: str | None = None
    ) -> list[dict]:
        query = {}
        if material:
            query["material"] = material
        if structure:
            query["structure"] = structure
        return await self.select("reference_values", query or None)

    async def create_verification(self, data: dict) -> dict:
        return await self.insert("verifications", data)

    async def update_verification(self, verification_id: str, data: dict) -> dict:
        rows = await self.update("verifications", {"id": verification_id}, data)
        return rows[0] if rows else {}

    async def get_verification(self, verification_id: str) -> dict | None:
        rows = await self.select("verifications", {"id": verification_id})
        return rows[0] if rows else None

    async def get_verifications_for_potential(self, potential_id: str) -> list[dict]:
        return await self.select("verifications", {"potential_id": potential_id})


# Singleton
_client: SupabaseClient | None = None


def get_supabase_client() -> SupabaseClient:
    global _client
    if _client is None:
        _client = SupabaseClient()
    return _client
