"""Local database potential provider.

Wraps the existing SQL query logic behind the PotentialProvider protocol.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Potential
from nfm_db.schemas.potential import PotentialDetail, PotentialSummary
from nfm_db.services.providers.base import PotentialFilters

logger = logging.getLogger(__name__)


class LocalPotentialProvider:
    """Provider that queries the local PostgreSQL/SQLite potential table.

    Query semantics are unchanged from the original service layer
    (FTS / dialect search is WS3, handled separately).
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_summaries(self, filters: PotentialFilters) -> list[PotentialSummary]:
        """Return the FULL filtered list of published potentials (no pagination).

        Pagination + total are handled by the service wrapper so the same
        logic works for the composite multi-provider case (NFM-296 design).
        """

        stmt = select(Potential).where(Potential.status == "published")

        if filters.type_filter:
            stmt = stmt.where(Potential.type == filters.type_filter)

        if filters.query:
            pattern = f"%{filters.query}%"
            stmt = stmt.where(
                or_(
                    Potential.name.ilike(pattern),
                    Potential.display_name.ilike(pattern),
                    Potential.description.ilike(pattern),
                )
            )

        sort_column = {
            "name": Potential.name,
            "type": Potential.type,
            "updated": Potential.updated_at,
        }.get(filters.sort, Potential.updated_at)
        stmt = stmt.order_by(
            sort_column.desc() if filters.sort == "updated" else sort_column.asc()
        )

        rows = (await self.db.execute(stmt)).scalars().all()

        # In-Python element filtering (portable across SQLite/PG).
        if filters.elements:
            wanted = {e.strip() for e in filters.elements if e.strip()}
            rows = [r for r in rows if wanted.intersection(r.elements or [])]

        return [PotentialSummary.model_validate(r) for r in rows]

    async def get_detail(self, potential_id: uuid.UUID) -> PotentialDetail | None:
        """Return a single potential by id, or None if not found / not published."""
        stmt = select(Potential).where(
            Potential.id == potential_id, Potential.status == "published"
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return PotentialDetail.model_validate(row)
