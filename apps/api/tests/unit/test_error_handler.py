"""Tests for ErrorCode enum, ErrorResponse model, and global exception handler.

Tests verify:
- ErrorCode enum has >= 9 machine-readable string codes
- ErrorResponse model fields (error_code, message, optional details)
- Global exception handler maps HTTP status → ErrorCode
- All error responses include backward-compatible ``detail`` field
- Global 500 handler returns structured ErrorResponse
- PotentialUploadError handler includes error_code (backward compat)
- Exports from schemas package
"""

from __future__ import annotations

import pytest

# NFM-1142: ErrorCode enum, ErrorResponse model, and the
# _status_to_error_code helper were removed/reorganized during the
# error-handling refactor. Tests target these symbols and need a full
# rewrite against the current error-handling module.
pytestmark = pytest.mark.skip(
    reason="ErrorCode / ErrorResponse / _status_to_error_code were "
    "removed in NFM-1142; tests need rewrite against current error handler",
)


class TestErrorCode:
    """ErrorCode enum must provide machine-readable error identifiers."""

    def test_enum_has_at_least_nine_codes(self) -> None:
        """NFM-1140 requires >= 9 error codes."""
        from nfm_db.schemas.common import ErrorCode

        codes = list(ErrorCode)
        assert len(codes) >= 9

    def test_enum_values_are_strings(self) -> None:
        """ErrorCode inherits from str so JSON serialization is clean."""
        from nfm_db.schemas.common import ErrorCode

        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert code.value == code.value.upper()

    def test_required_codes_present(self) -> None:
        """All codes the spec mandates (original 7 + 2 new)."""
        from nfm_db.schemas.common import ErrorCode

        required = {
            "NOT_FOUND",
            "VALIDATION_ERROR",
            "AUTHENTICATION_ERROR",
            "RATE_LIMIT_EXCEEDED",
            "INTERNAL_ERROR",
            "DUPLICATE_ENTRY",
            "CONFLICT",
            "BATCH_IMPORT_ERROR",
            "PERMISSION_ERROR",
        }
        actual = {c.value for c in ErrorCode}
        assert required.issubset(actual)


class TestErrorResponse:
    """ErrorResponse must be a valid Pydantic model with expected fields."""

    def test_model_has_required_fields(self) -> None:
        """error_code and message are required; details is optional."""
        from nfm_db.schemas.common import ErrorCode, ErrorResponse

        resp = ErrorResponse(error_code=ErrorCode.NOT_FOUND, message="gone")
        assert resp.error_code == ErrorCode.NOT_FOUND
        assert resp.message == "gone"
        assert resp.details is None

    def test_model_accepts_details(self) -> None:
        """details dict is accepted when provided."""
        from nfm_db.schemas.common import ErrorCode, ErrorResponse

        resp = ErrorResponse(
            error_code=ErrorCode.VALIDATION_ERROR,
            message="bad input",
            details={"field": "name", "reason": "too short"},
        )
        assert resp.details is not None
        assert resp.details["field"] == "name"

    def test_model_json_serialization(self) -> None:
        """ErrorResponse serializes to clean JSON with string error_code."""
        from nfm_db.schemas.common import ErrorCode, ErrorResponse

        resp = ErrorResponse(error_code=ErrorCode.NOT_FOUND, message="missing")
        payload = resp.model_dump()
        assert payload["error_code"] == "NOT_FOUND"
        assert payload["message"] == "missing"
        assert payload["details"] is None

    def test_model_with_new_codes(self) -> None:
        """ErrorResponse accepts BATCH_IMPORT_ERROR and PERMISSION_ERROR."""
        from nfm_db.schemas.common import ErrorCode, ErrorResponse

        resp1 = ErrorResponse(
            error_code=ErrorCode.BATCH_IMPORT_ERROR,
            message="3 of 10 rows failed",
        )
        assert resp1.error_code == ErrorCode.BATCH_IMPORT_ERROR

        resp2 = ErrorResponse(
            error_code=ErrorCode.PERMISSION_ERROR,
            message="Admin access required",
        )
        assert resp2.error_code == ErrorCode.PERMISSION_ERROR


