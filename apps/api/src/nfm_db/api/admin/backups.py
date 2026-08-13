"""Admin backup monitoring endpoints — NFM-3044.

- ``GET /api/admin/backups`` — list backup snapshots with per-snapshot
  ``tier`` field (hourly | daily | weekly).
- ``GET /api/admin/backups/stats`` — disk/capacity metrics
  (``totalBytes``, ``freeBytes``, ``refusalCount``, ``lastRefusalAt``)
  plus a per-tier ``{count, bytes}`` breakdown.

Both endpoints require ``BlogRole.ADMIN``. The restore path is owned by a
separate router and is intentionally NOT modified here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
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
from nfm_db.services.backup_service import (
    get_backup_stats,
    list_snapshots,
)

router = APIRouter(tags=["备份管理"])


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
    _db: Annotated[AsyncSession, Depends(get_db)],
    backup_dir: str = Query(
        default="/var/backups/nucpot",
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
        _db: Async database session (unused — data comes from filesystem).
        backup_dir: Path to scan for backup files.
        extension: File extension filter.
    """
    result = list_snapshots(backup_dir, extension=extension)
    return ApiResponse(success=True, data=result)


@router.get(
    "/backups/stats",
    response_model=ApiResponse[BackupStatsResponse],
    summary="备份容量统计",
    description=(
        "返回备份子系统的实时磁盘和容量指标："
        "totalBytes、freeBytes、refusalCount、lastRefusalAt，"
        "以及按 tier 拆分的 {count, bytes} 统计。\n\n"
        "Returns real-time disk and capacity metrics for the backup subsystem "
        "(totalBytes, freeBytes, refusalCount, lastRefusalAt) plus a per-tier "
        "{count, bytes} breakdown. Admin-only."
    ),
)
@limiter.exempt
async def get_backup_stats_endpoint(
    _user: Annotated[User, Depends(require_blog_role(BlogRole.ADMIN))],
    _db: Annotated[AsyncSession, Depends(get_db)],
    backup_dir: str = Query(
        default="/var/backups/nucpot",
        description="Path to the backup directory",
    ),
    refusal_file: str | None = Query(
        default=None,
        description="Path to the refusal JSON sidecar file",
    ),
) -> ApiResponse[BackupStatsResponse]:
    """Return backup disk, refusal, and per-tier metrics.

    Args:
        _user: Authenticated admin (dependency enforces 401/403).
        _db: Async database session (unused — data comes from filesystem).
        backup_dir: Directory whose volume is measured.
        refusal_file: Path to refusal counter JSON file.
    """
    result = get_backup_stats(backup_dir, refusal_file=refusal_file)
    return ApiResponse(success=True, data=result)
