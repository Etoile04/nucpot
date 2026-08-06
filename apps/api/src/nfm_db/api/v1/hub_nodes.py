"""Hub Node Management API (NFM-2022).

Six endpoints under ``/api/v1/hub/nodes/`` covering the hub-side
CRUD + heartbeat view of resource nodes in the M2 1+N architecture
(NFM-2018):

* ``POST   /register``            — register a new resource node
* ``GET    /``                    — paginated list (default 20, max 100)
* ``GET    /{node_id}``           — node detail
* ``PUT    /{node_id}/status``    — update operational status
* ``POST   /{node_id}/heartbeat`` — record liveness timestamp
* ``DELETE /{node_id}``           — deregister (hard delete)

Sister module ``self_management.py`` (under ``api/v1/hub/``) covers
the hub's own self-management endpoints — kept separate to keep each
file focused and within the 800-line ceiling.

AC-3 (unique-name validation) is enforced at the API layer rather
than via a DB unique index because the B1 schema (NFM-2019) does not
declare ``unique=True`` on ``resource_nodes.name`` and changing that
constraint mid-feature is out of scope for this story.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models import HubNode, ResourceNode, SyncOperation
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams
from nfm_db.schemas.hub_nodes import (
    NodeHeartbeatRequest,
    NodeRegisterRequest,
    NodeResponse,
    NodeStatusUpdate,
    NodeSyncStatsResponse,
    SyncDataItem,
    SyncDataResponse,
    SyncOperationRequest,
    SyncOperationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hub/nodes", tags=["Hub 节点管理"])

# ---------------------------------------------------------------------------
# Auth: Hub token validation
# ---------------------------------------------------------------------------
# TODO(NFM-2029-auth): Replace shared-secret HUB_TOKEN with per-node JWT
# credentials.  The current approach validates that callers possess the hub
# token but cannot distinguish one resource node from another.  A follow-up
# issue should introduce JWT-based auth where the token encodes the node_id,
# allowing sync-data endpoints to derive node identity from the token
# directly (eliminating the path-parameter trust issue flagged in CR #679).

_HUB_TOKEN: str | None = os.environ.get("HUB_TOKEN")


async def _require_hub_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Reject requests that lack a valid ``Authorization: Bearer <token>``.

    Returns ``None`` — this is a *gate* dependency, not an identity
    provider.  It ensures the caller possesses the shared hub secret.
    """
    if _HUB_TOKEN is None:
        # No token configured — gate is disabled (dev / test mode).
        return
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required",
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or parts[1] != _HUB_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid hub token",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """Return UTC now as an ISO-8601 string with explicit ``+00:00``.

    The contract stores ``last_heartbeat`` as ``VARCHAR(50)`` (see
    B1 schema, NFM-2019), so we round-trip through a string instead
    of a tz-aware ``datetime``.  ``datetime.fromisoformat`` (used by
    the test) accepts both ``Z`` and ``+00:00`` suffixes.
    """
    return datetime.now(UTC).isoformat()


async def _get_node_or_404(
    node_id: uuid.UUID,
    db: AsyncSession,
) -> ResourceNode:
    """Fetch a resource node by id or raise a 404."""
    node = await db.get(ResourceNode, node_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource node {node_id} not found",
        )
    return node


# ---------------------------------------------------------------------------
# POST /register — register a resource node
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=ApiResponse[NodeResponse],
    status_code=status.HTTP_201_CREATED,
    summary="注册资源节点",
    description=(
        "Register a new resource node under a hub.  Validates the "
        "hub_node_id FK (404 if missing), the node_type enum (422 if "
        "unknown), and the unique name constraint (409 on duplicate)."
    ),
)
async def register_node(
    body: NodeRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[NodeResponse]:
    """Register a new resource node under a hub."""
    # Verify hub exists (FK pre-check — we want 404, not 500).
    hub = await db.get(HubNode, body.hub_node_id)
    if hub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hub node {body.hub_node_id} not found",
        )

    # AC-3: name uniqueness — enforced at the API layer.
    existing = await db.execute(
        select(ResourceNode).where(ResourceNode.name == body.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Resource node with name {body.name!r} already exists",
        )

    node = ResourceNode(
        hub_node_id=body.hub_node_id,
        name=body.name,
        node_type=body.node_type,
        api_endpoint=body.api_endpoint,
        public_key=body.public_key,
    )
    db.add(node)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Race between the SELECT and the INSERT — surface the same 409
        # the pre-check would have produced.
        logger.warning("Concurrent name collision on register: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Resource node with name {body.name!r} already exists",
        ) from exc
    await db.refresh(node)

    return ApiResponse(success=True, data=NodeResponse.model_validate(node))


# ---------------------------------------------------------------------------
# GET / — paginated list
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=ApiResponse[PaginatedResponse[NodeResponse]],
    summary="列出资源节点",
    description=(
        "Return all registered resource nodes, paginated.  Default "
        "page size is 20; the maximum is 100 (request rejected with "
        "422 above that).  Optionally filter by ``hub_node_id``."
    ),
)
async def list_nodes(
    db: Annotated[AsyncSession, Depends(get_db)],
    params: Annotated[PaginationParams, Depends()],
    hub_node_id: uuid.UUID | None = Query(
        None, description="按所属 hub 节点过滤 (可选)",
    ),
) -> ApiResponse[PaginatedResponse[NodeResponse]]:
    """Return paginated resource nodes, optionally filtered by hub."""
    stmt = select(ResourceNode)
    count_stmt = select(func.count()).select_from(ResourceNode)
    if hub_node_id is not None:
        stmt = stmt.where(ResourceNode.hub_node_id == hub_node_id)
        count_stmt = count_stmt.where(ResourceNode.hub_node_id == hub_node_id)

    total = (await db.execute(count_stmt)).scalar() or 0
    pages = params.pages(total)

    stmt = (
        stmt.order_by(ResourceNode.created_at.desc())
        .offset(params.offset)
        .limit(params.per_page)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=[NodeResponse.model_validate(r) for r in rows],
            total=total,
            page=params.page,
            limit=params.per_page,
            pages=pages,
        ),
    )


# ---------------------------------------------------------------------------
# GET /{node_id} — node detail
# ---------------------------------------------------------------------------


@router.get(
    "/{node_id}",
    response_model=ApiResponse[NodeResponse],
    summary="获取节点详情",
    description="Return one resource node by id, or 404 if it does not exist.",
)
async def get_node(
    node_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[NodeResponse]:
    """Return a single resource node by id."""
    node = await _get_node_or_404(node_id, db)
    return ApiResponse(success=True, data=NodeResponse.model_validate(node))


# ---------------------------------------------------------------------------
# PUT /{node_id}/status — update status
# ---------------------------------------------------------------------------


@router.put(
    "/{node_id}/status",
    response_model=ApiResponse[NodeResponse],
    summary="更新节点状态",
    description=(
        "Update the operational status of a resource node.  Invalid "
        "status values are rejected with 422 (Pydantic-level); an "
        "unknown id returns 404."
    ),
)
async def update_node_status(
    node_id: uuid.UUID,
    body: NodeStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[NodeResponse]:
    """Update the operational status of a resource node."""
    node = await _get_node_or_404(node_id, db)
    node.status = body.status
    await db.commit()
    await db.refresh(node)
    return ApiResponse(success=True, data=NodeResponse.model_validate(node))


# ---------------------------------------------------------------------------
# POST /{node_id}/heartbeat — record liveness
# ---------------------------------------------------------------------------


@router.post(
    "/{node_id}/heartbeat",
    response_model=ApiResponse[NodeResponse],
    summary="节点心跳",
    description=(
        "Record that a resource node is alive.  The server stamps "
        "``last_heartbeat`` with the current UTC time.  Reserved for "
        "future client-supplied metrics (see ``NodeHeartbeatRequest``)."
    ),
)
async def receive_heartbeat(
    node_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _body: NodeHeartbeatRequest | None = None,
) -> ApiResponse[NodeResponse]:
    """Record a heartbeat from a resource node (AC-2)."""
    node = await _get_node_or_404(node_id, db)
    node.last_heartbeat = _iso_now()
    await db.commit()
    await db.refresh(node)
    return ApiResponse(success=True, data=NodeResponse.model_validate(node))


# ---------------------------------------------------------------------------
# GET/POST /{node_id}/sync-data — durable incremental sync
# ---------------------------------------------------------------------------


@router.get(
    "/{node_id}/sync-data",
    response_model=ApiResponse[SyncDataResponse],
    summary="拉取节点增量同步数据",
)
async def fetch_sync_data(
    node_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _auth: Annotated[None, Depends(_require_hub_token)],
    since: int = Query(default=0, ge=0),
) -> ApiResponse[SyncDataResponse]:
    """Return operations with a monotonic sequence greater than ``since``."""
    await _get_node_or_404(node_id, db)
    rows = (
        await db.execute(
            select(SyncOperation)
            .where(
                SyncOperation.resource_node_id == node_id,
                SyncOperation.sequence_no > since,
            )
            .order_by(SyncOperation.sequence_no.asc())
            .limit(1000)
        )
    ).scalars().all()
    watermark = rows[-1].sequence_no if rows else since
    return ApiResponse(
        success=True,
        data=SyncDataResponse(
            items=[SyncDataItem(**row.as_record()) for row in rows],
            watermark=watermark,
        ),
    )


@router.post(
    "/{node_id}/sync-data",
    response_model=ApiResponse[SyncOperationResponse],
    summary="推送节点同步操作",
)
async def push_sync_data(
    node_id: uuid.UUID,
    body: SyncOperationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _auth: Annotated[None, Depends(_require_hub_token)],
) -> ApiResponse[SyncOperationResponse]:
    """Idempotently persist one resource-node operation."""
    node = await _get_node_or_404(node_id, db)
    existing = (
        await db.execute(
            select(SyncOperation).where(
                SyncOperation.resource_node_id == node_id,
                SyncOperation.operation_id == body.operation_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ApiResponse(
            success=True,
            data=SyncOperationResponse(
                operation_id=existing.operation_id,
                watermark=existing.sequence_no,
                duplicate=True,
            ),
        )

    operation = SyncOperation(
        operation_id=body.operation_id,
        resource_node_id=node_id,
        op_type=body.op_type,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        payload=body.payload,
        vector_clock=body.vector_clock,
    )
    db.add(operation)
    await db.flush()
    node.sync_watermark = str(operation.sequence_no)
    await db.commit()
    await db.refresh(operation)
    return ApiResponse(
        success=True,
        data=SyncOperationResponse(
            operation_id=operation.operation_id,
            watermark=operation.sequence_no,
        ),
    )


# ---------------------------------------------------------------------------
# GET /{node_id}/sync-stats — sync statistics for a node
# ---------------------------------------------------------------------------


def _derive_sync_status(
    watermark: str | None,
    heartbeat: str | None,
    offline_since: str | None,
) -> str:
    """Derive a human-readable sync status from node fields."""
    if offline_since is not None:
        return "behind"
    if watermark is None:
        if heartbeat is not None:
            return "syncing"
        return "unknown"
    if heartbeat is not None:
        return "synced"
    return "unknown"


@router.get(
    "/{node_id}/sync-stats",
    response_model=ApiResponse[NodeSyncStatsResponse],
    summary="获取节点同步统计",
    description="Return sync statistics for a resource node, including conflict counts.",
)
async def get_node_sync_stats(
    node_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiResponse[NodeSyncStatsResponse]:
    """Return sync statistics for a resource node."""
    node = await _get_node_or_404(node_id, db)

    # NOTE: ConflictRecord has no FK to ResourceNode (only material_node_id
    # and property_node_id referencing kg_nodes), so per-node conflict
    # counts cannot be scoped here.  Conflict counts are available via the
    # dedicated /api/v1/kg/conflicts endpoint instead.

    sync_status = _derive_sync_status(
        node.sync_watermark,
        node.last_heartbeat,
        node.offline_since,
    )

    return ApiResponse(
        success=True,
        data=NodeSyncStatsResponse(
            node_id=node.id,
            last_heartbeat=node.last_heartbeat,
            sync_watermark=node.sync_watermark,
            offline_since=node.offline_since,
            sync_status=sync_status,
        ),
    )


# ---------------------------------------------------------------------------
# DELETE /{node_id} — deregister
# ---------------------------------------------------------------------------


@router.delete(
    "/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="注销资源节点",
    description="Hard-delete a resource node.  Returns 404 if not found.",
)
async def deregister_node(
    node_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Deregister a resource node (hard delete)."""
    node = await _get_node_or_404(node_id, db)
    await db.delete(node)
    await db.commit()
    # FastAPI will turn the bare Response into a 204; we still return
    # one explicitly to keep the contract clear (no body).
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
