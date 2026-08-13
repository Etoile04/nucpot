"""Admin backup monitoring endpoints (NFM-3024-D / NFM-3052 / NFM-3065).

- ``GET /api/admin/backups`` — list backup snapshots with per-snapshot
  ``tier`` field (``hourly`` | ``daily`` | ``weekly`` | ``null`` for
  pre-migration).
- ``GET /api/admin/backups/stats`` — disk/capacity metrics plus refusal
  counter (``total_bytes``, ``free_bytes``, ``refusal_count``,
  ``last_refusal_at``). Cached via a 1-second module-level TTL.

Both endpoints require ``BlogRole.ADMIN``. Both return ``404`` when the
backup subsystem is disabled (``NFM_BACKUP_ENABLED=false``).  The stats
endpoint returns ``503`` when the disk-stat call is unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_blog_role
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
    """Canonicalize and validate a backup directory path.

    Rejects paths that resolve outside ``backup_service.BACKUP_DIR`` to
    prevent admin-only directory traversal (CR review finding).  The
    allowed root is read dynamically from the service module so that
    test monkeypatches take effect without patching this module too.
    """
    target = Path(override) if override else backup_service.BACKUP_DIR
    try:
        resolved = target.resolve(strict=False)
    except (OSError, ValueError) as exc:
        logger.warning("Invalid backup_dir path %r: %s", override, exc)
        raise HTTPException(status_code=400, detail="Invalid backup_dir path") from exc

    allowed = backup_service.BACKUP_DIR.resolve(strict=False)
    try:
        resolved.relative_to(allowed)
    except ValueError:
        logger.warning(
            "backup_dir %s resolved outside allowed root %s — rejected",
            resolved,
            allowed,
        )
        raise HTTPException(
            status_code=403,
            detail="backup_dir must resolve inside the configured backup root",
        )
    return resolved


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
@limiter.exempt
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
@limiter.exempt
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
