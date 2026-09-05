"""Service layer for material CRUD and search queries."""

import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.models import Material, MaterialAlias, MaterialCategory
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.material import (
    MaterialAliasResponse,
    MaterialCategoryListResponse,
    MaterialCategoryResponse,
    MaterialCompositionResponse,
    MaterialCreate,
    MaterialDetailResponse,
    MaterialResponse,
    MaterialUpdate,
    UncategorizedMaterialCountResponse,
)

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "name": Material.name,
    "created_at": Material.created_at,
    "updated_at": Material.updated_at,
}


# NFM-4057 — Phase 4 strategy B canonical placeholder. The NFM-3918 / NFM-4052
# cleanup consolidated N unknown-attributed rows into a single canonical row
# that carries 96 measurements + 13 datasets. It cannot be deleted, but its
# bad-word name leaks onto the public `/materials` list + search.
#
# Per CPO Option-2 decision (NFM-4055): hide it from list/search by exact
# name filter, preserve direct UUID access and the per-material property
# data path for curation. Filtering by *exact* name (not a prefix like
# `Unknown%`) does not blind NFM-3919-style leakage gates — a producer that
# re-creates a bare `Unknown Material` row still surfaces through the gate.
PLACEHOLDER_CANONICAL_NAME = "Unknown Material (canonical)"


async def list_materials(
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    sort: str = "created_at",
    order: str = "desc",
    category_id: uuid.UUID | None = None,
) -> PaginatedResponse[MaterialResponse]:
    """Return a paginated list of materials, optionally filtered by category."""
    stmt = select(Material)

    # NFM-4057 — hide the Phase-4 strategy-B canonical placeholder from
    # public list results. Direct UUID access and the data path remain
    # open (see ``get_material`` and the per-material properties endpoint).
    stmt = stmt.where(Material.name != PLACEHOLDER_CANONICAL_NAME)

    # NFM-4312 — retired materials (duplicate fragments merged into a
    # canonical row by the backfill) stay queryable by UUID but leave
    # the public list. All prod rows are is_active=True today, so this
    # only affects future merges.
    stmt = stmt.where(Material.is_active.is_(True))

    if category_id is not None:
        stmt = stmt.where(Material.category_id == category_id)

    sort_column = _SORT_COLUMNS.get(sort, Material.created_at)
    stmt = stmt.order_by(sort_column.desc() if order == "desc" else sort_column.asc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    items = [MaterialResponse.model_validate(r) for r in rows]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=max(1, -(-total // limit)),  # ceil
    )


async def get_material(db: AsyncSession, material_id: uuid.UUID) -> MaterialDetailResponse | None:
    """Return a material with aliases and composition eager-loaded, or None."""
    stmt = (
        select(Material)
        .options(
            selectinload(Material.aliases),
            selectinload(Material.composition),
        )
        .where(Material.id == material_id)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    aliases = [MaterialAliasResponse.model_validate(a) for a in row.aliases]
    composition = [MaterialCompositionResponse.model_validate(c) for c in row.composition]
    base = MaterialResponse.model_validate(row)
    return MaterialDetailResponse(
        **base.model_dump(),
        aliases=aliases,
        composition=composition,
    )


async def create_material(db: AsyncSession, data: MaterialCreate) -> MaterialResponse:
    """Create a new material and return it."""
    mat = Material(**data.model_dump())
    db.add(mat)
    await db.commit()
    await db.refresh(mat)
    return MaterialResponse.model_validate(mat)


async def update_material(
    db: AsyncSession, material_id: uuid.UUID, data: MaterialUpdate
) -> MaterialResponse | None:
    """Update an existing material. Returns None if not found."""
    stmt = select(Material).where(Material.id == material_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)

    db.add(row)
    await db.commit()
    await db.refresh(row)
    return MaterialResponse.model_validate(row)


async def search_materials(
    db: AsyncSession,
    *,
    query: str = "",
    page: int = 1,
    limit: int = 20,
    category_id: uuid.UUID | None = None,
) -> PaginatedResponse[MaterialResponse]:
    """Search materials by name, formula, or alias (ILIKE).

    An empty query returns all materials (paginated). When
    ``category_id`` is provided the result set is restricted to that
    category — composing with the ``query`` parameter is intentional
    (NFM-3917 / Tier 1D CPO decision: do not make search and category
    filter mutually exclusive).
    """
    stmt = select(Material)

    # NFM-4057 — same canonical-placeholder filter as ``list_materials``.
    # Search-by-name with ``q=unknown`` would otherwise surface the
    # canonical row, defeating the CPO Option-2 decision.
    stmt = stmt.where(Material.name != PLACEHOLDER_CANONICAL_NAME)

    # NFM-4312 — retired fragments leave search results too, mirroring
    # ``list_materials`` so a retired duplicate cannot re-enter through
    # the name/formula/alias ILIKE path.
    stmt = stmt.where(Material.is_active.is_(True))

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                Material.name.ilike(pattern),
                Material.formula.ilike(pattern),
                Material.aliases.any(MaterialAlias.alias_name.ilike(pattern)),
            )
        )

    if category_id is not None:
        stmt = stmt.where(Material.category_id == category_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    items = [MaterialResponse.model_validate(r) for r in rows]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=max(1, -(-total // limit)),
    )


async def list_material_categories(db: AsyncSession) -> MaterialCategoryListResponse:
    """Return every material category ordered for the filter dropdown.

    NFM-3917 / Tier 1D: feeds the ``/materials`` page category ``Select``.
    Sort order is ``(sort_order ASC, name ASC)`` so the seeded taxonomy
    (which sets ``sort_order`` to a stable integer in
    ``065_seed_material_categories``) renders in the order data
    curators chose. ``name`` is the tiebreaker so newly inserted rows
    without an explicit ``sort_order`` still appear deterministically.
    """
    stmt = select(MaterialCategory).order_by(
        MaterialCategory.sort_order.asc(),
        MaterialCategory.name.asc(),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return MaterialCategoryListResponse(
        items=[MaterialCategoryResponse.model_validate(r) for r in rows],
    )


async def count_uncategorized_materials(
    db: AsyncSession,
) -> UncategorizedMaterialCountResponse:
    """Count materials whose ``category_id IS NULL`` (NFM-4030).

    These rows are invisible under any category filter on the
    ``/materials`` page (NFM-3917 Tier 1D silent-gap follow-up). The
    frontend surfaces a notice when this count is positive so users are
    not surprised by 47 (or however many) "missing" materials.

    Single-row ``COUNT(*)`` aggregation — cheap enough to fetch on every
    page mount; no caching needed at current data volume (~hundreds of
    materials). If traffic warrants it later, a 60-second in-process
    cache keyed on (db bind) is the obvious next step.
    """
    stmt = (
        select(func.count())
        .select_from(Material)
        .where(
            Material.category_id.is_(None),
            Material.is_active.is_(True),
        )
    )
    total = (await db.execute(stmt)).scalar_one()
    return UncategorizedMaterialCountResponse(count=int(total))
