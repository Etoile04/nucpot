"""Generic API response envelopes and pagination dependencies.

Provides type-safe, reusable wrappers so every endpoint shares a
consistent JSON shape without duplicating boilerplate.
"""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from nfm_db.schemas.errors import ErrorCode as _ErrorCode

T = TypeVar("T")

# Re-export ErrorCode from errors module for backward compatibility
ErrorCode = _ErrorCode

#: Server-side cap for page size (NFM-4308 ③). Requests above the cap are
#: clamped to this value and flagged via ``PaginationParams.truncated`` /
#: ``PaginatedResponse.truncated`` — never silently dropped or 422-rejected.
MAX_PER_PAGE = 100
DEFAULT_PER_PAGE = 20


class ErrorResponse(BaseModel):
    """Structured error response model with machine-readable error_code."""

    error_code: ErrorCode
    message: str
    details: Any | None = None


class PaginationParams(BaseModel):
    """Unified pagination parameters for all list/search endpoints.

    Contract (NFM-4308 ③):

    * ``page_size`` is an accepted alias for ``per_page``. An explicit
      non-default ``per_page`` wins over the alias; when ``per_page`` is
      absent (or left at its default) ``page_size`` applies.
    * Values above :data:`MAX_PER_PAGE` are clamped to the cap and the
      request still succeeds — the effective page size is echoed in the
      response (``limit``) together with ``truncated: true`` so callers
      never silently miss rows.
    """

    page: int = Field(default=1, ge=1, description="页码")
    per_page: int = Field(
        default=DEFAULT_PER_PAGE,
        ge=1,
        description="每页数量(1-100;超限按 100 执行并以 truncated 回传)",
    )
    page_size: int | None = Field(
        default=None,
        ge=1,
        description="per_page 的别名(显式非默认 per_page 优先)",
    )

    _truncated: bool = PrivateAttr(default=False)

    def __init__(self, **data: Any) -> None:
        # FastAPI materialises every field default into the kwargs before
        # constructing the dependency, so "was per_page actually sent?"
        # cannot be detected by absence downstream. Drop a default-valued
        # per_page whenever the page_size alias is present so the alias
        # applies; an explicit non-default per_page always wins.
        if data.get("page_size") is not None and data.get("per_page") in (
            None,
            DEFAULT_PER_PAGE,
        ):
            data.pop("per_page", None)
        super().__init__(**data)

    @model_validator(mode="before")
    @classmethod
    def _apply_page_size_alias(cls, data: Any) -> Any:
        """Map the ``page_size`` alias onto ``per_page`` when unset."""
        if not isinstance(data, dict):
            return data
        if data.get("per_page") is None and data.get("page_size") is not None:
            return {**data, "per_page": data["page_size"]}
        return data

    @model_validator(mode="after")
    def _clamp_per_page(self) -> PaginationParams:
        """Clamp over-cap page sizes to MAX_PER_PAGE and record truncation."""
        if self.per_page > MAX_PER_PAGE:
            self.per_page = MAX_PER_PAGE
            self._truncated = True
        return self

    @property
    def truncated(self) -> bool:
        """True when the requested page size exceeded the cap and was clamped."""
        return self._truncated

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
    """Paginated data payload — intended to be wrapped in ``ApiResponse``.

    ``limit`` always echoes the *effective* page size; ``truncated`` is
    true when the caller asked for more than ``MAX_PER_PAGE`` rows and
    the cap was applied (NFM-4308 ③).
    """

    items: list[T]
    total: int
    page: int
    limit: int
    pages: int
    truncated: bool = False
