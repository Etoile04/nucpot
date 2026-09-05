"""Potential API endpoints.

- GET  /api/v1/potentials                  — paginated, filtered list
- GET  /api/v1/potentials/{id}             — full detail
- GET  /api/v1/potentials/{id}/file        — canonical anonymous file download (NFM-4309)
- PATCH /api/v1/potentials/{id}/verification — autovc verification seam
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

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
from nfm_db.services.potential_file_resolver import (
    canonical_file_url,
    is_supabase_public_url,
    public_object_url,
    record_potential_download,
    resolve_storage_ref,
    validate_persistable_file_url,
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

# ── canonical file download proxy (NFM-4309 / BUG-37) ─────────────────────────

_REMOTE_FETCH_TIMEOUT = httpx.Timeout(60.0)
_STREAM_CHUNK_SIZE = 64 * 1024


async def _aclose(obj: object) -> None:
    """Close an httpx-like object if it exposes ``aclose``."""
    close = getattr(obj, "aclose", None)
    if close is not None:
        await close()


class RemoteFileStream:
    """Owns an httpx streaming response for hand-off to ``StreamingResponse``.

    Adapted so tests can substitute a fake ``httpx.AsyncClient`` exposing
    only ``stream()`` (the real client's context manager semantics are
    unwound manually via ``aclose``).
    """

    def __init__(self, client: Any, response: Any, stream_cm: Any = None) -> None:
        self._client = client
        self.response = response
        # Keep the async context manager returned by ``client.stream()``
        # referenced for the lifetime of the stream.  Dropping the only
        # reference lets CPython finalize its async generator, whose
        # ``finally`` closes the response — after any intervening await the
        # body iteration then fails with ``httpx.StreamClosed`` and the
        # client receives a truncated (empty) 200 body.
        self._stream_cm = stream_cm if stream_cm is not None else response

    @classmethod
    async def open(cls, url: str) -> RemoteFileStream:
        client = httpx.AsyncClient(follow_redirects=True, timeout=_REMOTE_FETCH_TIMEOUT)
        stream_cm = client.stream("GET", url)
        try:
            response = await stream_cm.__aenter__()
        except Exception:
            await _aclose(client)
            raise
        return cls(client, response, stream_cm)

    @property
    def status_code(self) -> int:
        return int(self.response.status_code)

    @property
    def content_type(self) -> str | None:
        value: str | None = self.response.headers.get("content-type")
        return value

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_bytes(_STREAM_CHUNK_SIZE):
            yield chunk

    async def aclose(self) -> None:
        aexit = getattr(self._stream_cm, "__aexit__", None)
        if aexit is not None:
            await aexit(None, None, None)
        else:
            await _aclose(self.response)
        await _aclose(self._client)


@router.get(
    "/potentials/{potential_id}/file",
    response_model=None,
    summary="下载势函数文件",
    description="匿名下载势函数文件（NFM-4309 规范下载地址）。后端解析实际存储位置（共享上传卷或 Supabase 公开对象）并流式返回；多文件条目用 ?index= 选择（默认 0）。\n\nAnonymous canonical download URL for a potential's file.",
    responses={
        200: {"content": {"application/octet-stream": {}}},
        404: {"description": "势函数或文件不存在"},
    },
)
async def download_potential_file(
    potential_id: UUID,
    index: int = Query(0, ge=0, description="多文件条目的对象下标（默认 0）"),
    db: AsyncSession = Depends(get_db),
) -> FileResponse | StreamingResponse:
    """Canonical anonymous download proxy (BUG-37 spec §1)."""
    from sqlalchemy import select as sa_select

    from nfm_db.models.potential import Potential

    potential = (
        await db.execute(sa_select(Potential).where(Potential.id == potential_id))
    ).scalar_one_or_none()
    if potential is None:
        raise HTTPException(status_code=404, detail="Potential not found")

    ref = resolve_storage_ref(potential.extra, potential.file_url)
    if ref is None:
        raise HTTPException(status_code=404, detail="Potential has no downloadable file")

    if ref.get("kind") == "uploads":
        upload_root = get_upload_dir().resolve()
        key = str(ref.get("key", ""))
        candidate = (upload_root / key).resolve()
        if not candidate.is_relative_to(upload_root) or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Potential file not found")
        await _record_download(db, potential_id)
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return FileResponse(
            candidate,
            filename=candidate.name,
            media_type=media_type,
        )

    objects = [str(o) for o in (ref.get("objects") or []) if str(o)]
    if index >= len(objects):
        raise HTTPException(
            status_code=404,
            detail=f"Potential file index {index} out of range ({len(objects)} file(s))",
        )
    filename = Path(objects[index].rstrip("/")).name or "potential_file"
    url = public_object_url(objects[index])
    if not is_supabase_public_url(url):
        return RedirectResponse(url, status_code=307)
    stream = await RemoteFileStream.open(url)
    if stream.status_code != 200:
        await stream.aclose()
        detail = "Potential file not found in object storage"
        code = 404 if stream.status_code in (400, 403, 404) else 502
        if code == 502:
            detail = f"Object storage returned {stream.status_code}"
        raise HTTPException(status_code=code, detail=detail)
    await _record_download(db, potential_id)
    return StreamingResponse(
        stream.aiter_bytes(),
        media_type=stream.content_type or "application/octet-stream",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(stream.aclose),
    )


async def _record_download(db: AsyncSession, potential_id: UUID) -> None:
    """Count a served download; statistics must never fail the download."""
    try:
        await record_potential_download(db, potential_id)
    except Exception:
        await db.rollback()
        logger.warning(
            "download_count update failed for potential %s", potential_id, exc_info=True
        )


@router.get(
    "/potentials",
    response_model=ApiResponse[PotentialListResponse],
    summary="分页查询势函数列表",
    description="返回分页的势函数列表，支持按类型、元素和关键词筛选。\n\nReturn a paginated, filtered list of interatomic potentials.",
)
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


@router.post(
    "/potentials",
    response_model=ApiResponse,
    status_code=201,
    summary="创建势函数",
    description="创建一条新的势函数记录。\n\nCreate a new interatomic potential.",
)
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
        return ApiResponse(
            success=True,
            data={
                "id": str(potential.id),
                "name": potential.name,
                "display_name": potential.display_name,
                "type": potential.type,
                "elements": potential.elements,
                "format": potential.format,
                "description": potential.description,
                "version": potential.version,
                "tags": potential.tags,
            },
        )
    except PotentialNameConflictError:
        raise HTTPException(status_code=409, detail="Potential name already exists")
    except PotentialUploadError as e:
        raise HTTPException(status_code=400, detail=e.message)


@router.get(
    "/potentials/{potential_id}",
    response_model=ApiResponse[PotentialDetail],
    summary="获取势函数详情",
    description="获取单个势函数的详细信息。\n\nReturn full detail of a single interatomic potential.",
)
async def get_potential_endpoint(
    potential_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PotentialDetail]:
    detail = await get_potential_by_id(db, potential_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Potential not found")
    return ApiResponse(success=True, data=detail)


@router.post(
    "/potentials/{potential_id}/file",
    response_model=ApiResponse,
    summary="上传势函数文件",
    description="为指定势函数上传势函数文件（如 .eam、.meam、.fs 等）。\n\nUpload a potential file for a given potential.",
)
async def upload_potential_file(
    potential_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    upload_dir: Path = Depends(get_upload_dir),
) -> ApiResponse[dict[str, Any]]:
    """Upload a potential file (NFM-299 write path; NFM-4309 canonical URL)."""
    import hashlib

    from sqlalchemy import select as sa_select

    from nfm_db.models.potential import Potential

    potential = (
        await db.execute(sa_select(Potential).where(Potential.id == potential_id))
    ).scalar_one_or_none()
    if potential is None:
        raise HTTPException(status_code=404, detail="Potential not found")

    # NFM-4087 (BUG-06): align with upload_service.ALLOWED_EXTENSIONS —
    # tersoff/sw/bop/mtp etc. are legitimate potential formats that the
    # service layer already allows but this endpoint's narrower list 400'd.
    allowed = {
        ".eam",
        ".eam.alloy",
        ".eam.fs",
        ".alloy",
        ".fs",
        ".meam",
        ".repram",
        ".json",
        ".yaml",
        ".txt",
        ".dat",
        ".pot",
        ".setfl",
        ".param",
        ".table",
        ".mtp",
        ".snap",
        ".zip",
        ".tar.gz",
        ".gz",
        ".reaxff",
        ".tersoff",
        ".sw",
        ".bop",
        ".comb",
        ".lj",
        ".adp",
        ".spt",
        ".pb",
        ".pth",
    }
    # Longest matching allowed suffix so multi-part extensions survive
    # (.eam.alloy stays .eam.alloy — not just .alloy).
    lower_name = (file.filename or "").lower()
    ext = next(
        (a for a in sorted(allowed, key=len, reverse=True) if lower_name.endswith(a)),
        "",
    )
    if not ext:
        raise HTTPException(status_code=400, detail=f"File type not allowed: '{lower_name}'")

    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{potential_id}{ext}"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    dest.write_bytes(content)

    # NFM-4309 (BUG-37): persist the canonical proxy URL — never a
    # site-relative (/uploads/…) or container (/app/uploads/…) path.  The
    # backing location is recorded in extra.file_storage; the URL is
    # validated against the shape contract before it reaches the DB.
    web_path = canonical_file_url(potential_id)
    validate_persistable_file_url(web_path)
    potential.file_url = web_path
    potential.file_size = len(content)
    potential.file_hash = hashlib.sha256(content).hexdigest()
    potential.extra = {
        **(potential.extra or {}),
        "file_storage": {"kind": "uploads", "key": f"{potential_id}{ext}"},
    }
    await db.commit()

    return ApiResponse(
        success=True,
        data={"file_path": web_path, "file_url": web_path, "file_name": file.filename},
    )


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
