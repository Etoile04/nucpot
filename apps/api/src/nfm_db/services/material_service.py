"""Service layer for material CRUD and search queries."""

import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.models import Material, MaterialAlias
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.material import (
    MaterialAliasResponse,
    MaterialCompositionResponse,
    MaterialCreate,
    MaterialDetailResponse,
    MaterialResponse,
    MaterialUpdate,
)

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "name": Material.name,
    "created_at": Material.created_at,
    "updated_at": Material.updated_at,
}


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

    if category_id is not None:
        stmt = stmt.where(Material.category_id == category_id)

    sort_column = _SORT_COLUMNS.get(sort, Material.created_at)
    stmt = stmt.order_by(
        sort_column.desc() if order == "desc" else sort_column.asc()
    )

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


async def get_material(
    db: AsyncSession, material_id: uuid.UUID
) -> MaterialDetailResponse | None:
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
    composition = [
        MaterialCompositionResponse.model_validate(c) for c in row.composition
    ]
    base = MaterialResponse.model_validate(row)
    return MaterialDetailResponse(
        **base.model_dump(),
        aliases=aliases,
        composition=composition,
    )


async def create_material(
    db: AsyncSession, data: MaterialCreate
) -> MaterialResponse:
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
) -> PaginatedResponse[MaterialResponse]:
    """Search materials by name, formula, or alias (ILIKE).

    An empty query returns all materials (paginated).
    """
    stmt = select(Material)

    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            or_(
                Material.name.ilike(pattern),
                Material.formula.ilike(pattern),
                Material.aliases.any(MaterialAlias.alias_name.ilike(pattern)),
            )
        )

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
