"""Composite potential provider (NFM-296 Task 7).

Merges (list) and routes (detail) across a local provider and an
OpenKIM provider, degrading gracefully if OpenKIM is unreachable.

Provider seams are:

- ``list_summaries``: ``local_rows + openkim_rows`` — OpenKIM appended.
- ``get_detail``: local first, fall through to OpenKIM if not found.
"""

from __future__ import annotations

import logging
import uuid

from nfm_db.schemas.potential import PotentialDetail, PotentialSummary
from nfm_db.services.providers.base import PotentialFilters, PotentialProvider

logger = logging.getLogger(__name__)


class CompositeProvider:
    """Merge + route a local provider and an OpenKIM provider."""

    def __init__(self, local: PotentialProvider, openkim: PotentialProvider):
        self.local = local
        self.openkim = openkim

    async def list_summaries(self, filters: PotentialFilters) -> list[PotentialSummary]:
        # Local first (the authoritative store); OpenKIM appended.
        local_rows = await self.local.list_summaries(filters)
        try:
            openkim_rows = await self.openkim.list_summaries(filters)
        except Exception:  # noqa: BLE001 — defensive, never fail the list
            logger.warning("OpenKIM list_summaries raised; serving local-only")
            openkim_rows = []
        return [*local_rows, *openkim_rows]

    async def get_detail(self, potential_id: uuid.UUID) -> PotentialDetail | None:
        detail = await self.local.get_detail(potential_id)
        if detail is not None:
            return detail
        try:
            return await self.openkim.get_detail(potential_id)
        except Exception:  # noqa: BLE001 — defensive, never fail the lookup
            logger.warning("OpenKIM get_detail raised; returning None")
            return None
