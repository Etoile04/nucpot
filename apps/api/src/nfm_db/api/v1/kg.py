"""Knowledge Graph query, review queue, and conflict resolution API endpoints.

Query modes (NFM-858):
  - GET  /kg/query/property  — find nodes by property value
  - GET  /kg/query/relation  — find edges by relation type between entities
  - GET  /kg/query/path      — find paths between entities (depth ≤3)

Review queue (NFM-859):
  - GET  /kg/review/queue          — list pending review items
  - POST /kg/review/{id}/approve   — approve and add to KG
  - POST /kg/review/{id}/reject    — reject with reason

Conflict resolution (NFM-861):
  - GET  /kg/conflicts              — list conflict records
  - POST /kg/conflicts/{id}/resolve — resolve a conflict
  - POST /kg/fusion                  — run multi-source fusion pipeline
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.schemas.common import ApiResponse
from nfm_db.schemas.conflict import (
    ConflictListResponse,
    ConflictResponse,
    FusionResult,
    ResolveConflictRequest,
    ResolveConflictResponse,
)
from nfm_db.schemas.kg_query import (
    Direction,
    MAX_PATH_DEPTH,
    PathQueryRequest,
    PathQueryResponse,
    PropertyQueryRequest,
    PropertyQueryResponse,
    RelationQueryRequest,
    RelationQueryResponse,
)
from nfm_db.services.kg_query_service import (
    path_query,
    property_query,
    relation_query,
)
from nfm_db.services.multi_source_fusion import (
    list_conflicts,
    resolve_single_conflict,
    run_fusion,
)
from nfm_db.services.review_queue_service import (
    approve_review_item,
    list_pending_reviews,
    reject_review_item,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/kg/query/property",
    response_model=ApiResponse[PropertyQueryResponse],
)
async def property_query_endpoint(
    params: Annotated[PropertyQueryRequest, Query(description="Property-query filters")],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PropertyQueryResponse]:
    """Find knowledge graph nodes by property value, label, or type."""
    result = await property_query(
        db,
        node_type=params.node_type,
        label=params.label,
        property_key=params.property_key,
        property_value=params.property_value,
        fuzzy=params.fuzzy,
        limit=params.limit,
        offset=params.offset,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/kg/query/relation",
    response_model=ApiResponse[RelationQueryResponse],
)
async def relation_query_endpoint(
    params: Annotated[RelationQueryRequest, Query(description="Relation-query filters")],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[RelationQueryResponse]:
    """Find knowledge graph edges by relation type and/or endpoint nodes.

    ``direction`` is restricted to ``outgoing | incoming | both`` by the
    Pydantic ``Direction`` literal — invalid values yield 422 before the
    service is invoked.
    """
    result = await relation_query(
        db,
        source_node_id=params.source_node_id,
        target_node_id=params.target_node_id,
        relation_type=params.relation_type,
        direction=params.direction,
        limit=params.limit,
        offset=params.offset,
    )
    return ApiResponse(success=True, data=result)


@router.get(
    "/kg/query/path",
    response_model=ApiResponse[PathQueryResponse],
)
async def path_query_endpoint(
    params: Annotated[PathQueryRequest, Query(description="Path-query parameters")],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PathQueryResponse]:
    """Find paths between two KG nodes via multi-hop traversal.

    ``max_depth`` is hard-capped at :data:`MAX_PATH_DEPTH` (3) per B2.4 spec.
    """
    result = await path_query(
        db,
        source_node_id=params.source_node_id,
        target_node_id=params.target_node_id,
        max_depth=params.max_depth,
        relation_types=params.relation_types,
        limit=params.limit,
    )
    return ApiResponse(success=True, data=result)


# ===========================================================================
# Review Queue endpoints (NFM-859 B2.6)
# ===========================================================================


class ApproveRequest(BaseModel):
    """Request body for approving a review item."""

    reviewer_notes: str | None = Field(
        None,
        description="Optional notes from the reviewer",
    )


class RejectRequest(BaseModel):
    """Request body for rejecting a review item."""

    reason: str = Field(
        ...,
        min_length=1,
        description="Reason for rejection",
    )


class ReviewQueueResponse(BaseModel):
    """Response envelope for the review queue listing."""

    items: list[dict] = Field(default_factory=list)
    total: int = 0


@router.get(
    "/kg/review/queue",
    response_model=ApiResponse[ReviewQueueResponse],
)
async def list_review_queue(
    item_type: str | None = Query(None, description="Filter by item type (entity/relation)"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ReviewQueueResponse]:
    """List pending items in the KG review queue."""
    items, total = await list_pending_reviews(
        db,
        item_type=item_type,
        limit=limit,
        offset=offset,
    )
    return ApiResponse(
        success=True,
        data=ReviewQueueResponse(items=items, total=total),
    )


@router.post(
    "/kg/review/{review_id}/approve",
    response_model=ApiResponse[dict],
)
async def approve_review(
    review_id: UUID,
    body: ApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Approve a pending review item and promote it to the live KG."""
    result = await approve_review_item(
        db,
        review_id=review_id,
        reviewer_notes=body.reviewer_notes if body else None,
    )
    if "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result["error"],
        )
    return ApiResponse(success=True, data=result)


