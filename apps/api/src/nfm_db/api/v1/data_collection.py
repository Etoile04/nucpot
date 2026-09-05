"""Data Collection API — coverage scan + request management (NFM-2620).

Endpoints for listing, viewing, and transitioning DataCollectionRequest
records, computing coverage rate metrics, and triggering coverage scans.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user, require_domain_expert
from nfm_db.database import get_db
from nfm_db.models import DataCollectionRequest, User
from nfm_db.models.data_collection_request import DATA_COLLECTION_REQUEST_STATUSES
from nfm_db.schemas.common import PaginatedResponse, PaginationParams
from nfm_db.schemas.data_collection_request import (
    CoverageMetricsResponse,
    DataCollectionRequestResponse,
)
from nfm_db.services.coverage_scan_service import CoverageScanService
from nfm_db.services.gap_dispatch_service import DispatchResult, GapDispatchService

router = APIRouter(tags=["数据采集管理"])
logger = logging.getLogger(__name__)


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
        truncated=pagination.truncated,
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
# POST /data-collection/requests/{id}/dispatch — dispatch to collection path
# ---------------------------------------------------------------------------


@router.post(
    "/data-collection/requests/{request_id}/dispatch",
    response_model=DataCollectionRequestResponse,
    summary="Dispatch a data collection request",
    description=(
        "Dispatch a DataCollectionRequest to its collection path based on "
        "source_preference (literature | dft | external_db | any). "
        "Transitions the request from 'open' to 'in_progress'."
    ),
)
async def dispatch_request(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> DataCollectionRequestResponse:
    """Dispatch a DataCollectionRequest to the appropriate collection path."""
    svc = GapDispatchService(session)
    try:
        await svc.dispatch_request(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    await session.commit()

    # Reload the request to return the updated state.
    result = await session.execute(
        select(DataCollectionRequest).where(
            DataCollectionRequest.id == request_id,
        ),
    )
    req = result.scalar_one()
    return DataCollectionRequestResponse.model_validate(req)


# ---------------------------------------------------------------------------
# POST /data-collection/{request_id}/dispatch -- per-request dispatch
# (NFM-2662)
#
# This is the architecture-aligned per-request endpoint: it returns the
# flat DispatchResult shape ({dispatched_at, dispatched_path,
# dispatch_status, result_reference}) sourced from the persisted
# DataCollectionRequest state.  The legacy /requests/{request_id}/dispatch
# route above continues to return the full DCR payload.
# ---------------------------------------------------------------------------


def _parse_dispatched_at(raw: str) -> datetime:
    """Parse the ISO 8601 ``dispatched_at`` written by ``GapDispatchService``.

    Falls back to ``datetime.now(UTC)`` if the value is missing or malformed,
    so the response is always well-formed.
    """
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Could not parse dispatched_at timestamp %r; falling back to now(UTC)",
            raw,
        )
        return datetime.now(UTC)


@router.post(
    "/data-collection/{request_id}/dispatch",
    summary="Dispatch a DataCollectionRequest (per-request, architecture-aligned)",
    description=(
        "Dispatch a single DataCollectionRequest by ID.  Returns the flat "
        "DispatchResult shape (dispatched_at, dispatched_path, "
        "dispatch_status, result_reference) sourced from the persisted "
        "DataCollectionRequest state.  Returns 404 if the DCR does not "
        "exist and 409 if it has already been dispatched."
    ),
)
async def dispatch_request_per_request(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Dispatch a single DataCollectionRequest and return the spec-shaped result."""
    # Pre-flight: detect 409 (already dispatched) BEFORE handing off to the
    # service so the idempotency rule is explicit at the API boundary.
    # NOTE: This SELECT-then-dispatch pattern has a TOCTOU window — concurrent
    # requests could race past this check.  The GapDispatchService is the
    # authority on idempotency and handles duplicate dispatches internally.
    existing = await session.execute(
        select(DataCollectionRequest).where(
            DataCollectionRequest.id == request_id,
        ),
    )
    req = existing.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )
    if req.status != "open":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"DataCollectionRequest {request_id} is already dispatched "
                f"(status={req.status!r})."
            ),
        )

    svc = GapDispatchService(session)
    try:
        await svc.dispatch_request(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    await session.commit()

    # Reload to read the persisted dispatch metadata.
    refreshed = await session.execute(
        select(DataCollectionRequest).where(
            DataCollectionRequest.id == request_id,
        ),
    )
    dispatched = refreshed.scalar_one()
    dispatch_meta = (dispatched.metadata_ or {}).get("dispatch") or {}

    return {
        "dispatched_at": _parse_dispatched_at(
            str(dispatch_meta.get("dispatched_at", "")),
        ),
        "dispatched_path": str(dispatch_meta.get("path_taken", "")),
        "dispatch_status": str(dispatch_meta.get("dispatch_status", "")),
        "result_reference": str(dispatch_meta.get("detail", "")) or None,
    }

# ---------------------------------------------------------------------------
# POST /data-collection/dispatch — batch dispatch (NFM-2651)
# ---------------------------------------------------------------------------


class BatchDispatchResultItem(BaseModel):
    """Single entry in a batch-dispatch response."""

    request_id: uuid.UUID
    path_taken: str | None = None
    status: str
    detail: str


@router.post(
    "/data-collection/dispatch",
    response_model=list[BatchDispatchResultItem],
    summary="Trigger batch dispatch for open requests",
    description=(
        "Dispatch open DataCollectionRequests to their collection paths. "
        "Returns one result per request, in the order they were processed. "
        "domain_expert role required."
    ),
)
async def batch_dispatch(
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
    ontology_version_id: uuid.UUID | None = Query(
        default=None,
        description="Optional ontology version filter.",
    ),
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of requests to dispatch.",
    ),
) -> list[BatchDispatchResultItem]:
    """Dispatch up to ``limit`` open requests, oldest-urgency first."""
    svc = GapDispatchService(session)

    query = select(DataCollectionRequest).where(
        DataCollectionRequest.status == "open",
    )
    if ontology_version_id is not None:
        query = query.where(
            DataCollectionRequest.ontology_version_id == ontology_version_id,
        )

    # Higher urgency first, then oldest first.
    query = query.order_by(
        DataCollectionRequest.urgency.desc(),
        DataCollectionRequest.created_at.asc(),
    ).limit(limit)

    result = await session.execute(query)
    candidates = result.scalars().all()

    items: list[BatchDispatchResultItem] = []
    for req in candidates:
        try:
            dispatch_result: DispatchResult = await svc.dispatch_request(req.id)
            items.append(
                BatchDispatchResultItem(
                    request_id=dispatch_result.request_id,
                    path_taken=dispatch_result.path_taken,
                    status=dispatch_result.status,
                    detail=dispatch_result.detail,
                ),
            )
        except ValueError as exc:
            # Race: request was removed or already in a non-open state.
            items.append(
                BatchDispatchResultItem(
                    request_id=req.id,
                    path_taken=None,
                    status="failed",
                    detail=str(exc)[:500],
                ),
            )
        except Exception as exc:
            # Best-effort: record the error and continue with the next request.
            items.append(
                BatchDispatchResultItem(
                    request_id=req.id,
                    path_taken=None,
                    status="failed",
                    detail=f"Unexpected error: {exc!s}"[:500],
                ),
            )

    await session.commit()
    return items


