"""Generic API response envelopes for all endpoints.

Provides type-safe, reusable wrappers so every endpoint shares a
consistent JSON shape without duplicating boilerplate.
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorCode(str, Enum):
    """Machine-readable error identifiers returned in every error response."""

    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"
    CONFLICT = "CONFLICT"
    BATCH_IMPORT_ERROR = "BATCH_IMPORT_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"


class ErrorResponse(BaseModel):
    """Standard error payload with machine-readable code."""

    error_code: ErrorCode
    message: str
    details: dict | None = None


class ApiResponse(BaseModel, Generic[T]):
    """Standard success/error envelope used by every endpoint."""

    success: bool
    data: T | None = None
    error: str | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated data payload — intended to be wrapped in ``ApiResponse``."""

    items: list[T]
    total: int
    page: int
    limit: int
    pages: int


class PaginationParams(BaseModel):
    """Reusable pagination query parameters for all paginated endpoints.

    Inject via ``Depends(PaginationParams)`` to accept ``?page=1&per_page=20``.
    Accepts both ``per_page`` and ``limit`` as the page-size query parameter
    for backward compatibility with endpoints that previously used ``limit``.
    """

    model_config = ConfigDict(populate_by_name=True)

    page: int = Field(1, ge=1, description="页码")
    per_page: int = Field(
        20,
        ge=1,
        le=100,
        description="每页数量",
        alias="per_page",
    )
