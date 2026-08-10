"""Data Collection API — coverage scan + request management (NFM-2620).

Endpoints for listing, viewing, and transitioning DataCollectionRequest
records, computing coverage rate metrics, and triggering coverage scans.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user, require_domain_expert
from nfm_db.database import get_db
from nfm_db.models import DataCollectionRequest, User
from nfm_db.models.data_collection_request import (
    DATA_COLLECTION_REQUEST_STATUSES,
    SOURCE_PREFERENCES,
)
from nfm_db.schemas.common import PaginatedResponse, PaginationParams
from nfm_db.schemas.data_collection_request import (
    CoverageMetricsResponse,
    DataCollectionRequestResponse,
)
from nfm_db.services.coverage_scan_service import CoverageScanService
from nfm_db.services.gap_dispatch_service import (
    DEFAULT_BATCH_LIMIT,
    DispatchResult,
    GapDispatchService,
)
from nfm_db.services.paths import (
    DFTFillPath,
    ExternalDBFillPath,
    GapFillPath,
    LiteratureFillPath,
)


def _build_fill_paths(
    session: AsyncSession,
) -> dict[str, GapFillPath]:
    """Instantiate all concrete fill-path handlers keyed by ``source_preference``.

    Used by ``dispatch_single_request`` so the per-request route routes to the
    real handlers instead of falling through ``GapDispatchService`` with an
    empty ``fill_paths`` dict (which would short-circuit every dispatch with
    ``"No fill path registered"``).
    """
    return {
        "literature": LiteratureFillPath(session=session),
        "dft": DFTFillPath(session=session),
        "external_db": ExternalDBFillPath(session=session),
    }


router = APIRouter(tags=["数据采集管理"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class StatusTransitionRequest(BaseModel):
    """Request body for PATCH /data-collection/requests/{id}/status."""

    status: str = Field(
        description="Target status (open | in_progress | completed | declined).",
    )


class CoverageScanRequest(BaseModel):
    """Request body for POST /data-collection/scan."""

    ontology_version_id: uuid.UUID = Field(
        description="Ontology version to scan against.",
    )
    material_system: str = Field(
        default="unspecified",
        description="Material system label for created requests.",
        max_length=200,
    )


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress", "declined"},
    "in_progress": {"completed", "declined"},
    "completed": set(),
    "declined": {"open"},
}


# ---------------------------------------------------------------------------
# GET /data-collection/requests — paginated list
# ---------------------------------------------------------------------------


@router.get(
    "/data-collection/requests",
    response_model=PaginatedResponse[DataCollectionRequestResponse],
    summary="List data collection requests",
    description="Paginated list of all DataCollectionRequest records.",
)
async def list_requests(
    _current_user: Annotated[User, Depends(get_current_active_user)],
    pagination: PaginationParams = Depends(PaginationParams),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Optional status filter.",
    ),
    entity_type: str | None = Query(
        default=None,
        description="Optional entity_type filter.",
    ),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DataCollectionRequestResponse]:
    """Return a paginated list of DataCollectionRequest records."""
    query = select(DataCollectionRequest).order_by(
        DataCollectionRequest.created_at.desc(),
    )

    if status_filter is not None:
        if status_filter not in DATA_COLLECTION_REQUEST_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid status: {status_filter!r}. "
                    f"Must be one of {DATA_COLLECTION_REQUEST_STATUSES}."
                ),
            )
        query = query.where(DataCollectionRequest.status == status_filter)

    if entity_type is not None:
        query = query.where(DataCollectionRequest.entity_type == entity_type)

    count_result = await session.execute(
        select(func.count()).select_from(query.subquery()),
    )
    total = count_result.scalar_one()

    paginated_query = query.offset(pagination.offset).limit(pagination.per_page)
    result = await session.execute(paginated_query)
    items = [
        DataCollectionRequestResponse.model_validate(r)
        for r in result.scalars().all()
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        limit=pagination.per_page,
        pages=pagination.pages(total),
    )


# ---------------------------------------------------------------------------
# GET /data-collection/requests/{id} — detail
# ---------------------------------------------------------------------------


@router.get(
    "/data-collection/requests/{request_id}",
    response_model=DataCollectionRequestResponse,
    summary="Get data collection request detail",
    description="Return a single DataCollectionRequest by ID.",
)
async def get_request(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> DataCollectionRequestResponse:
    """Return a single DataCollectionRequest."""
    result = await session.execute(
        select(DataCollectionRequest).where(DataCollectionRequest.id == request_id),
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )
    return DataCollectionRequestResponse.model_validate(req)


# ---------------------------------------------------------------------------
# PATCH /data-collection/requests/{id}/status — status transition
# ---------------------------------------------------------------------------


@router.patch(
    "/data-collection/requests/{request_id}/status",
    response_model=DataCollectionRequestResponse,
    summary="Transition request status",
    description="Update the status of a DataCollectionRequest.",
)
async def update_status(
    request_id: uuid.UUID,
    body: StatusTransitionRequest,
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> DataCollectionRequestResponse:
    """Transition a DataCollectionRequest to a new status."""
    result = await session.execute(
        select(DataCollectionRequest).where(DataCollectionRequest.id == request_id),
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )

    current_status = req.status
    target_status = body.status

    if target_status not in DATA_COLLECTION_REQUEST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid status: {target_status!r}. "
                f"Must be one of {DATA_COLLECTION_REQUEST_STATUSES}."
            ),
        )

    allowed = _VALID_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Cannot transition from {current_status!r} to {target_status!r}. "
                f"Allowed: {sorted(allowed) or ['(terminal)']}."
            ),
        )

    req.status = target_status
    if target_status in ("completed", "declined"):
        req.completed_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(req)
    return DataCollectionRequestResponse.model_validate(req)


# ---------------------------------------------------------------------------
# GET /data-collection/coverage/{ontology_version_id} — coverage metrics
# ---------------------------------------------------------------------------


@router.get(
    "/data-collection/coverage/{ontology_version_id}",
    response_model=CoverageMetricsResponse,
    summary="Get coverage rate metrics",
    description="Return coverage metrics for a specific ontology version.",
)
async def get_coverage(
    ontology_version_id: uuid.UUID,
    _current_user: Annotated[User, Depends(get_current_active_user)],
    session: AsyncSession = Depends(get_db),
) -> CoverageMetricsResponse:
    """Compute and return coverage metrics for an ontology version."""
    svc = CoverageScanService(session)
    try:
        metrics = await svc.compute_metrics(ontology_version_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # Count existing requests by status for this ontology version
    count_stmt = (
        select(DataCollectionRequest.status, func.count())
        .where(DataCollectionRequest.ontology_version_id == ontology_version_id)
        .group_by(DataCollectionRequest.status)
    )
    status_rows = (await session.execute(count_stmt)).all()
    status_counts = {row[0]: row[1] for row in status_rows}

    total_requests = sum(status_counts.values())
    open_requests = status_counts.get("open", 0)
    in_progress_requests = status_counts.get("in_progress", 0)
    completed_requests = status_counts.get("completed", 0)
    declined_requests = status_counts.get("declined", 0)

    return CoverageMetricsResponse(
        ontology_version_id=ontology_version_id,
        total_requests=total_requests,
        open_requests=open_requests,
        in_progress_requests=in_progress_requests,
        completed_requests=completed_requests,
        declined_requests=declined_requests,
        coverage_rate=metrics.coverage_rate,
        computed_at=metrics.computed_at,
    )


# ---------------------------------------------------------------------------
# POST /data-collection/scan — trigger coverage scan
# ---------------------------------------------------------------------------


@router.post(
    "/data-collection/scan",
    summary="Trigger a coverage scan",
    description=(
        "Scan an ontology version against DB records and create "
        "DataCollectionRequests for uncovered properties."
    ),
)
async def trigger_scan(
    body: CoverageScanRequest,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run a coverage scan and return the result summary."""
    svc = CoverageScanService(session)
    try:
        result = await svc.run_scan(
            ontology_version_id=body.ontology_version_id,
            material_system=body.material_system,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    await session.commit()

    return {
        "ontology_version_id": str(result.ontology_version_id),
        "metrics": {
            "total_expected": result.metrics.total_expected,
            "covered": result.metrics.covered,
            "uncovered": result.metrics.uncovered,
            "coverage_rate": round(result.metrics.coverage_rate, 4),
        },
        "uncovered_properties": [
            {"entity_type": up.entity_type, "property_name": up.property_name}
            for up in result.uncovered_properties
        ],
        "requests_created": result.requests_created,
        "scan_duration_ms": result.scan_duration_ms,
    }


# ---------------------------------------------------------------------------
# Dispatch response schemas
# ---------------------------------------------------------------------------


class DispatchResponseItem(BaseModel):
    """Single dispatch result paired with a DCR ID."""

    request_id: uuid.UUID
    success: bool
    path: str | None = None
    reference: str | None = None
    error: str | None = None
    data_found: bool = False


class DispatchBatchResponse(BaseModel):
    """Response for POST /data-collection/dispatch."""

    dispatched_count: int
    results: list[DispatchResponseItem]


# ---------------------------------------------------------------------------
# POST /data-collection/dispatch — trigger batch dispatch
# ---------------------------------------------------------------------------


@router.post(
    "/data-collection/dispatch",
    response_model=DispatchBatchResponse,
    summary="Trigger gap dispatch",
    description=(
        "Select open, undispatched DataCollectionRequests and dispatch them "
        "through the gap-fill router."
    ),
)
async def trigger_dispatch(
    _current_user: Annotated[User, Depends(require_domain_expert)],
    ontology_version_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=DEFAULT_BATCH_LIMIT, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> DispatchBatchResponse:
    """Select open undispatched DCRs and dispatch each individually."""
    stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.status == "open",
        DataCollectionRequest.dispatched_at.is_(None),
    )
    if ontology_version_id is not None:
        stmt = stmt.where(
            DataCollectionRequest.ontology_version_id == ontology_version_id,
        )
    stmt = stmt.order_by(DataCollectionRequest.urgency.desc()).limit(limit)
    dcrs = list((await session.execute(stmt)).scalars().all())

    svc = GapDispatchService(session, fill_paths=_build_fill_paths(session))
    paired: list[tuple[uuid.UUID, DispatchResult]] = []
    for dcr in dcrs:
        try:
            r = await svc.dispatch(dcr)
            paired.append((dcr.id, r))
        except Exception as exc:
            paired.append((
                dcr.id,
                DispatchResult(
                    success=False,
                    path="error",
                    reference=None,
                    error=f"Dispatch error for {dcr.id}: {exc}",
                    data_found=False,
                ),
            ))
    await session.commit()

    results = [
        DispatchResponseItem(
            request_id=req_id,
            success=r.success,
            path=r.path,
            reference=r.reference,
            error=r.error,
            data_found=r.data_found,
        )
        for req_id, r in paired
    ]
    return DispatchBatchResponse(
        dispatched_count=len(results),
        results=results,
    )


# ---------------------------------------------------------------------------
# GET /data-collection/dispatch/status — paginated dispatch status
# ---------------------------------------------------------------------------


@router.get(
    "/data-collection/dispatch/status",
    response_model=PaginatedResponse[DataCollectionRequestResponse],
    summary="List dispatch status",
    description="Paginated list of dispatched DataCollectionRequests.",
)
async def list_dispatch_status(
    _current_user: Annotated[User, Depends(get_current_active_user)],
    pagination: PaginationParams = Depends(PaginationParams),
    dispatch_status_filter: str | None = Query(
        default=None,
        alias="dispatch_status",
        description="Filter by dispatch_status.",
    ),
    dispatched_path_filter: str | None = Query(
        default=None,
        alias="dispatched_path",
        description="Filter by dispatched_path.",
    ),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DataCollectionRequestResponse]:
    """Return paginated dispatch status for dispatched DCRs."""
    query = select(DataCollectionRequest).where(
        DataCollectionRequest.dispatched_at.is_not(None),
    ).order_by(DataCollectionRequest.dispatched_at.desc())

    if dispatch_status_filter is not None:
        query = query.where(
            DataCollectionRequest.dispatch_status == dispatch_status_filter,
        )
    if dispatched_path_filter is not None:
        query = query.where(
            DataCollectionRequest.dispatched_path == dispatched_path_filter,
        )

    count_result = await session.execute(
        select(func.count()).select_from(query.subquery()),
    )
    total = count_result.scalar_one()

    paginated_query = query.offset(pagination.offset).limit(pagination.per_page)
    result = await session.execute(paginated_query)
    items = [
        DataCollectionRequestResponse.model_validate(r)
        for r in result.scalars().all()
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        limit=pagination.per_page,
        pages=pagination.pages(total),
    )


# ---------------------------------------------------------------------------
# POST /data-collection/dispatch/{id}/retry — retry failed dispatch
# ---------------------------------------------------------------------------


@router.post(
    "/data-collection/dispatch/{request_id}/retry",
    response_model=DataCollectionRequestResponse,
    summary="Retry a failed dispatch",
    description="Re-dispatch a DataCollectionRequest that previously failed.",
)
async def retry_dispatch(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> DataCollectionRequestResponse:
    """Retry dispatch for a failed DCR."""
    result = await session.execute(
        select(DataCollectionRequest).where(DataCollectionRequest.id == request_id),
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )
    if req.dispatch_status != "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Cannot retry: dispatch_status is {req.dispatch_status!r}, "
                "expected 'failed'."
            ),
        )

    svc = GapDispatchService(session)
    try:
        await svc.dispatch(req)
    except Exception as exc:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retry dispatch failed: {exc}",
        ) from exc

    await session.commit()
    await session.refresh(req)
    return DataCollectionRequestResponse.model_validate(req)


