"""Potential API endpoints.

- GET /api/v1/potentials         — paginated, filtered list
- GET /api/v1/potentials/{id}    — full detail
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.potential import (
    PotentialDetail,
    PotentialListResponse,
)
from nfm_db.services.potential_service import (
    get_potential_by_id,
    list_potentials,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/potentials", response_model=ApiResponse[PotentialListResponse])
async def list_potentials_endpoint(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    type: str | None = Query(None),
    elements: str | None = Query(None, description="Comma-separated element symbols"),
    q: str | None = Query(None),
    sort: str = Query("updated", pattern="^(updated|name|type)$"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialListResponse]:
    elements_list = [e for e in (elements.split(",") if elements else [])] or None
    result = await list_potentials(
        db,
        page=page,
        limit=limit,
        type_filter=type,
        elements=elements_list,
        query=q,
        sort=sort,
    )
    return ApiResponse(success=True, data=result)


@router.get("/potentials/{potential_id}", response_model=ApiResponse[PotentialDetail])
async def get_potential_endpoint(
    potential_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    detail = await get_potential_by_id(db, potential_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Potential not found")
    return ApiResponse(success=True, data=detail)
