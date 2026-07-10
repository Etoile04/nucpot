"""Potential API endpoints.

- GET  /api/v1/potentials              — paginated, filtered list
- GET  /api/v1/potentials/{id}         — full detail
- POST /api/v1/potentials              — create metadata (NFM-299 write path)
- POST /api/v1/potentials/{id}/file    — attach file (NFM-299 write path)
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.potential import (
    FileUploadResponse,
    PotentialCreateRequest,
    PotentialDetail,
    PotentialListResponse,
)
from nfm_db.services.potential_service import (
    get_potential_by_id,
    list_potentials,
)
from nfm_db.services.upload_service import (
    attach_potential_file,
    create_potential,
    get_upload_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["势函数管理"])


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


@router.post("/potentials", response_model=ApiResponse[PotentialDetail], status_code=201)
async def create_potential_endpoint(
    payload: PotentialCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    """Create a potential (metadata).  Validation ported from legacy Supabase prior-art."""
    # PotentialUploadError (incl. PotentialNameConflict) is translated to the
    # ApiResponse envelope by the handler registered in main.py.
    potential = await create_potential(db, payload)
    return ApiResponse(success=True, data=PotentialDetail.model_validate(potential))


@router.post(
    "/potentials/{potential_id}/file",
    response_model=ApiResponse[FileUploadResponse],
)
async def upload_potential_file_endpoint(
    potential_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    upload_dir: Path = Depends(get_upload_dir),
) -> ApiResponse[FileUploadResponse]:
    """Attach a file to a potential.  Validates extension + size (prior-art verbatim)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="No file provided")
    # PotentialNotFound / PotentialUploadError are translated to the envelope by
    # the handler registered in main.py.
    result = await attach_potential_file(db, upload_dir, potential_id, file.filename, data)
    return ApiResponse(success=True, data=FileUploadResponse(**result))
