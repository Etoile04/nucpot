"""Service layer for potential queries.

Query execution is delegated to providers behind the PotentialProvider
protocol (see `providers/base.py`).  The thin module-level wrappers keep
existing call sites + tests green.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.schemas.potential import (
    PotentialDetail,
    PotentialListResponse,
)
from nfm_db.services.providers.base import PotentialFilters
from nfm_db.services.providers.composite import CompositeProvider
from nfm_db.services.providers.local import LocalPotentialProvider
from nfm_db.services.providers.openkim import OpenKIMProvider

logger = logging.getLogger(__name__)

# Shared filtering helper — kept here so the total/pagination wrapper does not
# depend on any single provider.
def _compute_total_pages(total: int, limit: int) -> int:
    return max(1, -(-total // limit))  # ceil


def build_composite_provider(db: AsyncSession) -> CompositeProvider:
    """Construct the dual-provider composite used by the service wrappers.

    Local is authoritative; OpenKIM is additive + degrade-safe.
    """
    return CompositeProvider(LocalPotentialProvider(db), OpenKIMProvider())


async def list_potentials(
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    type_filter: str | None = None,
    elements: list[str] | None = None,
    query: str | None = None,
    sort: str = "updated",
) -> PotentialListResponse:
    """Return a paginated, filtered list of published potentials."""

    provider = build_composite_provider(db)
    filters = PotentialFilters(
        page=page,
        limit=limit,
        type_filter=type_filter,
        elements=elements,
        query=query,
        sort=sort,
    )
    summaries = await provider.list_summaries(filters)

    # total/pagination: the provider returns the full filtered set;
    # the service wrapper slices for the requested page.
    total = len(summaries)
    offset = (page - 1) * limit
    page_summaries = summaries[offset : offset + limit]
    return PotentialListResponse(
        potentials=page_summaries,
        total=total,
        page=page,
        limit=limit,
        total_pages=_compute_total_pages(total, limit),
    )


async def get_potential_by_id(
    db: AsyncSession, potential_id: uuid.UUID
) -> PotentialDetail | None:
    """Return a single potential by id, or None if not found / not published."""
    return await build_composite_provider(db).get_detail(potential_id)
