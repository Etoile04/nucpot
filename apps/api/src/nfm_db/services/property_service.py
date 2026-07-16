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
    MaterialPropertyItem,
    MaterialPropertyListMeta,
    MaterialPropertyListResponse,
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


# ---------------------------------------------------------------------------
# Material-scoped property listing (NFM-1067)
# ---------------------------------------------------------------------------


# Confidence is a derived field — no `confidence` column on the model.
# We map the curator-driven `review_status` to a 0..1 float the frontend
# can render as a badge.
_REVIEW_STATUS_TO_CONFIDENCE: dict[str, float] = {
    "approved": 0.95,
    "verified": 0.95,
    "pending": 0.7,
    "flagged": 0.5,
    "rejected": 0.3,
}

# Allowed sort columns for the per-material property table.
_MATERIAL_PROPERTY_SORT_COLUMNS = {
    "name": lambda: PropertyType.name,
    "value": lambda: PropertyMeasurement.value_scalar,
    "created_at": lambda: PropertyMeasurement.created_at,
}


def _format_measurement_value(measurement: PropertyMeasurement) -> str:
    """Render a measurement's value column as a short, human-readable string.

    Priority: expression > range > scalar > list > text. The order mirrors
    what curators typically populate first when recording data.
    """
    if measurement.value_expression is not None:
        return str(measurement.value_expression)
    if measurement.value_min is not None and measurement.value_max is not None:
        return f"{float(measurement.value_min)}-{float(measurement.value_max)}"
    if measurement.value_scalar is not None:
        # Decimal → float so the trailing zeros from Numeric(16, 6) are dropped
        # (the database returns Decimal("5.680000") for value_scalar=5.68).
        return str(float(measurement.value_scalar))
    if measurement.value_list is not None:
        return "[" + ", ".join(str(float(v)) for v in measurement.value_list) + "]"
    if measurement.value_text is not None:
        return str(measurement.value_text)
    return "—"


def _derive_confidence(measurement: PropertyMeasurement) -> float:
    """Map review_status → 0..1 confidence score."""
    return _REVIEW_STATUS_TO_CONFIDENCE.get(
        (measurement.review_status or "pending").lower(),
        0.5,
    )


def _resolve_unit_symbol(measurement: PropertyMeasurement) -> str | None:
    """Use the measurement's unit, falling back to the property type's default."""
    if measurement.unit is not None:
        return measurement.unit.symbol
    if measurement.property_type is not None and measurement.property_type.default_unit is not None:
        return measurement.property_type.default_unit.symbol
    return None


async def list_material_properties(
    db: AsyncSession,
    material_id: uuid.UUID,
    *,
    page: int = 1,
    limit: int = 50,
    sort: str = "name",
    order: Literal["asc", "desc"] = "asc",
    filter: str | None = None,
) -> MaterialPropertyListResponse | None:
    """Return the property table for a single material, or ``None`` if missing.

    Joins:
      PropertyMeasurement → Dataset (material_id filter + source)
                        → DataSource (source title)
                        → PropertyType (name + default unit)
                        → Unit (measurement unit symbol)

    The inner shape is ``{ data: [...], meta: {...} }`` — distinct from the
    standard ``PaginatedResponse`` because the frontend React table consumes
    a single flat payload per page.
    """
    # 1. Material must exist — return None so the route can raise 404.
    mat_exists = (
        await db.execute(select(Material.id).where(Material.id == material_id))
    ).scalar_one_or_none()
    if mat_exists is None:
        return None

    # 2. Base query. Eager-load related rows needed to render the table
    #    without N+1 queries.
    stmt = (
        select(PropertyMeasurement)
        .join(Dataset, PropertyMeasurement.dataset_id == Dataset.id)
        .options(
            selectinload(PropertyMeasurement.property_type).selectinload(
                PropertyType.default_unit
            ),
            selectinload(PropertyMeasurement.unit),
            selectinload(PropertyMeasurement.dataset).selectinload(Dataset.source),
        )
        .where(Dataset.material_id == material_id)
    )

    # 3. Sort. `name` and `value` need the property_types join; `created_at`
    #    does not. We join PropertyType ONCE when needed for either sort or
    #    filter, to avoid `ambiguous column name` errors.
    sort_key = sort if sort in _MATERIAL_PROPERTY_SORT_COLUMNS else "name"
    needs_property_type_join = bool(filter) or sort_key in ("name", "value")
    if needs_property_type_join:
        stmt = stmt.join(
            PropertyType, PropertyMeasurement.property_type_id == PropertyType.id
        )

    # 4. Optional name filter (case-insensitive substring on PropertyType.name).
    if filter:
        stmt = stmt.where(PropertyType.name.ilike(f"%{filter}%"))

    sort_col = _MATERIAL_PROPERTY_SORT_COLUMNS[sort_key]()
    stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    # 5. Total count for the meta block.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # 6. Paginate.
    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    # 7. Render rows. Sorting is on a single string column, so the flat
    #    response is a deterministic list — no further re-ordering.
    items: list[MaterialPropertyItem] = []
    for r in rows:
        items.append(
            MaterialPropertyItem(
                id=r.id,
                name=r.property_type.name if r.property_type is not None else "",
                value=_format_measurement_value(r),
                unit=_resolve_unit_symbol(r),
                source=r.dataset.source.title if r.dataset and r.dataset.source else "",
                confidence=_derive_confidence(r),
            )
        )

    return MaterialPropertyListResponse(
        data=items,
        meta=MaterialPropertyListMeta(total=total, page=page, limit=limit),
    )
