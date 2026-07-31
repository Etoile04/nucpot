"""Health check and alert endpoints.

- ``GET /health`` — liveness probe for load balancers.
- ``GET /health/alerts`` — query structured silent-failure events (NFM-2222).
- ``GET /health/alerts/summary`` — aggregated event counts (NFM-2222).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.middleware.rate_limit import limiter
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.health import AlertsResponse, SummaryResponse
from nfm_db.services.health_alert_service import get_alerts, get_alerts_summary

router = APIRouter(tags=["健康检查"])


@router.get("/health", summary="健康检查", description="返回API服务健康状态，用于负载均衡探针和监控告警。\n\nReturns API health status for load balancer probes and monitoring alerts.")
@limiter.exempt
async def health_check() -> dict:
    """返回API服务健康状态.

    Includes worker consecutive-failure counter (NFM-2014)."""
    from monitoring.worker_health import worker_health

    return worker_health.snapshot()


@router.get(
    "/health/alerts",
    response_model=ApiResponse[AlertsResponse],
    summary="健康告警列表",
    description="返回最近的静默失败健康事件，支持按类型、严重程度、来源服务和时间过滤。\n\nReturns recent silent-failure health events with optional filters.",
)
@limiter.exempt
async def list_health_alerts(
    event_type: str | None = Query(None, description="Filter by event type"),
    severity: str | None = Query(None, description="Filter by severity"),
    source_service: str | None = Query(None, description="Filter by source service"),
    since: datetime | None = Query(None, description="Lower bound for created_at (ISO 8601)"),
    limit: int = Query(50, ge=1, le=500, description="Max events to return (default 50, max 500)"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AlertsResponse]:
    """Return recent health events with optional filters.

    No authentication required (consistent with other health endpoints).
    Defaults to the last 24 hours when ``since`` is omitted.
    """
    result = await get_alerts(
        db,
        event_type=event_type,
        severity=severity,
        source_service=source_service,
        since=since,
        limit=limit,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/health/alerts/summary",
    response_model=ApiResponse[SummaryResponse],
    summary="健康告警汇总",
    description="返回最近24小时内健康事件的聚合统计。\n\nReturns aggregated health event counts for the last 24 hours.",
)
@limiter.exempt
async def health_alerts_summary(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SummaryResponse]:
    """Return aggregated event counts by type and severity.

    No authentication required (consistent with other health endpoints).
    """
    result = await get_alerts_summary(db)
    return ApiResponse(success=True, data=result)