# ---------------------------------------------------------------------------
# POST /data-collection/{id}/dispatch — dispatch a single request (NFM-2662)
# ---------------------------------------------------------------------------


class PerRequestDispatchRequest(BaseModel):
    """Optional body for POST /data-collection/{request_id}/dispatch."""

    source_preference_override: str | None = Field(
        default=None,
        description=(
            "Override the stored source_preference before routing. "
            f"One of: {', '.join(SOURCE_PREFERENCES)}."
        ),
    )

    @field_validator("source_preference_override")
    @classmethod
    def _validate_source_preference(cls, value: str | None) -> str | None:
        """Reject overrides outside the canonical source_preference set."""
        if value is not None and value not in SOURCE_PREFERENCES:
            raise ValueError(
                f"source_preference_override must be one of "
                f"{list(SOURCE_PREFERENCES)}, got {value!r}.",
            )
        return value


class PerRequestDispatchResponse(BaseModel):
    """Persisted dispatch columns after a single-request dispatch."""

    dispatched_at: datetime
    dispatched_path: str
    dispatch_status: str
    result_reference: str | None = None


@router.post(
    "/data-collection/{request_id}/dispatch",
    response_model=PerRequestDispatchResponse,
    summary="Dispatch a single data collection request",
    description=(
        "Route one DataCollectionRequest through the gap-fill dispatcher and "
        "return its persisted dispatch columns. Requests that were already "
        "dispatched are rejected with 409."
    ),
)
async def dispatch_single_request(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    body: PerRequestDispatchRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> PerRequestDispatchResponse:
    """Dispatch one DCR and return the columns the dispatcher persisted."""
    result = await session.execute(
        select(DataCollectionRequest).where(DataCollectionRequest.id == request_id),
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )
    if req.dispatched_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"DataCollectionRequest {request_id} was already dispatched at "
                f"{req.dispatched_at.isoformat()} "
                f"(dispatch_status={req.dispatch_status!r})."
            ),
        )

    if body is not None and body.source_preference_override is not None:
        req.source_preference = body.source_preference_override

    svc = GapDispatchService(session, fill_paths=_build_fill_paths(session))
    try:
        await svc.dispatch(req)
    except Exception as exc:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Dispatch failed for {request_id}: {exc}",
        ) from exc

    await session.commit()
    await session.refresh(req)
    return PerRequestDispatchResponse(
        dispatched_at=req.dispatched_at,
        dispatched_path=req.dispatched_path,
        dispatch_status=req.dispatch_status,
        result_reference=req.result_reference,
    )
