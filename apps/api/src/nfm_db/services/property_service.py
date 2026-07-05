"""Service layer for property CRUD and stats queries (NFM-697)."""

import logging
import uuid
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nfm_db.models import (
    Dataset,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.common import PaginatedResponse
from nfm_db.schemas.property import (
    DatasetResponse,
    MaterialMeasurementCount,
    MeasurementConditionResponse,
    PropertyCategoryCount,
    PropertyMeasurementCreate,
    PropertyMeasurementDetailResponse,
    PropertyMeasurementResponse,
    PropertyMeasurementUpdate,
    PropertyStatsResponse,
)

logger = logging.getLogger(__name__)

_SORT_COLUMNS = {
    "created_at": PropertyMeasurement.created_at,
    "updated_at": PropertyMeasurement.updated_at,
}


async def list_measurements(
    db: AsyncSession,
    *,
    page: int = 1,
    per_page: int = 20,
    sort: str = "created_at",
    order: Literal["asc", "desc"] = "desc",
    material_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None = None,
) -> PaginatedResponse[PropertyMeasurementResponse]:
    """Return a paginated list of measurements, optionally filtered by material or property type."""

    # Build base query with join to Dataset for material_id filtering
    stmt = select(PropertyMeasurement).join(Dataset, PropertyMeasurement.dataset_id == Dataset.id)

    if material_id is not None:
        stmt = stmt.where(Dataset.material_id == material_id)

    if property_type_id is not None:
        stmt = stmt.where(PropertyMeasurement.property_type_id == property_type_id)

    # Add sorting
    sort_column = _SORT_COLUMNS.get(sort, PropertyMeasurement.created_at)
    stmt = stmt.order_by(sort_column.desc() if order == "desc" else sort_column.asc())

    # Get total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Apply pagination
    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)
    rows = (await db.execute(stmt)).scalars().all()

    items = [PropertyMeasurementResponse.model_validate(r) for r in rows]
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        limit=per_page,
        pages=max(1, -(-total // per_page)),  # ceil
    )


async def get_measurement(
    db: AsyncSession, measurement_id: uuid.UUID
) -> PropertyMeasurementDetailResponse | None:
    """Return a measurement with conditions and dataset, or None."""

    stmt = (
        select(PropertyMeasurement)
        .options(
            selectinload(PropertyMeasurement.conditions),
            selectinload(PropertyMeasurement.dataset),
        )
        .where(PropertyMeasurement.id == measurement_id)
    )

    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    # Build conditions list
    conditions = [MeasurementConditionResponse.model_validate(c) for c in row.conditions]

    # Build dataset response
    dataset = DatasetResponse.model_validate(row.dataset) if row.dataset else None

    # Build base response
    base = PropertyMeasurementResponse.model_validate(row)

    return PropertyMeasurementDetailResponse(
        **base.model_dump(),
        conditions=conditions,
        dataset=dataset,
    )


async def create_measurement(
    db: AsyncSession, data: PropertyMeasurementCreate
) -> PropertyMeasurementResponse:
    """Create a new property measurement and return it."""

    measurement = PropertyMeasurement(**data.model_dump())
    db.add(measurement)
    await db.commit()
    await db.refresh(measurement)

    return PropertyMeasurementResponse.model_validate(measurement)


async def update_measurement(
    db: AsyncSession, measurement_id: uuid.UUID, data: PropertyMeasurementUpdate
) -> PropertyMeasurementResponse | None:
    """Update an existing measurement. Returns None if not found."""

    stmt = select(PropertyMeasurement).where(PropertyMeasurement.id == measurement_id)
    row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        return None

    # Apply updates
    updates = data.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)

    db.add(row)
    await db.commit()
    await db.refresh(row)

    return PropertyMeasurementResponse.model_validate(row)


async def get_measurement_stats(db: AsyncSession) -> PropertyStatsResponse:
    """Return aggregate statistics about measurements by category and material.

    Uses func.count() group-by (no raw SQL).
    """

    # Total count
    total_stmt = select(func.count()).select_from(PropertyMeasurement)
    total = (await db.execute(total_stmt)).scalar_one()

    # Count by property category
    category_stmt = (
        select(
            PropertyCategory.name,
            func.count(PropertyMeasurement.id).label("cnt"),
        )
        .join(PropertyType, PropertyType.category_id == PropertyCategory.id)
        .join(
            PropertyMeasurement,
            PropertyMeasurement.property_type_id == PropertyType.id,
        )
        .group_by(PropertyCategory.name)
        .order_by(func.count(PropertyMeasurement.id).desc())
    )

    category_result = await db.execute(category_stmt)
    by_category = [
        PropertyCategoryCount(category=name, count=count) for name, count in category_result.all()
    ]

    # Count by material (join through Dataset, join to Material for name)
    material_stmt = (
        select(
            Material.id,
            Material.name,
            func.count(PropertyMeasurement.id).label("cnt"),
        )
        .join(Dataset, Dataset.material_id == Material.id)
        .join(
            PropertyMeasurement,
            PropertyMeasurement.dataset_id == Dataset.id,
        )
        .group_by(Material.id, Material.name)
        .order_by(func.count(PropertyMeasurement.id).desc())
    )

    material_result = await db.execute(material_stmt)
    by_material = [
        MaterialMeasurementCount(material_id=mid, material_name=name, count=count)
        for mid, name, count in material_result.all()
    ]

    return PropertyStatsResponse(
        total_measurements=total,
        by_category=by_category,
        by_material=by_material,
    )
