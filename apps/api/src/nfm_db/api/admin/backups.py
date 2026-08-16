"""Admin backup monitoring endpoints (NFM-3024-D / NFM-3052 / NFM-3065 / NFM-3070).

- ``GET /api/admin/backups`` — list backup snapshots with per-snapshot
  ``tier`` field (``hourly`` | ``daily`` | ``weekly`` | ``null`` for
  pre-migration).
- ``GET /api/admin/backups/stats`` — disk/capacity metrics plus refusal
  counter (``total_bytes``, ``free_bytes``, ``refusal_count``,
  ``last_refusal_at``). Cached via a 1-second module-level TTL.

Both endpoints require ``BlogRole.ADMIN``. Both return ``404`` when the
backup subsystem is disabled (``NFM_BACKUP_ENABLED=false``).  The stats
endpoint returns ``503`` when the disk-stat call is unavailable.

Path parameters (``backup_dir``) are validated against the
``NFM_BACKUP_DIR_ROOTS`` allowlist from :class:`nfm_db.config.Settings`
using :func:`nfm_db.core.path_safety.safe_resolve` (NFM-3070).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_blog_role
from nfm_db.config import get_settings
from nfm_db.core.path_safety import PathNotAllowedError, safe_resolve
from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.models.user import BlogRole, User
from nfm_db.schemas.backup import (
    BackupListResponse,
    BackupStatsResponse,
)
from nfm_db.schemas.common import ApiResponse
from nfm_db.services import backup_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["备份管理"])


def _ensure_backup_enabled() -> None:
    """Raise 404 when the operator has disabled the backup subsystem."""
    if not backup_service.is_backup_enabled():
        raise HTTPException(status_code=404, detail="Backup subsystem disabled")


def _resolve_backup_dir(override: str | None) -> Path:
    """Canonicalize and validate a backup directory path (NFM-3070).

    Iterates over ``settings.backup_dir_roots`` and uses
    :func:`safe_resolve` against each.  The first match wins, so
    multiple roots can be configured.  Falls back to
    ``backup_service.BACKUP_DIR`` when the override is ``None`` and
    the root list is empty.

    Raises:
        HTTPException: 400 if the path escapes all allowed roots.
    """
    settings = get_settings()
    roots = settings.backup_dir_roots or [str(backup_service.BACKUP_DIR)]
    target = override if override else str(backup_service.BACKUP_DIR)

    for root in roots:
        try:
            return safe_resolve(target, root)
        except PathNotAllowedError:
            continue

    logger.warning(
        "Rejected backup_dir %r — not inside any allowed root",
        target,
    )
    raise HTTPException(
        status_code=400,
        detail="Requested path is not inside the configured backup directory.",
    )


@router.get(
    "/backups",
    response_model=ApiResponse[BackupListResponse],
    response_model_by_alias=True,
    summary="列出备份快照",
    description=(
        "扫描备份目录, 返回所有备份快照的元数据列表,"
        "每个快照包含 GFS 保留层级 (``hourly`` | ``daily`` | ``weekly`` | ``null``)。\n\n"
        "Scans the backup directory and returns metadata for every snapshot, "
        "including its GFS retention tier (``null`` for pre-migration). "
        "Admin-only."
    ),
)
@limiter.exempt  # type: ignore[untyped-decorator]
async def get_backups(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    _db: Annotated[AsyncSession, Depends(get_db)],
    backup_dir: str = Query(
        default=None,
        description="Override the backup directory (defaults to BACKUP_DIR).",
    ),
    extension: str = Query(
        default=".sql.gz",
        description="File extension to filter snapshots.",
    ),
) -> ApiResponse[BackupListResponse]:
    """List backup snapshots with tier metadata.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        _db: Async database session (unused — data comes from filesystem).
        backup_dir: Optional directory override.
        extension: File extension filter.
    """
    _ensure_backup_enabled()
    target = _resolve_backup_dir(backup_dir)
    result = backup_service.list_snapshots(target, extension=extension)
    return ApiResponse(success=True, data=result)


@router.get(
    "/backups/stats",
    response_model=ApiResponse[BackupStatsResponse],
    response_model_by_alias=True,
    summary="备份容量统计",
    description=(
        "返回备份子系统的实时磁盘和容量指标:"
        "``totalBytes``、``freeBytes``、``refusalCount``、``lastRefusalAt``。\n\n"
        "Returns real-time disk and capacity metrics for the backup "
        "subsystem.  Cached for 1 second.  Admin-only."
    ),
)
@limiter.exempt  # type: ignore[untyped-decorator]
async def get_backup_stats_endpoint(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    _db: Annotated[AsyncSession, Depends(get_db)],
    backup_dir: str = Query(
        default=None,
        description="Override the backup directory (defaults to BACKUP_DIR).",
    ),
) -> ApiResponse[BackupStatsResponse]:
    """Return backup disk and refusal metrics.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        _db: Async database session (unused — data comes from filesystem).
        backup_dir: Optional directory override.
    """
    _ensure_backup_enabled()
    target = _resolve_backup_dir(backup_dir)
    try:
        result = backup_service.get_backup_stats(target)
    except OSError:
        # Both the requested path and the root volume failed to report.
        raise HTTPException(
            status_code=503,
            detail="Disk statistics unavailable for the backup subsystem",
        )
    return ApiResponse(success=True, data=result)
