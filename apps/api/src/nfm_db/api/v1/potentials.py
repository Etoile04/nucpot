"""Potential API endpoints.

- GET  /api/v1/potentials                  — paginated, filtered list
- GET  /api/v1/potentials/{id}             — full detail
- PATCH /api/v1/potentials/{id}/verification — autovc verification seam
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.potential import (
    PotentialCreateRequest,
    PotentialDetail,
    PotentialListResponse,
    VerificationUpdate,
)
from nfm_db.services.potential_service import (
    get_potential_by_id,
    list_potentials,
    update_potential_verification,
)
from nfm_db.services.upload_service import (
    PotentialNameConflictError,
    PotentialUploadError,
    create_potential,
    get_upload_dir,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["势函数管理"])


@router.get("/potentials", response_model=ApiResponse[PotentialListResponse])
async def list_potentials_endpoint(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100, alias="per_page"),
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


@router.post("/potentials", response_model=ApiResponse, status_code=201)
async def create_potential_endpoint(
    payload: PotentialCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    """Create a new potential (NFM-299 write path)."""
    try:
        potential = await create_potential(db, payload)
        await db.commit()
        await db.refresh(potential)
        return ApiResponse(success=True, data={"id": str(potential.id), "name": potential.name, "display_name": potential.display_name, "type": potential.type, "elements": potential.elements, "format": potential.format, "description": potential.description, "version": potential.version, "tags": potential.tags})
    except PotentialNameConflictError:
        raise HTTPException(status_code=409, detail="Potential name already exists")
    except PotentialUploadError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get("/potentials/{potential_id}", response_model=ApiResponse[PotentialDetail])
async def get_potential_endpoint(
    potential_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    detail = await get_potential_by_id(db, potential_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Potential not found")
    return ApiResponse(success=True, data=detail)


@router.post("/potentials/{potential_id}/file", response_model=ApiResponse)
async def upload_potential_file(
    potential_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    upload_dir: Path = Depends(get_upload_dir),
) -> ApiResponse[dict[str, Any]]:
    """Upload a potential file (NFM-299 write path)."""
    from sqlalchemy import select as sa_select

    from nfm_db.models.potential import Potential

    result = await db.execute(sa_select(Potential).where(Potential.id == potential_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Potential not found")

    allowed = {".eam", ".alloy", ".fs", ".meam", ".repram", ".json", ".yaml", ".txt", ".dat", ".pot"}
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")

    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{potential_id}{ext}"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    dest.write_bytes(content)

    return ApiResponse(success=True, data={"file_path": str(dest), "file_name": file.filename})


@router.patch(
    "/potentials/{potential_id}/verification",
    response_model=ApiResponse[PotentialDetail],
)
async def patch_verification_endpoint(
    potential_id: UUID,
    body: VerificationUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    """Update a potential's verification status — defensive autovc callback seam.

    Called by nucpot-autovc after async verification completes. No auth guard
    yet (deferred per ADR-2). Validation of the status enum happens on the
    ``VerificationUpdate`` schema (invalid values return 422).
    """
    updated = await update_potential_verification(
        db,
        potential_id,
        body.verification_status,
        message=body.message,
        evidence_url=body.evidence_url,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Potential not found")
    return ApiResponse(success=True, data=PotentialDetail.model_validate(updated))
