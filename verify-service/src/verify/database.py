"""Supabase REST client for verification pipeline.

Uses httpx directly instead of supabase-py for lighter dependencies.
The service_role key bypasses RLS for write operations.
"""

import logging
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Lightweight Supabase REST client using PostgREST conventions."""

    def __init__(
        self,
        url: str | None = None,
        service_key: str | None = None,
    ):
        self.url = (url or settings.SUPABASE_URL).rstrip("/")
        self.service_key = service_key or settings.SUPABASE_SERVICE_KEY
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=f"{self.url}/rest/v1",
                headers={
                    "apikey": self.service_key,
                    "Authorization": f"Bearer {self.service_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_potential(self, potential_id: str) -> dict[str, Any] | None:
        """Fetch a potential by ID."""
        client = await self._get_client()
        resp = await client.get(
            "/potentials",
            params={"id": f"eq.{potential_id}", "select": "*"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    async def get_reference_values(
        self,
        element: str,
        crystal_structure: str,
    ) -> list[dict[str, Any]]:
        """Fetch reference values for an element/structure pair."""
        client = await self._get_client()
        resp = await client.get(
            "/reference_values",
            params={
                "element": f"eq.{element}",
                "crystal_structure": f"eq.{crystal_structure}",
                "order": "property_name",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_reference_value(
        self,
        element: str,
        crystal_structure: str,
        property_name: str,
    ) -> dict[str, Any] | None:
        """Fetch a single reference value."""
        rows = await self.get_reference_values(element, crystal_structure)
        for row in rows:
            if row["property_name"] == property_name:
                return row
        return None

    async def save_verification(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert a verification record."""
        client = await self._get_client()
        resp = await client.post("/verifications", json=data)
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else {}

    async def update_verification(
        self,
        verification_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a verification record."""
        client = await self._get_client()
        resp = await client.patch(
            "/verifications",
            json=data,
            params={"id": f"eq.{verification_id}"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else {}

    async def get_verification(self, verification_id: str) -> dict[str, Any] | None:
        """Fetch a verification record by ID."""
        client = await self._get_client()
        resp = await client.get(
            "/verifications",
            params={"id": f"eq.{verification_id}", "select": "*"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    async def update_potential(
        self,
        potential_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a potential record (e.g. write back verified_props)."""
        client = await self._get_client()
        resp = await client.patch(
            "/potentials",
            json=data,
            params={"id": f"eq.{potential_id}"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else {}


# Singleton instance
db = SupabaseClient()
