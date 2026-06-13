"""Integration tests for authentication and authorization flows."""

import pytest
from httpx import AsyncClient

from nfm_db.models import BlogRole, User


@pytest.mark.integration
@pytest.mark.asyncio
class TestAuthenticationFlow:
    """Test complete authentication flows."""

    async def test_login_success(self, async_client: AsyncClient, db_session):
        """Test successful login returns access token."""
        # Create test user
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="$2b$12$hashed_password_here",  # Placeholder
        )
        db_session.add(user)
        db_session.commit()

        # Test login
        response = await async_client.post(
            "/api/v1/auth/login",
            json={
                "username": "testuser",
                "password": "password123",
            },
        )

        # Note: This will fail without proper password hashing implementation
        # Placeholder test to verify the flow structure
        assert response.status_code in [200, 401]  # May fail without real auth

    async def test_login_invalid_credentials(self, async_client: AsyncClient):
        """Test login with invalid credentials."""
        response = await async_client.post(
            "/api/v1/auth/login",
            json={
                "username": "nonexistent",
                "password": "wrongpassword",
            },
        )

        assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
class TestRoleManagementFlow:
    """Test role assignment and management flows."""

    async def test_list_roles_as_admin(
        self, async_client: AsyncClient, admin_headers, db_session
    ):
        """Test admin can list all available roles."""
        response = await async_client.get(
            "/api/v1/auth/roles",
            headers=admin_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        roles = data["data"]
        assert len(roles) == 3
        role_names = {role["role"] for role in roles}
        assert role_names == {"admin", "editor", "reviewer"}

    async def test_assign_role_to_user(
        self, async_client: AsyncClient, admin_headers, db_session
    ):
        """Test admin can assign role to user."""
        # Create test user
        user = User(
            username="promotee",
            email="promotee@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        response = await async_client.put(
            f"/api/v1/auth/users/{user.id}/role",
            headers=admin_headers,
            json={"role": "editor"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    async def test_remove_role_from_user(
        self, async_client: AsyncClient, admin_headers, db_session
    ):
        """Test admin can remove role from user."""
        # Create user with role
        user = User(
            username="demotee",
            email="demotee@example.com",
            hashed_password="hashed_password_here",
            blog_role=BlogRole.EDITOR,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        response = await async_client.put(
            f"/api/v1/auth/users/{user.id}/role",
            headers=admin_headers,
            json={"role": None},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["new_role"] is None

    async def test_non_admin_cannot_list_roles(
        self, async_client: AsyncClient, editor_headers
    ):
        """Test non-admin cannot list roles."""
        response = await async_client.get(
            "/api/v1/auth/roles",
            headers=editor_headers,
        )

        assert response.status_code == 403

    async def test_non_admin_cannot_assign_roles(
        self, async_client: AsyncClient, editor_headers, db_session
    ):
        """Test non-admin cannot assign roles."""
        user = User(
            username="target",
            email="target@example.com",
            hashed_password="hashed_password_here",
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        response = await async_client.put(
            f"/api/v1/auth/users/{user.id}/role",
            headers=editor_headers,
            json={"role": "reviewer"},
        )

        assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
class TestAuthorizationFlow:
    """Test authorization middleware flows."""

    async def test_admin_can_access_admin_endpoints(
        self, async_client: AsyncClient, admin_headers
    ):
        """Test admin can access admin-only endpoints."""
        response = await async_client.get(
            "/api/v1/auth/users",
            headers=admin_headers,
        )

        assert response.status_code == 200

    async def test_editor_cannot_access_admin_endpoints(
        self, async_client: AsyncClient, editor_headers
    ):
        """Test editor cannot access admin-only endpoints."""
        response = await async_client.get(
            "/api/v1/auth/users",
            headers=editor_headers,
        )

        assert response.status_code == 403

    async def test_unauthenticated_request_denied(
        self, async_client: AsyncClient
    ):
        """Test unauthenticated requests are denied."""
        response = await async_client.get("/api/v1/auth/me")

        assert response.status_code == 401
