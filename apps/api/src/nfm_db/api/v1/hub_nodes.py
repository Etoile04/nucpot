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
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models import HubNode, ResourceNode
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.hub_nodes import (
    NodeHeartbeatRequest,
    NodeRegisterRequest,
    NodeResponse,
    NodeStatusUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hub/nodes", tags=["Hub 节点管理"])


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
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页数量 (1..100)"),
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
    pages = (total + limit - 1) // limit if total else 0

    stmt = (
        stmt.order_by(ResourceNode.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=[NodeResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            limit=limit,
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