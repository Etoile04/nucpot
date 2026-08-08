"""Data Collection API — coverage scan + request management (NFM-2620).

Endpoints for listing, viewing, and transitioning DataCollectionRequest
records, computing coverage rate metrics, and triggering coverage scans.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models import DataCollectionRequest, User
from nfm_db.models.data_collection_request import DATA_COLLECTION_REQUEST_STATUSES
from nfm_db.schemas.common import PaginatedResponse, PaginationParams
from nfm_db.schemas.data_collection_request import (
    CoverageMetricsResponse,
    DataCollectionRequestResponse,
)
from nfm_db.services.coverage_scan_service import CoverageScanService

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
    _current_user: Annotated[User, Depends(get_current_active_user)],
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