# ---------------------------------------------------------------------------
# GET /data-collection/dispatch/status — paginated dispatch status (NFM-2651)
# ---------------------------------------------------------------------------


@router.get(
    "/data-collection/dispatch/status",
    response_model=PaginatedResponse[DataCollectionRequestResponse],
    summary="List dispatched request status",
    description=(
        "Paginated list of DataCollectionRequest records that have been "
        "dispatched (i.e. carry a 'dispatch' entry in ``metadata_``). "
        "Optionally filter by dispatch_status or dispatched_path."
    ),
)
async def list_dispatch_status(
    _current_user: Annotated[User, Depends(require_domain_expert)],
    pagination: PaginationParams = Depends(PaginationParams),
    dispatch_status: str | None = Query(
        default=None,
        description="Filter by dispatch outcome (dispatched | failed | pending).",
    ),
    dispatched_path: str | None = Query(
        default=None,
        description="Filter by collection path (literature | dft | external_db).",
    ),
    session: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DataCollectionRequestResponse]:
    """Return a paginated list of dispatched requests.

    Dispatch metadata lives in the request's ``metadata_`` JSONB bag, so
    status/path filtering is performed client-side after materializing
    rows.  This keeps the query portable to SQLite (used in tests) and
    avoids requiring a GIN index on the metadata column.

    Filtering is applied BEFORE pagination so ``total``/``pages`` reflect
    the full filtered set rather than the current page slice.  A coarse
    ``metadata_ IS NOT NULL`` pre-filter keeps us from materializing rows
    that never had any metadata bag to begin with.
    """
    base_query = (
        select(DataCollectionRequest)
        .where(DataCollectionRequest.metadata_.is_not(None))
        .order_by(DataCollectionRequest.created_at.desc())
    )

    result = await session.execute(base_query)
    rows = result.scalars().all()

    items = [
        DataCollectionRequestResponse.model_validate(r)
        for r in rows
    ]

    # Keep only rows that have actually been dispatched.
    items = [i for i in items if i.dispatched_at is not None]

    if dispatch_status is not None:
        items = [i for i in items if i.dispatch_status == dispatch_status]
    if dispatched_path is not None:
        items = [i for i in items if i.dispatched_path == dispatched_path]

    filtered_total = len(items)
    offset = pagination.offset
    page_slice = items[offset : offset + pagination.per_page]

    return PaginatedResponse(
        items=page_slice,
        total=filtered_total,
        page=pagination.page,
        limit=pagination.per_page,
        pages=pagination.pages(filtered_total),
        truncated=pagination.truncated,
    )


