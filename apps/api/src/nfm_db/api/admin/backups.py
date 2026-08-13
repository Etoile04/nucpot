"""Admin backup monitoring endpoints — NFM-3017 / NFM-3070.

- ``GET /backups`` — list backup snapshots with per-snapshot tier field.
- ``GET /backups/stats`` — disk/capacity metrics (totalBytes, freeBytes,
  refusalCount, lastRefusalAt).

Both endpoints require ``BlogRole.ADMIN``.  Path parameters
(``backup_dir``, ``refusal_file``) are validated against the
``NFM_BACKUP_DIR_ROOTS`` allowlist from
:class:`nfm_db.config.Settings` using
:func:`nfm_db.core.path_safety.safe_resolve`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from nfm_db.api.v1.auth import require_blog_role
from nfm_db.config import get_settings
from nfm_db.core.path_safety import PathNotAllowedError, safe_resolve
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import BlogRole, User
from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupStatsResponse,
)
from nfm_db.schemas.common import ApiResponse
from nfm_db.services.backup_service import get_backup_stats, list_snapshots

logger = logging.getLogger(__name__)

router = APIRouter(tags=["备份管理"])

# Default backup directory — used as the Query default and as the
# allowlist fallback when ``NFM_BACKUP_DIR_ROOTS`` is not set.
_DEFAULT_BACKUP_DIR = "/var/backups/nucpot"


def _validate_backup_dir(backup_dir: str) -> Path:
    """Validate that *backup_dir* resolves inside an allowed root.

    Iterates over ``settings.backup_dir_roots`` and uses
    :func:`safe_resolve` against each.  The first match wins, so
    multiple roots can be configured.

    Args:
        backup_dir: Caller-supplied path string.

    Returns:
        The resolved :class:`Path`.

    Raises:
        HTTPException: 400 if the path escapes all allowed roots.
    """
    settings = get_settings()
    roots = settings.backup_dir_roots or [_DEFAULT_BACKUP_DIR]

    for root in roots:
        try:
            return safe_resolve(backup_dir, root)
        except PathNotAllowedError:
            continue

    logger.warning(
        "Rejected backup_dir %r — not inside any allowed root",
        backup_dir,
    )
    raise HTTPException(
        status_code=400,
        detail="Requested path is not inside the configured backup directory.",
    )


def _validate_refusal_file(
    refusal_file: str,
    backup_dir: Path,
) -> Path:
    """Validate that *refusal_file* resolves inside *backup_dir*.

    The refusal sidecar must be a child of the backup directory
    (or one of the configured backup roots).  This prevents using
    the endpoint as a generic file-read oracle.

    Args:
        refusal_file: Caller-supplied path string.
        backup_dir: Already-validated backup directory path.

    Returns:
        The resolved :class:`Path`.

    Raises:
        HTTPException: 400 if the path escapes all allowed roots.
    """
    settings = get_settings()
    roots = settings.backup_dir_roots or [_DEFAULT_BACKUP_DIR]

    for root in roots:
        try:
            return safe_resolve(refusal_file, root)
        except PathNotAllowedError:
            continue

    logger.warning(
        "Rejected refusal_file %r — not inside any allowed root",
        refusal_file,
    )
    raise HTTPException(
        status_code=400,
        detail="Requested path is not inside the configured backup directory.",
    )


@router.get(
    "/backups",
    response_model=ApiResponse[BackupListResponse],
    summary="列出备份快照",
    description=(
        "扫描备份目录，返回所有备份快照的元数据列表，"
        "每个快照包含 GFS 保留层级 (hourly | daily | weekly)。\n\n"
        "Scans the backup directory and returns metadata for every snapshot, "
        "including its GFS retention tier. Admin-only."
    ),
)
@limiter.exempt
async def get_backups(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    backup_dir: str = Query(
        default=_DEFAULT_BACKUP_DIR,
        description="Path to the backup directory",
    ),
    extension: str = Query(
        default=".sql.gz",
        description="File extension to filter snapshots",
    ),
) -> ApiResponse[BackupListResponse]:
    """List backup snapshots with tier metadata.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        backup_dir: Path to scan for backup files (validated against
            ``NFM_BACKUP_DIR_ROOTS``).
        extension: File extension filter.
    """
    validated = _validate_backup_dir(backup_dir)
    result = list_snapshots(str(validated), extension=extension)
    return ApiResponse(success=True, data=result)


@router.get(
    "/backups/stats",
    response_model=ApiResponse[BackupStatsResponse],
    summary="备份容量统计",
    description=(
        "返回备份子系统的实时磁盘和容量指标："
        "totalBytes、freeBytes、refusalCount、lastRefusalAt。\n\n"
        "Returns real-time disk and capacity metrics for the backup subsystem. "
        "Admin-only."
    ),
)
@limiter.exempt
async def get_backup_stats_endpoint(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    backup_dir: str = Query(
        default=_DEFAULT_BACKUP_DIR,
        description="Path to the backup directory",
    ),
    refusal_file: str | None = Query(
        default=None,
        description="Path to the refusal JSON sidecar file",
    ),
) -> ApiResponse[BackupStatsResponse]:
    """Return backup disk and refusal metrics.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        backup_dir: Directory whose volume is measured (validated
            against ``NFM_BACKUP_DIR_ROOTS``).
        refusal_file: Path to refusal counter JSON file (validated
            against the same allowlist).
    """
    validated_dir = _validate_backup_dir(backup_dir)

    validated_refusal: str | None = None
    if refusal_file is not None:
        validated_refusal = str(_validate_refusal_file(refusal_file, validated_dir))

    result = get_backup_stats(str(validated_dir), refusal_file=validated_refusal)
    return ApiResponse(success=True, data=result)
