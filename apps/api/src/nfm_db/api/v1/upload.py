"""Chunked upload API endpoints (NFM-2024).

Provides four endpoints under ``/api/v1/upload/``:
  - POST /init    — initialise a chunked upload session
  - POST /chunk   — upload a single chunk (multipart: file + form fields)
  - POST /complete — finalise: assemble chunks, verify full SHA256
  - POST /resume  — query missing chunk indices for a paused upload
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.data_submission import (
    ChunkUploadResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadResumeRequest,
    UploadResumeResponse,
)
from nfm_db.services import chunk_upload_service as svc

router = APIRouter(prefix="/upload", tags=["断点续传"])


@router.post(
    "/init",
    response_model=ApiResponse[UploadInitResponse],
    summary="初始化上传会话",
    description="Create a new chunked upload session. Returns a resume_token for subsequent chunk uploads.",
)
async def upload_init(
    payload: UploadInitRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UploadInitResponse]:
    """Initialise a new upload session."""
    result = await svc.init_upload(db, payload)
    return ApiResponse(success=True, data=result)


@router.post(
    "/chunk",
    response_model=ApiResponse[ChunkUploadResponse],
    summary="上传分块",
    description="Upload a single chunk. Verifies per-chunk SHA256 before accepting.",
)
async def upload_chunk(
    resume_token: Annotated[str, Form()],
    chunk_index: Annotated[int, Form(ge=0)],
    sha256_chunk: Annotated[str, Form(min_length=64, max_length=64)],
    chunk_data: UploadFile = File(..., description="Binary chunk data"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ChunkUploadResponse]:
    """Upload a single chunk with SHA256 verification."""
    data = await chunk_data.read()
    try:
        result = await svc.upload_chunk(
            db=db,
            resume_token=resume_token,
            chunk_index=chunk_index,
            sha256_chunk=sha256_chunk,
            chunk_data=data,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ApiResponse(success=True, data=ChunkUploadResponse(**result))


@router.post(
    "/complete",
    response_model=ApiResponse[UploadCompleteResponse],
    summary="完成上传",
    description="Finalise the upload: assemble chunks, verify full-file SHA256.",
)
async def upload_complete(
    payload: UploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UploadCompleteResponse]:
    """Complete an upload session with full-file SHA256 verification."""
    result = await svc.complete_upload(db, payload.resume_token)
    return ApiResponse(success=True, data=result)


@router.post(
    "/resume",
    response_model=ApiResponse[UploadResumeResponse],
    summary="恢复中断上传",
    description="Query the current upload state and list missing chunk indices.",
)
async def upload_resume(
    payload: UploadResumeRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[UploadResumeResponse]:
    """Resume an interrupted upload by reporting missing chunks."""
    result = await svc.resume_upload(db, payload.resume_token)
    return ApiResponse(success=True, data=result)
