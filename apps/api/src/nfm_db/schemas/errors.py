"""Standard ErrorCode enum and error response helpers (NFM-1090).

Provides a consistent error envelope for all API endpoints:
    {"success": false, "error_code": "<CODE>", "error": "<中文消息>", "detail": "..."}

This module is backward-compatible — it adds ``error_code`` to the existing
``success`` + ``error`` shape used throughout the codebase.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    """Standard error codes with Chinese messages for API responses."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    CONFLICT = "CONFLICT"
    BAD_REQUEST = "BAD_REQUEST"
    BATCH_IMPORT_ERROR = "BATCH_IMPORT_ERROR"

    @property
    def message(self) -> str:
        """Return the Chinese-language default message for this error code."""
        return _MESSAGES[self]

    @property
    def http_status(self) -> int:
        """Return the default HTTP status code for this error code."""
        return _HTTP_STATUS[self]


# ── Internal mappings ───────────────────────────────────────────────

_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "请求参数验证失败",
    ErrorCode.AUTH_REQUIRED: "需要身份认证",
    ErrorCode.FORBIDDEN: "权限不足，拒绝访问",
    ErrorCode.NOT_FOUND: "请求的资源不存在",
    ErrorCode.RATE_LIMIT_EXCEEDED: "请求频率超限，请稍后重试",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
    ErrorCode.CONFLICT: "资源冲突，操作无法完成",
    ErrorCode.BAD_REQUEST: "请求格式错误",
    ErrorCode.BATCH_IMPORT_ERROR: "批量导入失败",
}

_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RATE_LIMIT_EXCEEDED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.CONFLICT: 409,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.BATCH_IMPORT_ERROR: 422,
}

# Reverse lookup: HTTP status → ErrorCode (for the global handler)
_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.AUTH_REQUIRED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMIT_EXCEEDED,
    500: ErrorCode.INTERNAL_ERROR,
}


# ── Helper functions ────────────────────────────────────────────────


def build_error_response(
    code: ErrorCode,
    detail: str | None = None,
) -> JSONResponse:
    """Build a standard error ``JSONResponse``.

    Parameters
    ----------
    code:
        The ``ErrorCode`` determining status code and Chinese message.
    detail:
        Optional extra context (e.g. "Material ID 42 not found").

    Returns
    -------
    JSONResponse
        With body ``{"success": false, "error_code": ..., "error": ..., "detail": ...}``.
        The ``detail`` key is omitted when not provided.
    """
    body: dict[str, Any] = {
        "success": False,
        "error_code": code.value,
        "error": code.message,
    }
    if detail is not None:
        body["detail"] = detail

    return JSONResponse(status_code=code.http_status, content=body)


def register_http_exception_handler(application: FastAPI) -> None:
    """Register a global HTTPException handler that adds ``error_code``.

    This enriches *all* ``HTTPException`` responses with the standard error
    envelope without requiring changes to individual route handlers.
    """

    @application.exception_handler(HTTPException)
    async def _http_exception_handler(
        _request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        error_code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        body: dict[str, Any] = {
            "success": False,
            "error_code": error_code.value,
            "error": error_code.message,
        }
        if exc.detail is not None:
            body["detail"] = exc.detail

        return JSONResponse(
            status_code=exc.status_code,
            content=body,
        )