@router.post(
    "/kg/review/{review_id}/reject",
    response_model=ApiResponse[dict],
)
async def reject_review(
    review_id: UUID,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Reject a pending review item with a reason."""
    result = await reject_review_item(
        db,
        review_id=review_id,
        reason=body.reason,
    )
    if "error" in result:
        raise HTTPException(
            status_code=result.get("status_code", 400),
            detail=result["error"],
        )
    return ApiResponse(success=True, data=result)


# ===========================================================================
# Conflict Resolution endpoints (NFM-861)
# ===========================================================================


@router.get(
    "/kg/conflicts",
    response_model=ApiResponse[ConflictListResponse],
)
async def list_conflicts_endpoint(
    material_id: str | None = Query(None, description="Filter by material node UUID"),
    property_type_id: str | None = Query(
        None, description="Filter by property type UUID"
    ),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ConflictListResponse]:
    """List conflict records from multi-source fusion."""
    mat_uuid: UUID | None = None
    if material_id:
        try:
            mat_uuid = UUID(material_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid material_id UUID")

    pt_uuid: UUID | None = None
    if property_type_id:
        try:
            pt_uuid = UUID(property_type_id)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid property_type_id UUID"
            )

    records, total = await list_conflicts(
        db,
        material_id=mat_uuid,
        property_type_id=pt_uuid,
        status=status,
        limit=limit,
        offset=offset,
    )

    conflict_responses = [
        ConflictResponse(
            id=r.id,
            material_node_id=r.material_node_id,
            property_node_id=r.property_node_id,
            property_type_id=r.property_type_id,
            conflicting_values=r.conflicting_values,
            strategy=r.strategy,
            resolved_value=r.resolved_value,
            status=r.status,
            resolved_by=r.resolved_by,
            resolved_at=r.resolved_at,
            resolution_notes=r.resolution_notes,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in records
    ]

    return ApiResponse(
        success=True,
        data=ConflictListResponse(
            conflicts=conflict_responses,
            total=total,
        ),
    )


@router.post(
    "/kg/conflicts/{conflict_id}/resolve",
    response_model=ApiResponse[ResolveConflictResponse],
)
async def resolve_conflict_endpoint(
    conflict_id: UUID,
    body: ResolveConflictRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ResolveConflictResponse]:
    """Resolve a conflict record manually or re-run auto-resolution."""
    record = await resolve_single_conflict(
        db,
        conflict_id=conflict_id,
        resolved_value=body.resolved_value if body else None,
        strategy_override=body.strategy_override if body else None,
        notes=body.notes if body else None,
    )

    if record is None:
        raise HTTPException(status_code=404, detail="Conflict record not found")

    return ApiResponse(
        success=True,
        data=ResolveConflictResponse(
            id=record.id,
            strategy=record.strategy,
            resolved_value=record.resolved_value,
            status=record.status,
            resolved_at=record.resolved_at,
        ),
    )


@router.post(
    "/kg/fusion",
    response_model=ApiResponse[FusionResult],
)
async def run_fusion_endpoint(
    material_id: str | None = Query(None, description="Material node UUID to fuse"),
    strategy_override: str | None = Query(
        None, description="Override conflict strategy for all properties"
    ),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FusionResult]:
    """Run multi-source fusion pipeline to detect and resolve conflicts."""
    mat_uuid: UUID | None = None
    if material_id:
        try:
            mat_uuid = UUID(material_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid material_id UUID")

    result = await run_fusion(
        db,
        material_id=mat_uuid,
        strategy_override=strategy_override,
    )
    return ApiResponse(success=True, data=result)
