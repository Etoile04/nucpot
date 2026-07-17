"""Provider protocol and shared filter model for potential data sources."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from nfm_db.schemas.potential import PotentialDetail, PotentialSummary


class PotentialFilters(BaseModel):
    """Filtering / pagination parameters for potential list queries.

    Mirrors the existing list_potentials parameter set.
    """

    page: int = 1
    limit: int = 20
    type_filter: str | None = None
    elements: list[str] | None = None
    query: str | None = None
    sort: str = "updated"


@runtime_checkable
class PotentialProvider(Protocol):
    """Async provider interface for potential data.

    Both local and OpenKIM providers conform to this protocol so the
    CompositeProvider can merge or route.
    """

    async def list_summaries(self, filters: PotentialFilters) -> list[PotentialSummary]:
        ...

    async def get_detail(self, potential_id: uuid.UUID) -> PotentialDetail | None:
        ...
