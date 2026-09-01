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
from nfm_db.monitoring.worker_health import worker_health
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.health import AlertsResponse, SummaryResponse
from nfm_db.services.health_alert_service import (
    count_recent_uuid_titled_source_blocks,
    get_alerts,
    get_alerts_summary,
)

router = APIRouter(tags=["健康检查"])


@router.get(
    "/health",
    summary="健康检查",
    description="返回API服务健康状态，用于负载均衡探针和监控告警。\n\nReturns API health status for load balancer probes and monitoring alerts.",
)
@limiter.exempt
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """返回API服务健康状态.

    Composes two signals into a single ``status`` field:

    1. **Worker consecutive-failure counter** (NFM-2014) — fast,
       in-process; flips to ``degraded`` when consecutive failures
       cross the threshold.
    2. **F4 UUID-title guard (NFM-4097 AC-4)** — DB query that
       counts ``health_events`` rows with
       ``event_type='uuid_titled_source_blocked'`` in the last 24
       hours.  Any non-zero count flips the status to ``degraded``
       so the load balancer pulls the API out of rotation and
       PagerDuty wakes the on-call.

    The worker snapshot is the source of truth for the other
    fields (``consecutive_failures`` / ``last_success_at`` /
    ``last_error``); this endpoint overlays the DB-derived
    ``status`` flip without losing those fields so monitoring
    agents that already consume them do not break.
    """
    snapshot = worker_health.snapshot()
    recent_uuid_block_count = await count_recent_uuid_titled_source_blocks(db)
    if recent_uuid_block_count > 0 and snapshot.get("status") == "ok":
        # Only override when the worker is otherwise ok — if the
        # worker already reported degraded, keep its status so the
        # operator sees the more informative failure mode.
        snapshot["status"] = "degraded"
    snapshot["recent_uuid_titled_source_blocks"] = recent_uuid_block_count
    return snapshot


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
