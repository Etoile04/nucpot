"""Write-side service for potential uploads (NFM-299).

Ports validation logic verbatim from the legacy Supabase prior-art:
  - .worktrees/phase4-infra/src/app/api/potentials/upload/route.ts
  - .worktrees/phase4-infra/src/app/api/potentials/upload-file/route.ts
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Potential
from nfm_db.schemas.potential import VALID_LICENSE_TYPES, PotentialCreateRequest

logger = logging.getLogger(__name__)

# ── file validation (ported verbatim) ─────────────────────────────────────────

ALLOWED_EXTENSIONS = (
    ".eam.alloy",
    ".eam.fs",
    ".eam",
    ".setfl",
    ".meam",
    ".param",
    ".table",
    ".mtp",
    ".snap",
    ".json",
    ".txt",
    ".zip",
    ".tar.gz",
    ".gz",
    ".reaxff",
    ".tersoff",
    ".sw",
    ".bop",
    ".comb",
    ".lj",
    ".dp",
)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _validate_extension(filename: str) -> None:
    """Raise :class:`PotentialUploadError` if *filename* has an unsupported extension."""
    lower = filename.lower()
    for allowed in ALLOWED_EXTENSIONS:
        if lower.endswith(allowed):
            return
    raise PotentialUploadError(
        f"Unsupported file extension. Acceptable: {', '.join(ALLOWED_EXTENSIONS)}",
    )


def _validate_size(size: int) -> None:
    """Raise :class:`PotentialUploadError` if *size* exceeds MAX_FILE_SIZE."""
    if size > MAX_FILE_SIZE:
        mb = size / 1024 / 1024
        raise PotentialUploadError(f"File too large: {mb:.1f}MB. Maximum: 50MB")


def _sanitize_filename(name: str) -> str:
    """Replace characters that are not alphanumeric, dot, dash, or underscore."""
    import re

    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


def _compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── upload directory ──────────────────────────────────────────────────────────

# Override point for tests.  Production code calls `get_upload_dir()` which
# resolves a default path unless this global is set.
_UPLOAD_DIR_OVERRIDE: Path | None = None


def _default_upload_dir() -> Path:
    """Resolve the default upload directory: <repo-root>/apps/web/public/uploads."""
    this_file = Path(__file__).resolve()  # .../apps/api/src/nfm_db/services/upload_service.py
    # Walk up to the repo root (5 levels above this file).
    repo_root = this_file.parents[
        5
    ]  # [0]=services, [1]=nfm_db, [2]=src, [3]=api, [4]=apps, [5]=root
    return repo_root / "apps" / "web" / "public" / "uploads"


def get_upload_dir() -> Path:
    """Return the upload storage directory (dependency override-able for testing)."""
    if _UPLOAD_DIR_OVERRIDE is not None:
        return _UPLOAD_DIR_OVERRIDE
    return _default_upload_dir()


# ── domain exceptions ─────────────────────────────────────────────────────────


class PotentialUploadError(Exception):
    """Raised for user-correctable upload problems (translated to HTTP in the endpoint)."""

    status_code: int = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class PotentialNameConflictError(PotentialUploadError):
    """Name-uniqueness violation → 409 Conflict."""

    status_code = 409


class PotentialNotFoundError(PotentialUploadError):
    """Potential row missing → 404."""

    status_code = 404


# ── business logic ────────────────────────────────────────────────────────────


def _validate_metadata(payload: PotentialCreateRequest) -> None:
    """Enforce required fields + license-authorization rules (prior-art verbatim)."""
    # Required fields (pydantic enforces these structurally; duplicate for clarity).
    if (
        not payload.name
        or not payload.type
        or not payload.elements
        or not payload.system_name
        or not payload.description
    ):
        raise PotentialUploadError(
            "name, type, elements, system_name, and description are required",
        )

    # License type membership
    if not payload.license_type or payload.license_type not in VALID_LICENSE_TYPES:
        raise PotentialUploadError(
            f"license_type is required ({', '.join(VALID_LICENSE_TYPES)})",
        )

    # License authorization (cross-field, verbatim from prior-art)
    if payload.license_type == "author_permission" and not payload.auth_file_path:
        raise PotentialUploadError(
            "Authorization proof file is required when license_type is author_permission",
        )
    if payload.license_type == "open_license" and not payload.license_detail:
        raise PotentialUploadError(
            "License name (e.g. CC-BY-4.0) is required when license_type is open_license",
        )


async def create_potential(db: AsyncSession, payload: PotentialCreateRequest) -> Potential:
    """Validate metadata and insert a potential row.

    Raises:
        PotentialUploadError: validation failure (400).
        PotentialNameConflict: name already exists (409).
    """
    _validate_metadata(payload)

    # Name uniqueness (exact match per prior-art)
    existing = (
        await db.execute(select(Potential.id).where(Potential.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise PotentialNameConflictError("Potential name already exists")

    extra = dict(payload.extra)
    extra.setdefault("status", "pending")
    extra["verification_status"] = "unverified"  # WS2 seam (ADR-2)
    extra["uploaded_by"] = payload.uploaded_by or "trusted-submitter"
    extra["license_type"] = payload.license_type
    if payload.license_detail:
        extra["license_detail"] = payload.license_detail
    if payload.auth_file_path:
        extra["auth_file_path"] = payload.auth_file_path

    potential = Potential(
        name=payload.name,
        display_name=payload.display_name or payload.name,
        type=payload.type,
        subtype=payload.subtype,
        format=payload.format or "LAMMPS",
        elements=payload.elements,
        system_name=payload.system_name,
        system_tags=payload.system_tags,
        description=payload.description,
        applicability=payload.applicability,
        references=payload.references,
        developers=payload.developers,
        verified_props={},
        sim_software=["LAMMPS"],
        lammps_config=payload.lammps_config,
        source="user_contributed",
        tags=payload.tags,
        extra=extra,
        status="pending",
    )
    db.add(potential)
    await db.commit()
    await db.refresh(potential)
    return potential


async def attach_potential_file(
    db: AsyncSession,
    upload_dir: Path,
    potential_id: UUID,
    filename: str,
    data: bytes,
) -> dict:
    """Validate file, write to disk, update DB row with file_url/file_hash/file_size.

    Returns a dict suitable for :class:`FileUploadResponse`.

    Raises:
        PotentialUploadError: extension or size validation (400).
        PotentialNotFoundError: potential_id doesn't exist (404).
    """
    _validate_extension(filename)
    _validate_size(len(data))

    # Verify potential exists
    potential = (
        await db.execute(select(Potential).where(Potential.id == potential_id))
    ).scalar_one_or_none()
    if potential is None:
        raise PotentialNotFoundError(f"Potential {potential_id} not found")

    # Write file
    sanitized = _sanitize_filename(filename)
    dest_dir = upload_dir / str(potential_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / sanitized
    dest_path.write_bytes(data)

    file_hash = _compute_hash(data)
    file_url = f"/uploads/{potential_id}/{sanitized}"
    file_size = len(data)

    # Update row via service layer (avoid extra round-trip)
    potential.file_url = file_url
    potential.file_hash = file_hash
    potential.file_size = file_size
    await db.commit()

    return {
        "file_name": filename,
        "file_url": file_url,
        "file_hash": file_hash,
        "file_size": file_size,
    }
