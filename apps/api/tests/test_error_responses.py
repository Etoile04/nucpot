"""Tests for standard ErrorCode enum and error response format (NFM-1090).

Verifies:
- ErrorCode enum members and Chinese message mappings
- build_error_response produces correct JSON shape
- Global HTTPException handler enriches responses with error_code
- Backward compatibility (existing 'error' field preserved)
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

# Direct file-based import to bypass schemas/__init__.py which has
# broken transitive imports from other unreleased modules (nfm_db.models.kg).
_schemas_dir = Path(__file__).resolve().parent.parent / "src" / "nfm_db" / "schemas"
_spec = importlib.util.spec_from_file_location(
    "nfm_db.schemas.errors",
    str(_schemas_dir / "errors.py"),
    submodule_search_locations=[],
)
_errors_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_errors_mod)

ErrorCode = _errors_mod.ErrorCode  # type: ignore[attr-defined]
build_error_response = _errors_mod.build_error_response  # type: ignore[attr-defined]
register_http_exception_handler = _errors_mod.register_http_exception_handler  # type: ignore[attr-defined]


# ── ErrorCode enum tests ────────────────────────────────────────────


class TestErrorCodeEnum:
    """Verify enum members, values, and Chinese messages."""

    EXPECTED_MEMBERS = [
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.AUTH_REQUIRED,
        ErrorCode.FORBIDDEN,
        ErrorCode.NOT_FOUND,
        ErrorCode.RATE_LIMIT_EXCEEDED,
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.CONFLICT,
        ErrorCode.BAD_REQUEST,
        ErrorCode.BATCH_IMPORT_ERROR,
        ErrorCode.REQUEST_ENTITY_TOO_LARGE,
    ]

    @pytest.mark.parametrize("member", EXPECTED_MEMBERS)
    def test_enum_member_exists(self, member: ErrorCode) -> None:
        assert member in list(ErrorCode)

    def test_total_member_count(self) -> None:
        assert len(ErrorCode) >= 10

    def test_values_are_strings(self) -> None:
        for member in ErrorCode:
            assert isinstance(member.value, str)

    def test_values_are_upper_snake_case(self) -> None:
        for member in ErrorCode:
            assert member.value == member.value.upper()
            assert member.value.isupper()

    def test_chinese_message_mapping(self) -> None:
        """Each ErrorCode must have a non-empty Chinese message."""
        for member in ErrorCode:
            msg = member.message
            assert isinstance(msg, str)
            assert len(msg) > 0, f"{member.name} has empty message"
            # Should contain CJK characters
            assert any("一" <= ch <= "鿿" for ch in msg), (
                f"{member.name}.message is not Chinese: {msg}"
            )

    def test_message_uniqueness(self) -> None:
        """Each error code should have a distinct Chinese message."""
        messages = [member.message for member in ErrorCode]
        assert len(messages) == len(set(messages)), "Duplicate Chinese messages found"


# ── build_error_response helper tests ───────────────────────────────


class TestBuildErrorResponse:
    """Verify the standard error response helper."""

    def test_returns_json_response(self) -> None:
        response = build_error_response(ErrorCode.NOT_FOUND)
        assert hasattr(response, "status_code")
        assert hasattr(response, "body")

    def test_status_code_not_found(self) -> None:
        response = build_error_response(ErrorCode.NOT_FOUND)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_body_structure_without_detail(self) -> None:
        response = build_error_response(ErrorCode.NOT_FOUND)
        data = json.loads(response.body)

        assert data["success"] is False
        assert data["error_code"] == "NOT_FOUND"
        assert data["error"] == ErrorCode.NOT_FOUND.message
        assert "detail" not in data

    @pytest.mark.asyncio
    async def test_body_structure_with_detail(self) -> None:
        response = build_error_response(
            ErrorCode.VALIDATION_ERROR,
            detail="材料名称不能为空",
        )
        data = json.loads(response.body)

        assert data["success"] is False
        assert data["error_code"] == "VALIDATION_ERROR"
        assert data["error"] == ErrorCode.VALIDATION_ERROR.message
        assert data["detail"] == "材料名称不能为空"

    @pytest.mark.asyncio
    async def test_backward_compatible_error_field(self) -> None:
        """Existing 'error' field must be preserved."""
        response = build_error_response(ErrorCode.BAD_REQUEST)
        data = json.loads(response.body)

        assert "error" in data
        assert isinstance(data["error"], str)

    @pytest.mark.asyncio
    async def test_status_code_mapping(self) -> None:
        """Each ErrorCode maps to the correct HTTP status code."""
        expected: dict[ErrorCode, int] = {
            ErrorCode.VALIDATION_ERROR: 422,
            ErrorCode.AUTH_REQUIRED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.RATE_LIMIT_EXCEEDED: 429,
            ErrorCode.INTERNAL_ERROR: 500,
            ErrorCode.CONFLICT: 409,
            ErrorCode.BAD_REQUEST: 400,
            ErrorCode.BATCH_IMPORT_ERROR: 422,
            ErrorCode.REQUEST_ENTITY_TOO_LARGE: 413,
        }
        for code, status in expected.items():
            response = build_error_response(code)
            assert response.status_code == status, (
                f"{code.name} should map to {status}, got {response.status_code}"
            )

    @pytest.mark.asyncio
    async def test_json_serializable(self) -> None:
        """Response body must be valid JSON."""
        response = build_error_response(ErrorCode.CONFLICT, detail="test detail")
        data = json.loads(response.body)
        assert isinstance(data, dict)


# ── Global HTTPException handler integration tests ──────────────────


class TestHttpExceptionHandlerIntegration:
    """Verify the global handler enriches HTTPException with error_code."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Minimal app with the global HTTPException handler registered."""
        application = FastAPI()
        register_http_exception_handler(application)

        @application.get("/not-found")
        async def not_found() -> None:
            raise HTTPException(status_code=404, detail="Material not found")

        @application.get("/bad-request")
        async def bad_request() -> None:
            raise HTTPException(status_code=400, detail="Invalid input")

        @application.get("/forbidden")
        async def forbidden() -> None:
            raise HTTPException(status_code=403, detail="Access denied")

        @application.get("/conflict")
        async def conflict() -> None:
            raise HTTPException(status_code=409, detail="Resource conflict")

        @application.get("/auth-required")
        async def auth_required() -> None:
            raise HTTPException(status_code=401, detail="Authentication required")

        @application.get("/rate-limited")
        async def rate_limited() -> None:
            raise HTTPException(status_code=429, detail="Too many requests")

        @application.get("/internal-error")
        async def internal_error() -> None:
            raise HTTPException(status_code=500, detail="Internal server error")

        return application

    @pytest.fixture
    def client(self, app: FastAPI) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_404_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/not-found")
        data = response.json()

        assert response.status_code == 404
        assert data["success"] is False
        assert data["error_code"] == "NOT_FOUND"
        assert "error" in data
        assert data["detail"] == "Material not found"

    @pytest.mark.asyncio
    async def test_400_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/bad-request")
        data = response.json()

        assert response.status_code == 400
        assert data["success"] is False
        assert data["error_code"] == "BAD_REQUEST"

    @pytest.mark.asyncio
    async def test_403_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/forbidden")
        data = response.json()

        assert response.status_code == 403
        assert data["success"] is False
        assert data["error_code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_409_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/conflict")
        data = response.json()

        assert response.status_code == 409
        assert data["success"] is False
        assert data["error_code"] == "CONFLICT"

    @pytest.mark.asyncio
    async def test_401_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/auth-required")
        data = response.json()

        assert response.status_code == 401
        assert data["success"] is False
        assert data["error_code"] == "AUTH_REQUIRED"

    @pytest.mark.asyncio
    async def test_429_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/rate-limited")
        data = response.json()

        assert response.status_code == 429
        assert data["success"] is False
        assert data["error_code"] == "RATE_LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_500_has_error_code(self, client: AsyncClient) -> None:
        response = await client.get("/internal-error")
        data = response.json()

        assert response.status_code == 500
        assert data["success"] is False
        assert data["error_code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_backward_compatible_error_field(self, client: AsyncClient) -> None:
        """The 'error' field with Chinese message must exist."""
        response = await client.get("/not-found")
        data = response.json()

        assert "error" in data
        assert isinstance(data["error"], str)
        assert len(data["error"]) > 0

    @pytest.mark.asyncio
    async def test_unknown_status_code_falls_back_to_internal(self, client: AsyncClient) -> None:
        """Status codes without explicit mapping should get INTERNAL_ERROR."""
        application = FastAPI()
        register_http_exception_handler(application)

        @application.get("/unknown")
        async def unknown() -> None:
            raise HTTPException(status_code=502, detail="Bad gateway")

        test_client = AsyncClient(transport=ASGITransport(app=application), base_url="http://test")
        response = await test_client.get("/unknown")
        data = response.json()

        assert response.status_code == 502
        assert data["success"] is False
        assert data["error_code"] == "INTERNAL_ERROR"