# ---------------------------------------------------------------------------
# POST /data-collection/dispatch/{request_id}/retry — retry failed dispatch
# ---------------------------------------------------------------------------


@router.post(
    "/data-collection/dispatch/{request_id}/retry",
    response_model=DataCollectionRequestResponse,
    summary="Retry a failed dispatch",
    description=(
        "Reset dispatch_status to pending and re-dispatch a previously "
        "dispatched request.  Only requests whose previous dispatch "
        "recorded status 'failed' are eligible.  domain_expert role required."
    ),
)
async def retry_dispatch(
    request_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_domain_expert)],
    session: AsyncSession = Depends(get_db),
) -> DataCollectionRequestResponse:
    """Retry a previously failed dispatch."""
    result = await session.execute(
        select(DataCollectionRequest).where(
            DataCollectionRequest.id == request_id,
        ),
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataCollectionRequest {request_id} not found.",
        )

    existing_meta = dict(req.metadata_ or {})
    dispatch_meta = existing_meta.get("dispatch")
    if not isinstance(dispatch_meta, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"DataCollectionRequest {request_id} has no prior dispatch "
                f"record; nothing to retry."
            ),
        )

    prior_status = dispatch_meta.get("dispatch_status")
    if prior_status != "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"DataCollectionRequest {request_id} last dispatch_status "
                f"is {prior_status!r}; only 'failed' dispatches may be "
                f"retried."
            ),
        )

    # Reset to 'open' so GapDispatchService.dispatch_request can re-dispatch.
    # Drop the prior dispatch metadata so the response reflects only the
    # new dispatch outcome (the service will write a fresh 'dispatch' block).
    req.status = "open"
    existing_meta.pop("dispatch", None)
    req.metadata_ = existing_meta

    await session.flush()

    svc = GapDispatchService(session)
    try:
        await svc.dispatch_request(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    await session.commit()
    await session.refresh(req)
    return DataCollectionRequestResponse.model_validate(req)
