"""Cursor-based pagination schemas.

Provides cursor pagination parameters and response models
for endpoints that need stable pagination over large datasets.

Cursor format: base64-encoded JSON ``{"created_at": "...", "id": "..."}``.
Data must be ordered by (created_at DESC, id DESC) for consistency.
"""

from __future__ import annotations

import base64
import json
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def encode_cursor(created_at: str, record_id: str) -> str:
    """Encode a (created_at, id) pair into an opaque cursor string."""
    payload = json.dumps({"created_at": created_at, "id": record_id})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode an opaque cursor string into (created_at, id).

    Raises ValueError if the cursor is malformed.
    """
    try:
        payload = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(payload)
        return data["created_at"], data["id"]
    except (Exception, KeyError) as exc:
        raise ValueError(f"Invalid cursor: {cursor}") from exc


class CursorPaginationParams(BaseModel):
    """Cursor-based pagination parameters.

    Exactly one of ``after_cursor`` or ``before_cursor`` may be set.
    If neither is set, the first page is returned.
    """

    after_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page (older records).",
    )
    before_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the previous page (newer records).",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Items per page.")


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Response envelope for cursor-paginated data.

    ``has_next`` / ``has_prev`` tell the UI whether to render
    the corresponding navigation button.
    """

    items: list[T]
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_next: bool = False
    has_prev: bool = False