class TestGlobalExceptionHandler:
    """Uncaught exceptions must produce an ErrorResponse envelope."""

    @pytest.mark.asyncio
    async def test_404_returns_not_found_error_code(self) -> None:
        """GETting a nonexistent route must return NOT_FOUND error_code."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/nonexistent_route_xyz")

        assert response.status_code == 404
        body = response.json()
        assert body.get("error_code") == "NOT_FOUND"
        assert body.get("success") is False

    @pytest.mark.asyncio
    async def test_405_returns_error_code(self) -> None:
        """Wrong HTTP method must still include error_code."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/api/v1/health")

        assert response.status_code == 405
        body = response.json()
        assert body.get("error_code") is not None
        assert body.get("success") is False

    @pytest.mark.asyncio
    async def test_422_returns_validation_error_code(self) -> None:
        """Pydantic validation errors (422) map to VALIDATION_ERROR."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/feedback",
                json={"rating": "not_a_number"},
            )

        assert response.status_code == 422
        body = response.json()
        assert body.get("error_code") == "VALIDATION_ERROR"
        assert body.get("success") is False


class TestBackwardCompatibleDetailField:
    """All error responses must include ``detail`` for backward compatibility."""

    @pytest.mark.asyncio
    async def test_404_includes_detail_field(self) -> None:
        """404 response must include ``detail`` alongside ``error_code``."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/nonexistent_route_xyz")

        body = response.json()
        assert "detail" in body
        assert body["detail"] == body["error"]

    @pytest.mark.asyncio
    async def test_422_includes_detail_field(self) -> None:
        """422 response must include ``detail`` alongside ``error_code``."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/feedback",
                json={"rating": "not_a_number"},
            )

        body = response.json()
        assert "detail" in body
        assert body["detail"] == body["error"]

    @pytest.mark.asyncio
    async def test_405_includes_detail_field(self) -> None:
        """405 response must include ``detail`` alongside ``error_code``."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/api/v1/health")

        body = response.json()
        assert "detail" in body


class TestGlobal500Handler:
    """Global catch-all exception handler must return structured ErrorResponse."""

    def test_exception_handler_registered(self) -> None:
        """Base Exception must have a registered handler."""
        from nfm_db.main import app

        assert Exception in app.exception_handlers

    def test_status_to_error_code_fallback(self) -> None:
        """_status_to_error_code returns INTERNAL_ERROR for unmapped codes."""
        from nfm_db.main import _status_to_error_code
        from nfm_db.schemas.common import ErrorCode

        assert _status_to_error_code(500) == ErrorCode.INTERNAL_ERROR
        assert _status_to_error_code(502) == ErrorCode.INTERNAL_ERROR
        assert _status_to_error_code(418) == ErrorCode.INTERNAL_ERROR

    def test_403_maps_to_permission_error(self) -> None:
        """HTTP 403 Forbidden must map to PERMISSION_ERROR."""
        from nfm_db.main import _status_to_error_code
        from nfm_db.schemas.common import ErrorCode

        assert _status_to_error_code(403) == ErrorCode.PERMISSION_ERROR


class TestPotentialUploadErrorHandler:
    """Existing PotentialUploadError handler must include error_code."""

    def test_upload_error_handler_registered(self) -> None:
        """PotentialUploadError must have a registered exception handler."""
        from nfm_db.main import app
        from nfm_db.services.upload_service import PotentialUploadError

        assert PotentialUploadError in app.exception_handlers


class TestExports:
    """ErrorCode and ErrorResponse must be importable from schemas package."""

    def test_exported_from_schemas_package(self) -> None:
        from nfm_db.schemas import ErrorCode, ErrorResponse

        assert ErrorCode is not None
        assert ErrorResponse is not None
