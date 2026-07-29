"""Pydantic schemas for the Hub Node Management API (NFM-2022).

These are the request/response shapes for ``/api/v1/hub/nodes/`` —
the hub-side view of resource nodes in the M2 1+N architecture
(NFM-2018).  Unlike ``schemas/data_submission.py`` (NFM-2019), which
covers the six ORM tables generically, the schemas here focus on the
hub's CRUD + heartbeat view of resource nodes.

Why a separate file:

* The data_submission schemas type ``last_heartbeat`` as ``datetime``
  while the ORM column is ``String(50)`` (the contract stores ISO
  strings).  We use ``str | None`` here to round-trip cleanly via
  ``from_attributes=True`` without forcing a model rewrite.
* The hub endpoints accept a slimmer payload (no offline_since /
  sync_watermark) and return an ``ApiResponse`` envelope that the
  generic data_submission schemas don't.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from nfm_db.schemas.common import ApiResponse, PaginatedResponse


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


NodeStatusLiteral = Literal["active", "inactive", "suspended"]
"""Operational status of a resource node.

Mirrors the ``NodeStatus`` constants in ``data_submission.py``.
We duplicate as a ``Literal`` so Pydantic validates the wire format
without importing a class whose ``__getattr__`` is a plain str.
"""

NodeTypeLiteral = Literal["computing", "storage", "observatory"]
"""Type of a resource node in the 1+N architecture."""


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class NodeRegisterRequest(BaseModel):
    """Body for ``POST /api/v1/hub/nodes/register``.

    ``name`` uniqueness is enforced at the API layer (no DB-level
    unique index — see AC-3 in NFM-2022).  ``hub_node_id`` must
    reference an existing hub; ``node_type`` must be one of the three
    1+N topology roles.
    """

    hub_node_id: uuid.UUID = Field(
        description="Owning hub node; must reference an existing row.",
    )
    name: str = Field(min_length=1, max_length=200)
    node_type: NodeTypeLiteral
    api_endpoint: str = Field(min_length=1, max_length=500)
    public_key: str | None = Field(default=None, max_length=2000)


class NodeStatusUpdate(BaseModel):
    """Body for ``PUT /api/v1/hub/nodes/{node_id}/status``.

    Pydantic rejects unknown statuses with 422 before the handler
    runs — that is AC-1's "invalid status returns 422" path.
    """

    status: NodeStatusLiteral


class NodeHeartbeatRequest(BaseModel):
    """Body for ``POST /api/v1/hub/nodes/{node_id}/heartbeat``.

    Body is optional and currently carries no client-supplied fields;
    the hub stamps ``last_heartbeat`` with the server-side UTC now.
    Reserved for future ``{client_metrics: ...}`` payload.
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class NodeResponse(BaseModel):
    """Public representation of a resource node for the hub API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_node_id: uuid.UUID
    name: str
    node_type: str
    api_endpoint: str
    public_key: str | None = None
    status: str
    last_heartbeat: str | None = Field(
        default=None,
        description="ISO timestamp of last heartbeat (string per contract).",
    )
    offline_since: str | None = None
    sync_watermark: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Convenience aliases (re-export the envelope types so routers can import
# them from a single module)
# ---------------------------------------------------------------------------


NodeListResponse = PaginatedResponse[NodeResponse]
"""Paginated list of resource nodes — wrapped in ``ApiResponse`` by the route."""


ApiResponseNode = ApiResponse[NodeResponse]
ApiResponseNodeList = ApiResponse[NodeListResponse]


__all__ = [
    "ApiResponseNode",
    "ApiResponseNodeList",
    "NodeHeartbeatRequest",
    "NodeListResponse",
    "NodeRegisterRequest",
    "NodeResponse",
    "NodeStatusLiteral",
    "NodeStatusUpdate",
    "NodeTypeLiteral",
]