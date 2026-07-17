"""Generic API response envelopes and pagination dependencies.

Provides type-safe, reusable wrappers so every endpoint shares a
consistent JSON shape without duplicating boilerplate.
"""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from nfm_db.schemas.errors import ErrorCode as _ErrorCode

T = TypeVar("T")

# Re-export ErrorCode from errors module for backward compatibility
ErrorCode = _ErrorCode


class ErrorResponse(BaseModel):
    """Structured error response model with machine-readable error_code."""

    error_code: ErrorCode
    message: str
    details: Any | None = None


class PaginationParams(BaseModel):
    """Unified pagination parameters for all list/search endpoints."""

    page: int = Field(default=1, ge=1, description="页码")
    per_page: int = Field(default=20, ge=1, le=100, description="每页数量")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    def pages(self, total: int) -> int:
        if total <= 0:
            return 0
        return math.ceil(total / self.per_page)


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
