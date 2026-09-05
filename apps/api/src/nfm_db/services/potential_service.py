"""Service layer for potential queries.

Filtering of JSON-stored fields (elements overlap, ``extra`` containment,
temperature ranges) is done in Python for cross-database portability (SQLite
tests). PG-native operators (&&, jsonb ops) + GIN indexes are a Phase 2
optimization.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Potential
from nfm_db.schemas.potential import (
    PotentialDetail,
    PotentialListResponse,
    PotentialSummary,
)

logger = logging.getLogger(__name__)


def _matches_extra(row: Potential, extra_filters: dict[str, Any]) -> bool:
    """JSONB containment semantics (``extra @> filters``): every key must be
    present in ``row.extra`` with an equal value."""
    extra = row.extra or {}
    return all(extra.get(key) == value for key, value in extra_filters.items())


def _temp_bounds(row: Potential) -> tuple[float | None, float | None]:
    """Extract ``(min, max)`` from ``applicability.temperatureRange``.

    Returns ``(None, None)`` when the range is absent or non-numeric —
    PostgREST's ``->>``-based comparison treats those rows as non-matching.
    """
    raw = (row.applicability or {}).get("temperatureRange")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return (None, None)
    try:
        return (float(raw[0]), float(raw[1]))
    except (TypeError, ValueError):
        return (None, None)


def _matches_temperature(
    row: Potential, temp_min: float | None, temp_max: float | None
) -> bool:
    if temp_min is None and temp_max is None:
        return True
    low, high = _temp_bounds(row)
    if low is None or high is None:
        return False
    if temp_min is not None and high < temp_min:
        return False
    return not (temp_max is not None and low > temp_max)


async def list_potentials(
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    type_filter: str | None = None,
    elements: list[str] | None = None,
    query: str | None = None,
    sort: str = "updated",
    extra_filters: dict[str, Any] | None = None,
    temp_min: float | None = None,
    temp_max: float | None = None,
) -> PotentialListResponse:
    """Return a paginated, filtered list of published potentials.

    ``extra_filters`` mirrors the legacy Supabase BFF containment filters
    (irradiation / defect / liquid / validationLevel) and ``temp_min`` /
    ``temp_max`` mirror its ``applicability.temperatureRange`` bounds, so the
    list can be served from the local stack (NFM-4311) without contract loss.
    """

    stmt = select(Potential).where(Potential.status == "published")

    if type_filter:
        # The browse UI sends comma-joined selections ("EAM,MEAM"); treat the
        # parameter as an IN list rather than an exact (never-matching) string.
        types = [t.strip() for t in type_filter.split(",") if t.strip()]
        if types:
            stmt = stmt.where(Potential.type.in_(types))

    if query:
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        stmt = stmt.where(
            or_(
                Potential.name.ilike(pattern, escape="\\"),
                Potential.display_name.ilike(pattern, escape="\\"),
                Potential.description.ilike(pattern, escape="\\"),
            )
        )

    sort_column = {
        "name": Potential.name,
        "type": Potential.type,
        "updated": Potential.updated_at,
    }.get(sort, Potential.updated_at)
    # The BFF stitches multiple backend pages into one response; a unique
    # tiebreaker keeps LIMIT/OFFSET windows stable across those statements.
    stmt = stmt.order_by(
        desc(sort_column) if sort == "updated" else asc(sort_column),
        Potential.id,
    )

    offset = (page - 1) * limit

    # Materialize all matching rows for in-Python element / extra / temperature
    # filtering. We fetch unfiltered to keep the SQL portable (no PG-specific
    # JSONB ops); this is acceptable because the corpus is small (≤hundreds).
    python_filters = bool(
        elements or extra_filters or temp_min is not None or temp_max is not None
    )
    if python_filters:
        wanted = {e.strip() for e in (elements or []) if e.strip()}
        extra_filters = extra_filters or {}
        all_rows = (await db.execute(stmt)).scalars().all()
        matched = [
            r
            for r in all_rows
            if (not wanted or wanted.intersection(r.elements or []))
            and _matches_extra(r, extra_filters)
            and _matches_temperature(r, temp_min, temp_max)
        ]
        total = len(matched)
        rows = matched[offset : offset + limit]
    else:
        # Cross-DB count + paginate (no Python-side filter needed)
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
