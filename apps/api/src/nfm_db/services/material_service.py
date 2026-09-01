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

# NFM-4057 / NFM-4052 Phase-4 merge target: this row carries the 96
# property_measurements + 13 datasets that were reattributed to it during
# the Unknown-Material cleanup. It must remain accessible via
# get_material() and via dataset/property_measurement material_id queries
# (curation path), but should NOT appear in the public list or search.
UNKNOWN_MATERIAL_CANONICAL_NAME = "Unknown Material (canonical)"


async def list_materials(
    db: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    sort: str = "created_at",
    order: str = "desc",
    category_id: uuid.UUID | None = None,
) -> PaginatedResponse[MaterialResponse]:
    """Return a paginated list of materials, optionally filtered by category.

    NFM-4057: hides the canonical "Unknown Material (canonical)" row from
    the public list. Direct UUID lookup (get_material) and the
    dataset/property_measurement data paths are intentionally preserved.
    """
    stmt = select(Material).where(Material.name != UNKNOWN_MATERIAL_CANONICAL_NAME)

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

    NFM-4057: hides the canonical "Unknown Material (canonical)" row so
    that even a query like ``q=unknown`` does not surface it. The row
    remains accessible via get_material() and via
    dataset/property_measurement material_id queries.
    """
    stmt = select(Material).where(Material.name != UNKNOWN_MATERIAL_CANONICAL_NAME)

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
        )
    )
    total = (await db.execute(stmt)).scalar_one()
    return UncategorizedMaterialCountResponse(count=int(total))
