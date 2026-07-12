"""Conflict Resolution API endpoints (Phase 2).

NFM-817 §5.2: 2 endpoints for listing and resolving multi-source conflicts.

Endpoints:
- GET  /conflicts              — List conflict records
- POST /conflicts/{id}/resolve — Resolve a conflict
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.conflict_record import ConflictRecord
from nfm_db.models.material import Material
from nfm_db.models.property import PropertyType
from nfm_db.models.source import DataSource
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.conflict import (
    ConflictRecordResponse,
    ConflictResolveRequest,
    SourceValue,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kg/conflicts", tags=["冲突管理"])

VALID_STRATEGIES = {"newest", "confidence", "consensus", "manual"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _enrich_conflict(
    record: ConflictRecord,
    db: AsyncSession,
) -> ConflictRecordResponse:
    """Add material_name and property_type to a conflict record response."""
    material = await db.get(Material, record.material_id)
    prop_type = await db.get(PropertyType, record.property_type_id)

    source_values: list[SourceValue] = []
    for sv in record.source_values:
        source_title = None
        if sv.get("source_id"):
            ds = await db.get(DataSource, sv["source_id"])
            if ds is not None:
                source_title = ds.title
        source_values.append(
            SourceValue(
                source_id=sv.get("source_id", uuid.uuid4()),
                source_title=source_title,
                value=sv.get("value"),
                confidence=sv.get("confidence", 0.0),
            )
        )

    return ConflictRecordResponse(
        id=record.id,
        material_id=record.material_id,
        material_name=material.name if material else None,
        property_type=prop_type.name if prop_type else None,
        source_values=source_values,
        resolution=record.resolution,
        resolved_value=record.resolved_value,
        created_at=record.created_at,
    )


async def _auto_resolve(
    record: ConflictRecord,
    strategy: str,
) -> dict[str, Any]:
    """Apply an automatic resolution strategy to a conflict."""
    values = record.source_values
    if not values:
        return {}

    if strategy == "confidence":
        best = max(values, key=lambda v: v.get("confidence", 0.0))
        return {"value": best.get("value"), "confidence": best.get("confidence", 0.0)}

    if strategy == "newest":
        # Assume the last entry is newest.
        newest = values[-1]
        return {"value": newest.get("value"), "confidence": newest.get("confidence", 0.0)}

    if strategy == "consensus":
        # Average numeric values, or most common non-numeric.
        numeric_vals = [v["value"] for v in values if isinstance(v.get("value"), (int, float))]
        if numeric_vals:
            return {"value": sum(numeric_vals) / len(numeric_vals)}
        return values[0].get("value", {})

    # "manual" returns whatever was provided.
    return {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ApiResponse[list[ConflictRecordResponse]],
    summary="获取冲突记录列表",
)
async def list_conflicts(
    material_id: uuid.UUID | None = Query(None),
    property_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ConflictRecordResponse]]:
    """获取冲突记录列表，支持按材料或属性类型筛选。

    Return conflict records, optionally filtered by material or property type.
    """
    stmt = select(ConflictRecord).order_by(
        ConflictRecord.created_at.desc(),
    )

    if material_id is not None:
        stmt = stmt.where(ConflictRecord.material_id == material_id)
    if property_type is not None:
        # Join with property_types to filter by name/slug.
        pt_stmt = select(PropertyType.id).where(
            PropertyType.name.ilike(f"%{property_type}%"),
        )
        pt_result = await db.execute(pt_stmt)
        pt_ids = list(pt_result.scalars().all())
        if pt_ids:
            stmt = stmt.where(
                ConflictRecord.property_type_id.in_(pt_ids),
            )

    result = await db.execute(stmt)
    records = result.scalars().all()

    items = []
    for record in records:
        items.append(await _enrich_conflict(record, db))

    return ApiResponse(success=True, data=items)


@router.post(
    "/{conflict_id}/resolve",
    response_model=ApiResponse[ConflictRecordResponse],
    summary="解决冲突记录",
)
async def resolve_conflict(
    conflict_id: uuid.UUID,
    body: ConflictResolveRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ConflictRecordResponse]:
    """使用指定策略解决数据冲突。

    Resolve a conflict using the specified strategy.
    """
    if body.strategy not in VALID_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy: {body.strategy}. Must be one of: {VALID_STRATEGIES}",
        )

    record = await db.get(ConflictRecord, conflict_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conflict record not found")

    if body.strategy == "manual":
        if body.selected_value is None:
            raise HTTPException(
                status_code=400,
                detail="selected_value is required when strategy='manual'",
            )
        record.resolved_value = body.selected_value
    else:
        record.resolved_value = await _auto_resolve(record, body.strategy)

    record.resolution = body.strategy
    record.resolved_at = datetime.now(UTC)
    record.resolved_by = None  # Set by auth middleware in production
    await db.commit()
    await db.refresh(record)

    return ApiResponse(
        success=True,
        data=await _enrich_conflict(record, db),
    )
