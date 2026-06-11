"""Generic API response envelopes for all endpoints.

Provides type-safe, reusable wrappers so every endpoint shares a
consistent JSON shape without duplicating boilerplate.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):  # type: ignore[valid-type]  # noqa: UP046
    """Standard success/error envelope used by every endpoint."""

    success: bool
    data: T | None = None
    error: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):  # type: ignore[valid-type]  # noqa: UP046
    """Paginated data payload — intended to be wrapped in ``ApiResponse``."""

    items: list[T]
    total: int
    page: int
    limit: int
    pages: int
