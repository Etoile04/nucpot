"""Service layer for potential queries.

Filtering of JSON-stored fields (elements overlap) is done in Python for
cross-database portability (SQLite tests). PG-native operators (&&, jsonb ops)
+ GIN indexes are a Phase 2 optimization.
"""

import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Potential
from nfm_db.schemas.potential import (
    PotentialDetail,
    PotentialListResponse,
    PotentialSummary,
)

logger = logging.getLogger(__name__)


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

    stmt = select(Potential).where(Potential.status == "published")

    if type_filter:
        stmt = stmt.where(Potential.type == type_filter)

    if query:
        pattern = f"%{query}%"
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
    }.get(sort, Potential.updated_at)
    stmt = stmt.order_by(sort_column.desc() if sort == "updated" else sort_column.asc())

    offset = (page - 1) * limit

    # Materialize all matching rows for in-Python element filtering.
    # We fetch unfiltered to keep the SQL portable (no PG-specific JSONB ops);
    # this is acceptable because the corpus is small (≤hundreds).
    if elements:
        wanted = {e.strip() for e in elements if e.strip()}
        all_rows = (await db.execute(stmt)).scalars().all()
        matched = [r for r in all_rows if wanted.intersection(r.elements or [])]
        total = len(matched)
        rows = matched[offset : offset + limit]
    else:
        # Cross-DB count + paginate (no Python-side element filter needed)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar_one()
        stmt = stmt.offset(offset).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()

    summaries = [PotentialSummary.model_validate(r) for r in rows]
    return PotentialListResponse(
        potentials=summaries,
        total=total,
        page=page,
        limit=limit,
        total_pages=max(1, -(-total // limit)),  # ceil
    )


async def get_potential_by_id(db: AsyncSession, potential_id: uuid.UUID) -> PotentialDetail | None:
    """Return a single potential by id, or None if not found / not published."""
    stmt = select(Potential).where(Potential.id == potential_id, Potential.status == "published")
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return PotentialDetail.model_validate(row)


async def update_potential_verification(
    db: AsyncSession,
    potential_id: uuid.UUID,
    status: str,
    *,
    message: str | None = None,
    evidence_url: str | None = None,
) -> Potential | None:
    """Update a potential's verification status (autovc PATCH seam helper).

    Sets ``verification_status`` and, when provided, folds the ``message`` /
    ``evidence_url`` audit fields into the existing ``extra`` JSON blob without
    clobbering unrelated keys. Returns the refreshed row, or ``None`` if the
    potential does not exist.
    """
    stmt = select(Potential).where(Potential.id == potential_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.verification_status = status
    if message or evidence_url:
        row.extra = {
            **(row.extra or {}),
            "verification_message": message,
            "verification_evidence_url": evidence_url,
        }
    await db.commit()
    await db.refresh(row)
    return row
