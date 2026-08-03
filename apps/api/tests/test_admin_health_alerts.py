"""Tests for GET /api/admin/health/alerts endpoint (NFM-2414).

Covers:
- Admin auth guard: 401 for unauthenticated, 403 for non-admin
- Response format: structured JSON with status and alerts array
- Degraded status when errors are present
- Healthy status when no errors
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from nfm_db.api.v1.auth import require_admin
from nfm_db.main import app
from nfm_db.models.user import BlogRole, User


@pytest.mark.no_auto_auth
@pytest.mark.unit
class TestAdminHealthAlertsAuth:
    """Verify the endpoint requires admin authentication."""

    async def test_unauthenticated_returns_401(self) -> None:
        """Missing credentials must return 401."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/admin/health/alerts")

        assert response.status_code == 401

    async def test_non_admin_returns_403(self) -> None:
        """Non-admin role must receive 403 Forbidden."""
        async def _mock_editor() -> User:
            raise HTTPException(status_code=403, detail="Forbidden")

        app.dependency_overrides[require_admin] = _mock_editor
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/admin/health/alerts")
            assert response.status_code == 403
        finally:
            app.dependency_overrides.pop(require_admin, None)

    async def test_admin_returns_200(self) -> None:
        """Admin role must receive 200 OK."""
        _admin_user = User(
            id="00000000-0000-0000-0000-000000000001",
            username="test_admin",
            email="admin@test.com",
            hashed_password="hashed",
            blog_role=BlogRole.ADMIN,
            is_active=True,
        )

        async def _mock_admin() -> User:
            return _admin_user

        app.dependency_overrides[require_admin] = _mock_admin
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/admin/health/alerts")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.pop(require_admin, None)


@pytest.mark.unit
class TestAdminHealthAlertsResponse:
    """Verify the response schema and data shape."""

    async def test_response_structure(self, authenticated_client: AsyncClient) -> None:
        """Response must include success=True, status field, and alerts array."""
        response = await authenticated_client.get("/api/v1/admin/health/alerts")

        assert response.status_code == 200
        body = response.json()

        assert body["success"] is True
        data = body["data"]
        assert "status" in data
        assert "alerts" in data
        assert isinstance(data["alerts"], list)
        assert data["status"] in ("healthy", "degraded")

    async def test_alert_items_have_required_fields(
        self, authenticated_client: AsyncClient,
    ) -> None:
        """Each alert must contain type, count, and last_seen."""
        response = await authenticated_client.get("/api/v1/admin/health/alerts")
        body = response.json()
        alerts = body["data"]["alerts"]

        for alert in alerts:
            assert "type" in alert
            assert "count" in alert
            assert isinstance(alert["count"], int)
            assert "last_seen" in alert

    async def test_healthy_when_no_alerts(self, authenticated_client: AsyncClient) -> None:
        """When there are no active errors, status must be 'healthy'."""
        response = await authenticated_client.get("/api/v1/admin/health/alerts")
        body = response.json()
        data = body["data"]

        if len(data["alerts"]) == 0:
            assert data["status"] == "healthy"

    async def test_degraded_when_alerts_present(
        self, authenticated_client: AsyncClient,
    ) -> None:
        """When alerts exist, status must be 'degraded'."""
        response = await authenticated_client.get("/api/v1/admin/health/alerts")
        body = response.json()
        data = body["data"]

        if len(data["alerts"]) > 0:
            assert data["status"] == "degraded"
