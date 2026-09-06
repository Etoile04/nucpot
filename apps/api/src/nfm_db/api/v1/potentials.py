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
from nfm_db.schemas.common import ApiResponse, PaginationParams
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


@router.get("/potentials", response_model=ApiResponse[PotentialListResponse], summary="分页查询势函数列表", description="返回分页的势函数列表，支持按类型、元素和关键词筛选。\n\nReturn a paginated, filtered list of interatomic potentials.\n\n分页契约 (NFM-4308): `page_size` 是 `per_page` 的别名；超过上限 100 时按 100 执行，`data.limit` 回传实际生效值并置 `data.truncated: true`。")
async def list_potentials_endpoint(
    pagination: PaginationParams = Depends(PaginationParams),
    type: str | None = Query(None),
    elements: str | None = Query(None, description="Comma-separated element symbols"),
    q: str | None = Query(None),
    sort: str = Query("updated", pattern="^(updated|name|type)$"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialListResponse]:
    """Return a paginated, filtered list of interatomic potentials.

    分页契约 (NFM-4308 ③): ``page_size`` 为 ``per_page`` 别名（显式非默认
    ``per_page`` 优先）；默认 20，上限 100。超限值按 100 执行并在
    ``data.limit`` / ``data.truncated`` 回传实际生效值与截断标志。
    """
    elements_list = [e for e in (elements.split(",") if elements else [])] or None
    result = await list_potentials(
        db,
        page=pagination.page,
        limit=pagination.per_page,
        type_filter=type,
        elements=elements_list,
        query=q,
        sort=sort,
    )
    return ApiResponse(success=True, data=result.model_copy(update={"truncated": pagination.truncated}))


@router.post("/potentials", response_model=ApiResponse, status_code=201, summary="创建势函数", description="创建一条新的势函数记录。\n\nCreate a new interatomic potential.")
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


@router.get("/potentials/{potential_id}", response_model=ApiResponse[PotentialDetail], summary="获取势函数详情", description="获取单个势函数的详细信息。\n\nReturn full detail of a single interatomic potential.")
async def get_potential_endpoint(
    potential_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    detail = await get_potential_by_id(db, potential_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Potential not found")
    return ApiResponse(success=True, data=detail)


@router.post("/potentials/{potential_id}/file", response_model=ApiResponse, summary="上传势函数文件", description="为指定势函数上传势函数文件（如 .eam、.meam、.fs 等）。\n\nUpload a potential file for a given potential.")
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

    # NFM-4087 (BUG-06): align with upload_service.ALLOWED_EXTENSIONS —
    # tersoff/sw/bop/mtp etc. are legitimate potential formats that the
    # service layer already allows but this endpoint's narrower list 400'd.
    allowed = {
        ".eam", ".eam.alloy", ".eam.fs", ".alloy", ".fs", ".meam",
        ".repram", ".json", ".yaml", ".txt", ".dat", ".pot", ".setfl",
        ".param", ".table", ".mtp", ".snap", ".zip", ".tar.gz", ".gz",
        ".reaxff", ".tersoff", ".sw", ".bop", ".comb", ".lj", ".adp",
        ".spt", ".pb", ".pth",
    }
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    lower_name = (file.filename or "").lower()
    if not any(lower_name.endswith(a) for a in allowed):
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")

    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{potential_id}{ext}"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    dest.write_bytes(content)

    # NFM-4087 (BUG-06): persist the URL so the row's file_url stops being
    # NULL — previously the endpoint only wrote bytes to disk and the DB
    # never learned the file existed (详情页仍显示"缺少文件").
    import hashlib

    from sqlalchemy import update as sa_update

    web_path = f"/uploads/{potential_id}{ext}"
    await db.execute(
        sa_update(Potential)
        .where(Potential.id == potential_id)
        .values(
            file_url=web_path,
            file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
        )
    )
    await db.commit()

    return ApiResponse(success=True, data={"file_path": web_path, "file_name": file.filename})


@router.patch(
    "/potentials/{potential_id}/verification",
    response_model=ApiResponse[PotentialDetail],
    summary="更新验证状态",
    description="更新势函数的验证状态，由自动验证服务回调触发。\n\nUpdate a potential's verification status via autovc callback seam.",
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
