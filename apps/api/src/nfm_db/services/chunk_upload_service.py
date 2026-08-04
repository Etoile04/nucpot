"""Chunked upload service with resume support and SHA256 verification (NFM-2024).

Coordinates chunked file uploads through four operations:
  - init:    create an UploadSession with a resume token
  - chunk:   accept a single chunk, verify per-chunk SHA256, persist to disk
  - complete: assemble all chunks, verify full-file SHA256, finalise session
  - resume:  report which chunk indices are still missing
"""

from __future__ import annotations

import hashlib
import math
import os
import secrets
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.classification_level import ClassificationLevel
from nfm_db.models.resource_node import ResourceNode
from nfm_db.models.upload_session import UploadSession
from nfm_db.schemas.data_submission import (
    UploadCompleteResponse,
    UploadInitRequest,
    UploadInitResponse,
    UploadResumeResponse,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CHUNK_STORAGE_ROOT: Path | None = None
_DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB


def _resolve_chunk_root() -> Path:
    """Return the chunk storage root directory."""
    if _CHUNK_STORAGE_ROOT is not None:
        return _CHUNK_STORAGE_ROOT
    raw = os.environ.get("CHUNK_STORAGE_ROOT", "/app/uploads/chunks")
    return Path(raw)


def set_chunk_storage_root(path: Path) -> None:
    """Override the chunk storage root (for testing)."""
    global _CHUNK_STORAGE_ROOT  # noqa: PLW0603
    _CHUNK_STORAGE_ROOT = path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_resume_token() -> str:
    """Return a cryptographically random 48-char hex token."""
    return secrets.token_hex(24)


def _chunk_dir_for(root: Path, session_id: uuid.UUID) -> Path:
    """Return the directory path where chunks for *session_id* are stored."""
    d = root / str(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _count_existing_chunks(chunk_dir: Path, total_chunks: int) -> int:
    """Count how many chunk files exist on disk for this session."""
    return sum(1 for idx in range(total_chunks) if (chunk_dir / str(idx)).is_file())


def _list_missing_chunks(chunk_dir: Path, total_chunks: int) -> list[int]:
    """Return zero-based indices of chunks not yet received."""
    return [idx for idx in range(total_chunks) if not (chunk_dir / str(idx)).is_file()]


def _assemble_and_hash(chunk_dir: Path, total_chunks: int) -> bytes:
    """Read all chunks in order and return the concatenated bytes."""
    parts: list[bytes] = []
    for idx in range(total_chunks):
        path = chunk_dir / str(idx)
        if not path.is_file():
            raise ValueError(f"Chunk {idx} is missing — cannot assemble")
        parts.append(path.read_bytes())
    return b"".join(parts)


def _compute_sha256(data: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------


async def init_upload(
    db: AsyncSession,
    payload: UploadInitRequest,
) -> UploadInitResponse:
    """Create a new upload session and return init metadata."""
    node = (
        await db.execute(
            select(ResourceNode.id).where(ResourceNode.id == payload.resource_node_id)
        )
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Resource node not found")

    chunk_size = payload.chunk_size or _DEFAULT_CHUNK_SIZE
    total_chunks = max(1, math.ceil(payload.total_size / chunk_size))
    resume_token = _generate_resume_token()

    # Validate classification level exists.
    cl = (
        await db.execute(
            select(ClassificationLevel.id).where(
                ClassificationLevel.id == payload.classification_level_id
            )
        )
    ).scalar_one_or_none()
    if cl is None:
        raise HTTPException(status_code=404, detail="Classification level not found")

    session = UploadSession(
        resource_node_id=payload.resource_node_id,
        file_name=payload.file_name,
        total_size=payload.total_size,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        uploaded_chunks=0,
        resume_token=resume_token,
        sha256_full=payload.sha256_full,
        status="initiated",
        classification_level=payload.classification_level_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return UploadInitResponse(
        session_id=session.id,
        resume_token=resume_token,
        chunk_size=chunk_size,
        total_chunks=total_chunks,
        status=session.status,
    )


async def _find_session_by_token(
    db: AsyncSession,
    resume_token: str,
) -> UploadSession:
    """Look up an UploadSession by resume_token; raise 404 if not found."""
    result = (
        await db.execute(
            select(UploadSession).where(UploadSession.resume_token == resume_token)
        )
    ).scalar_one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return result


async def upload_chunk(
    db: AsyncSession,
    resume_token: str,
    chunk_index: int,
    sha256_chunk: str,
    chunk_data: bytes,
    chunk_root: Path | None = None,
) -> dict:
    """Accept a single chunk, verify its SHA-256, and persist to disk."""
    session = await _find_session_by_token(db, resume_token)

    if session.status not in ("initiated", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=f"Session is {session.status}; cannot accept chunks",
        )

    if chunk_index < 0 or chunk_index >= session.total_chunks:
        raise HTTPException(
            status_code=400,
            detail=f"chunk_index {chunk_index} out of range [0, {session.total_chunks})",
        )

    actual_hash = _compute_sha256(chunk_data)
    if actual_hash != sha256_chunk:
        raise HTTPException(
            status_code=400,
            detail=f"Chunk {chunk_index} SHA256 mismatch",
        )

    root = chunk_root or _resolve_chunk_root()
    cdir = _chunk_dir_for(root, session.id)
    (cdir / str(chunk_index)).write_bytes(chunk_data)

    uploaded = _count_existing_chunks(cdir, session.total_chunks)
    session.uploaded_chunks = uploaded
    if session.status == "initiated":
        session.status = "in_progress"
    await db.commit()
    await db.refresh(session)

    return {
        "session_id": session.id,
        "chunk_index": chunk_index,
        "uploaded_chunks": uploaded,
        "total_chunks": session.total_chunks,
        "status": session.status,
    }


async def complete_upload(
    db: AsyncSession,
    resume_token: str,
    chunk_root: Path | None = None,
) -> UploadCompleteResponse:
    """Assemble all chunks, verify full-file SHA256, and finalise the session."""
    session = await _find_session_by_token(db, resume_token)

    if session.status not in ("initiated", "in_progress"):
        return UploadCompleteResponse(
            session_id=session.id,
            status=session.status,
            error=f"Session already {session.status}",
        )

    root = chunk_root or _resolve_chunk_root()
    cdir = _chunk_dir_for(root, session.id)

    missing = _list_missing_chunks(cdir, session.total_chunks)
    if missing:
        return UploadCompleteResponse(
            session_id=session.id,
            status="in_progress",
            error=f"Missing chunks: {missing}",
        )

    assembled = _assemble_and_hash(cdir, session.total_chunks)
    actual_sha256 = _compute_sha256(assembled)

    if session.sha256_full and actual_sha256 != session.sha256_full:
        session.status = "failed"
        await db.commit()
        await db.refresh(session)
        return UploadCompleteResponse(
            session_id=session.id,
            status="failed",
            error=f"SHA256 mismatch: expected {session.sha256_full}, got {actual_sha256}",
        )

    session.sha256_full = actual_sha256
    session.uploaded_chunks = session.total_chunks
    session.status = "completed"
    await db.commit()
    await db.refresh(session)

    return UploadCompleteResponse(
        session_id=session.id,
        status="completed",
        sha256_full=actual_sha256,
    )


async def resume_upload(
    db: AsyncSession,
    resume_token: str,
    chunk_root: Path | None = None,
) -> UploadResumeResponse:
    """Report the current state of an upload session and missing chunk indices."""
    session = await _find_session_by_token(db, resume_token)

    if session.status in ("completed", "failed"):
        return UploadResumeResponse(
            session_id=session.id,
            status=session.status,
            total_chunks=session.total_chunks,
            uploaded_chunks=session.uploaded_chunks,
            missing_chunks=[],
        )

    root = chunk_root or _resolve_chunk_root()
    cdir = _chunk_dir_for(root, session.id)

    missing = _list_missing_chunks(cdir, session.total_chunks)
    uploaded = session.total_chunks - len(missing)

    return UploadResumeResponse(
        session_id=session.id,
        status=session.status,
        total_chunks=session.total_chunks,
        uploaded_chunks=uploaded,
        missing_chunks=missing,
    )


__all__ = [
    "complete_upload",
    "init_upload",
    "resume_upload",
    "set_chunk_storage_root",
    "upload_chunk",
]
