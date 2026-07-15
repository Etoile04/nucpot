"""Reference values API endpoints: bulk staging, review queue, approve/reject,
bulk export for verification, and verification callback.

Per NFM-54 design Sections 2.2-2.3 and NFM-66:
- POST /api/v1/reference-values/bulk — Bulk write to staging
- GET  /api/v1/reference-values/pending-review — Review queue
- POST /api/v1/reference-values/{id}/approve — Approve + promote
- POST /api/v1/reference-values/{id}/reject — Reject
- POST /api/v1/reference-values/export — Bulk export for verification
- POST /api/v1/reference-values/verify-callback — Verification results callback
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)
from nfm_db.schemas.common import PaginationParams
from nfm_db.schemas.reference_values import (
    BulkStagingItemResult,
    BulkStagingRequest,
    BulkStagingResponse,
    PendingReviewResponse,
    ReviewRequest,
    ReviewResponse,
    StagingRecordResponse,
)
from nfm_db.schemas.verification import (
    ExportedRecord,
    ExportRequest,
    ExportResponse,
    VerificationCallbackItem,
    VerificationCallbackRequest,
    VerificationCallbackResponse,
)
from nfm_db.services.promotion_service import (
    InvalidTransitionError,
    StagingRecordNotFoundError,
    approve_staging_record,
    reject_staging_record,
)
from nfm_db.services.quality_gate import QualityGateService
from nfm_db.services.verification_service import (
    export_for_verification,
    process_verification_results,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["参考值管理"])


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/bulk
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/bulk",
    response_model=dict,
    status_code=201,
    summary="批量写入参考值",
    description="批量写入参考值到暂存区，执行质量门控（去重、范围检查、置信度路由）后写入暂存表。\n\nBulk write reference values to staging with quality gate (dedup, range check, confidence routing).",
)
async def bulk_stage_reference_values(
    payload: BulkStagingRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """批量写入参考值到暂存区。

    Bulk write reference values to staging.

    Accepts an array of reference_value dicts, runs quality gate on each
    (dedup, range check, confidence routing), and stages accepted values.

    Returns accepted/rejected counts with per-item status.
    """
    gate = QualityGateService(session)
    raw_values = [item.model_dump(by_alias=True) for item in payload.values]

    bulk_result = await gate.process_bulk(raw_values)

    results: list[BulkStagingItemResult] = []
    for gate_result in bulk_result.accepted:
        matching_raw = _find_matching_raw(raw_values, gate_result.dedup_hash)
        if matching_raw is not None:
            record = await gate.stage_record(matching_raw, gate_result)
            results.append(
                BulkStagingItemResult(
                    staging_id=record.id,
                    status=gate_result.decision.value,
                    confidence=gate_result.confidence,
                )
            )

    for gate_result in bulk_result.duplicates:
        results.append(
            BulkStagingItemResult(
                status=gate_result.decision.value,
                confidence=gate_result.confidence,
            )
        )

    for gate_result in bulk_result.rejected:
        results.append(
            BulkStagingItemResult(
                status=gate_result.decision.value,
                confidence=gate_result.confidence,
            )
        )

    return {
        "success": True,
        "data": BulkStagingResponse(
            accepted=len(bulk_result.accepted),
            rejected=len(bulk_result.rejected) + len(bulk_result.duplicates),
            results=results,
        ).model_dump(),
    }


def _find_matching_raw(
    values: list[dict[str, Any]],
    dedup_hash: str,
) -> dict[str, Any] | None:
    """Find the raw input dict whose dedup_hash matches (approximate)."""
    from nfm_db.services.quality_gate import compute_dedup_hash

    for raw in values:
        raw_hash = compute_dedup_hash(
            element_system=str(raw.get("element_system", "")),
            phase=raw.get("phase"),
            property_name=str(raw.get("property", raw.get("property_name", ""))),
            method=raw.get("method"),
            source=str(raw.get("source", "")),
        )
        if raw_hash == dedup_hash:
            return raw
    return None


# ---------------------------------------------------------------------------
# GET /api/v1/reference-values/pending-review
# ---------------------------------------------------------------------------


@router.get(
    "/reference-values/pending-review",
    summary="获取待审核暂存记录",
    description="获取待审核暂存记录分页列表，支持按元素体系、相态、属性名、置信度和状态筛选。\n\nPaginated list of staging records pending review.",
)
async def list_pending_review(
    element_system: str | None = Query(default=None, max_length=50),
    phase: str | None = Query(default=None, max_length=50),
    property_name: str | None = Query(default=None, max_length=100),
    confidence: Confidence | None = Query(default=None),
    status: str | None = Query(
        default=None,
        description="Filter by status: pending, approved, rejected, promoted, all",
    ),
    pagination: PaginationParams = Depends(PaginationParams),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """获取待审核暂存记录分页列表。

    Paginated list of staging records pending review.

    Filters: element_system, phase, property_name, confidence, status.
    When status is None or 'pending': returns PENDING records only (default).
    When status is 'approved', 'rejected', 'promoted': returns records with that status.
    When status is 'all': returns all records regardless of status.
    Standard {success, data} envelope with pagination metadata.

    分页参数: page/per_page, 默认 page=1 per_page=20, 最大100
    """
    # Validate status parameter if provided
    if status is not None:
        valid_statuses = {"pending", "approved", "rejected", "promoted", "all"}
        if status not in valid_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
            )

    # Build base filter
    base_filter = []

    # Apply status filter
    if status is None or status == "pending":
        # Default behavior: only pending records
        base_filter.append(RefGapFillStaging.status == StagingStatus.PENDING)
    elif status != "all":
        # Filter by specific status (status is already lowercase)
        status_enum = StagingStatus(status)
        base_filter.append(RefGapFillStaging.status == status_enum)
    # When status == "all", don't add any status filter

    if element_system is not None:
        base_filter.append(
            RefGapFillStaging.element_system == element_system,
        )
    if phase is not None:
        base_filter.append(RefGapFillStaging.phase == phase)
    if property_name is not None:
        base_filter.append(
            RefGapFillStaging.property_name == property_name,
        )
    if confidence is not None:
        base_filter.append(
            RefGapFillStaging.confidence == confidence,
        )

    # Count query
    count_stmt = select(func.count()).select_from(RefGapFillStaging).where(*base_filter)
    total = (await session.execute(count_stmt)).scalar_one()

    # Data query with pagination
    data_stmt = (
        select(RefGapFillStaging)
        .where(*base_filter)
        .order_by(RefGapFillStaging.created_at.desc())
        .limit(pagination.per_page)
        .offset(pagination.offset)
    )
    result = await session.execute(data_stmt)
    records = result.scalars().all()

    return {
        "success": True,
        "data": PendingReviewResponse(
            records=[StagingRecordResponse.model_validate(r) for r in records],
            total=total,
            page=pagination.page,
            per_page=pagination.per_page,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/approve
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/{staging_id}/approve",
    summary="审核通过暂存记录",
    description="审核通过并推广暂存记录，更新状态为已推广并记录审核元数据。\n\nApprove and promote a staging record to production.",
)
async def approve_reference_value(
    staging_id: UUID,
    payload: ReviewRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """审核通过并推广暂存记录。

    Approve and promote a staging record.

    Accepts an optional review_note. Runs the promotion pipeline:
    updates staging status to PROMOTED with review metadata and timestamp.

    When the NFMD normalized schema is available, this will also INSERT
    into property_measurements + measurement_conditions.
    """
    body = payload if payload is not None else ReviewRequest()

    try:
        record = await approve_staging_record(
            session=session,
            staging_id=staging_id,
            review_note=body.review_note,
        )
    except StagingRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Staging record not found") from None
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "success": True,
        "data": ReviewResponse(
            staging_id=record.id,
            status=record.status,
            review_note=record.review_note,
            property_measurement_id=record.promoted_to_pm_id,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/reject
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/{staging_id}/reject",
    summary="驳回暂存记录",
    description="驳回暂存记录，更新状态为已驳回并记录审核元数据。\n\nReject a staging record with review metadata.",
)
async def reject_reference_value(
    staging_id: UUID,
    payload: ReviewRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """驳回暂存记录。

    Reject a staging record.

    Accepts an optional review_note. Updates staging status to REJECTED
    with review metadata and timestamp.
    """
    body = payload if payload is not None else ReviewRequest()

    try:
        record = await reject_staging_record(
            session=session,
            staging_id=staging_id,
            review_note=body.review_note,
        )
    except StagingRecordNotFoundError:
        raise HTTPException(status_code=404, detail="Staging record not found") from None
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "success": True,
        "data": ReviewResponse(
            staging_id=record.id,
            status=record.status,
            review_note=record.review_note,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/export
# NFM-66: Bulk export for external verification
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/export",
    summary="导出参考值供验证",
    description="批量导出参考值供验证服务使用，支持按状态、元素体系、日期范围筛选。\n\nBulk export reference values for external verification.",
)
async def export_reference_values(
    payload: ExportRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """批量导出参考值供验证服务使用。

    Bulk export reference values for verification consumption.

    Exports staged values with optional filters (status, element_system,
    date range). The output format is compatible with verify-service
    ingestion.

    Defaults to exporting approved + promoted records if no status filter
    is provided.
    """
    filters = payload.filters

    records, total = await export_for_verification(
        session=session,
        element_system=filters.element_system,
        phase=filters.phase,
        property_name=filters.property_name,
        confidence=filters.confidence,
        min_confidence=filters.min_confidence,
        status_filter=filters.status,
        from_date=filters.from_date,
        to_date=filters.to_date,
        limit=payload.limit,
        offset=payload.offset,
    )

    exported = [ExportedRecord.model_validate(r) for r in records]

    return {
        "success": True,
        "data": ExportResponse(
            records=exported,
            total=total,
            offset=payload.offset,
            limit=payload.limit,
        ).model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/verify-callback
# NFM-66: Verification results callback from verify-service
# ---------------------------------------------------------------------------


@router.post(
    "/reference-values/verify-callback",
    summary="接收验证回调结果",
    description="接收验证服务回调结果，F级记录自动驳回，所有记录更新审核备注。\n\nReceive verification results from the verify-service.",
)
async def verify_callback(
    payload: VerificationCallbackRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    """接收验证服务回调结果。

    Receive verification results from the verify-service.

    The verify-service sends A-F grades for each staging record.
    F-grade records are auto-rejected. All verified records have
    their review_note updated with the grade and evidence.

    Returns counts of processed, updated, and not-found records.
    """
    results_raw = [item.model_dump() for item in payload.results]
    outcome = await process_verification_results(session, results_raw)

    response_items = [
        VerificationCallbackItem(
            staging_id=item["staging_id"],
            status=item["status"],
        )
        for item in outcome["results"]
    ]

    return {
        "success": True,
        "data": VerificationCallbackResponse(
            processed=outcome["processed"],
            updated=outcome["updated"],
            not_found=outcome["not_found"],
            results=response_items,
        ).model_dump(),
    }
