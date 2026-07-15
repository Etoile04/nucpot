"""Reference gaps API endpoints: list, summary, fill, scan.

Per NFM-54 design Sections 2.1-2.3.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import get_db
from nfm_db.models.user import User
from nfm_db.schemas.common import PaginationParams
from nfm_db.schemas.reference_gaps import (
    FillRequest,
    FillResponse,
    FillResultItem,
    ReferenceGapItem,
    ReferenceGapsApiResponse,
    ReferenceGapsListResponse,
    ReferenceGapsSummaryResponse,
    ScanRequest,
    ScanResponse,
    ScanResultItem,
    SystemCoverageBreakdown,
)
from nfm_db.services.gap_fill_service import GapFillService
from nfm_db.services.gap_scan_service import GapScanService

router = APIRouter(tags=["参考缺口管理"])


@router.get("/reference-gaps", response_model=ReferenceGapsApiResponse)
async def list_reference_gaps(
    element_system: str | None = Query(default=None, max_length=50),
    phase: str | None = Query(default=None, max_length=50),
    property_name: str | None = Query(default=None, alias="property", max_length=100),
    sort_by: str = Query(default="priority", pattern=r"^(priority|element_system)$"),
    pagination: PaginationParams = Depends(PaginationParams),
    session: AsyncSession = Depends(get_db),
) -> ReferenceGapsApiResponse:
    """List reference data gaps with filtering and pagination.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    svc = GapScanService(session)
    gaps, total = await svc.list_gaps(
        element_system=element_system,
        phase=phase,
        property_name=property_name,
        sort_by=sort_by,
        page=pagination.page,
        per_page=pagination.per_page,
    )

    gap_items = [
        ReferenceGapItem(
            element_system=g.element_system,
            phase=g.phase,
            property_name=g.property_name,
            priority=g.priority,
        )
        for g in gaps
    ]

    return ReferenceGapsApiResponse(
        success=True,
        data=ReferenceGapsListResponse(
            gaps=gap_items,
            total=total,
            page=pagination.page,
            per_page=pagination.per_page,
        ),
    )


@router.get("/reference-gaps/summary", response_model=ReferenceGapsApiResponse)
async def get_reference_gaps_summary(
    session: AsyncSession = Depends(get_db),
) -> ReferenceGapsApiResponse:
    """获取参考数据覆盖率统计。

    Get coverage statistics for reference data gaps."""
    svc = GapScanService(session)
    scan = await svc.scan_gaps()
    staging_counts = await svc._get_staging_counts()

    by_system = [
        SystemCoverageBreakdown(
            element_system=s.element_system,
            phase=s.phase,
            total=s.total,
            covered=s.covered,
            gaps=s.gaps,
        )
        for s in scan.system_breakdown
    ]

    return ReferenceGapsApiResponse(
        success=True,
        data=ReferenceGapsSummaryResponse(
            total_target_tuples=scan.stats.total_target_tuples,
            covered=scan.stats.covered,
            gaps=scan.stats.gaps,
            coverage_percent=scan.stats.coverage_percent,
            by_system=by_system,
            staging_pending=staging_counts.pending,
            staging_approved=staging_counts.approved,
        ),
    )


@router.post("/reference-gaps/fill", response_model=ReferenceGapsApiResponse, status_code=202)
async def fill_reference_gaps(
    payload: FillRequest,
    current_user: Annotated[User, Depends(require_editor)],
    session: AsyncSession = Depends(get_db),
) -> ReferenceGapsApiResponse:
    """触发特定缺口的填补操作。

    Trigger a fill operation for a specific gap tuple.

    Discovers reference values from cache, runs quality gate, and stages
    accepted values into the staging table.
    """
    svc = GapFillService(session)
    result = await svc.fill_gap(
        element_system=payload.element_system,
        phase=payload.phase,
        property_name=payload.property_name,
        cache_levels=payload.cache_levels,
        dry_run=payload.dry_run,
    )

    result_items = [
        FillResultItem(
            element_system=item.element_system,
            phase=item.phase,
            property_name=item.property_name,
            status=item.status,
            confidence=item.confidence,
            source=item.source,
        )
        for item in result.items
    ]

    return ReferenceGapsApiResponse(
        success=True,
        data=FillResponse(
            batch_id=result.batch_id,
            gaps_targeted=result.gaps_targeted,
            values_found=result.values_found,
            staged=result.staged,
            duplicates=result.duplicates,
            results=result_items,
        ),
    )


@router.post("/reference-gaps/scan", response_model=ReferenceGapsApiResponse)
async def scan_reference_gaps(
    current_user: Annotated[User, Depends(require_editor)],
    payload: ScanRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> ReferenceGapsApiResponse:
    """手动触发NFMD数据库缺口扫描。

    Trigger a manual gap scan against the NFMD database.

    Identifies all missing property tuples for the specified (or all)
    element systems.
    """
    svc = GapScanService(session)
    element_systems = payload.element_systems if payload else None
    scan = await svc.scan_gaps(element_systems=element_systems)

    # Group scan results by element_system for the response
    system_gaps: dict[str, int] = {}
    for gap in scan.gaps:
        key = gap.element_system
        system_gaps[key] = system_gaps.get(key, 0) + 1

    result_items = [
        ScanResultItem(
            element_system=es,
            phase=None,
            gaps_found=system_gaps.get(es, 0),
            properties_scanned=len([g for g in scan.gaps if g.element_system == es]),
        )
        for es in sorted(system_gaps)
    ]

    return ReferenceGapsApiResponse(
        success=True,
        data=ScanResponse(
            total_gaps_found=scan.stats.gaps,
            systems_scanned=len(set(g.element_system for g in scan.gaps)),
            results=result_items,
        ),
    )
