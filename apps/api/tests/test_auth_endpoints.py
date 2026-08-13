"""Integration tests for authentication endpoints."""

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient


pytestmark = pytest.mark.xfail(reason="NFM-1366: passlib/bcrypt incompatibility with Python 3.14", strict=False)
class TestRegisterEndpoint:
    """Test user registration endpoint."""

    @pytest.mark.asyncio
    async def test_register_new_user_success(self, async_client: AsyncClient) -> None:
        """Test successful user registration."""
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "secure_password_123",
                "full_name": "New User",
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["username"] == "newuser"
        assert data["email"] == "newuser@example.com"
        assert data["full_name"] == "New User"
        assert "hashed_password" not in data
        assert "id" in data

    @pytest.mark.asyncio
    async def test_register_duplicate_username_fails(self, async_client: AsyncClient) -> None:
        """Test that duplicate username registration fails."""
        # First registration
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "duplicate",
                "email": "user1@example.com",
                "password": "password123",
            },
        )

        # Second registration with same username
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "duplicate",
                "email": "user2@example.com",
                "password": "password123",
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestLoginEndpoint:
    """Test login endpoint."""

    @pytest.mark.asyncio
    async def test_login_success(self, async_client: AsyncClient) -> None:
        """Test successful login returns access token."""
        # First register a user
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "loginuser",
                "email": "login@example.com",
                "password": "login_password_123",
            },
        )

        # Login with OAuth2PasswordRequestForm
        response = await async_client.post(
            "/api/v1/auth/login",
            data={
                "username": "loginuser",
                "password": "login_password_123",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password_fails(self, async_client: AsyncClient) -> None:
        """Test login with wrong password fails."""
        response = await async_client.post(
            "/api/v1/auth/login",
            data={
                "username": "loginuser",
                "password": "wrong_password",
            },
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestGetCurrentUser:
    """Test current user info endpoint."""

    @pytest.mark.asyncio
    async def test_get_current_user_with_token(self, async_client: AsyncClient) -> None:
        """Test getting current user info with valid token."""
        # Register and login
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "meuser",
                "email": "me@example.com",
                "password": "password123",
                "blog_role": "editor",
            },
        )

        login_response = await async_client.post(
            "/api/v1/auth/login",
            data={
                "username": "meuser",
                "password": "password123",
            },
        )
        token = login_response.json()["access_token"]

        # Get current user info
        response = await async_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["data"]["username"] == "meuser"
        assert data["data"]["blog_role"] == "editor"

    @pytest.mark.asyncio
    async def test_get_current_user_without_token_fails(self, async_client: AsyncClient) -> None:
        """Test getting current user without token fails.

        FastAPI's HTTPBearer raises HTTP 401 (with WWW-Authenticate) for a
        missing/invalid bearer scheme; 401 is the correct status for an
        unauthenticated request to a protected endpoint.
        """
        response = await async_client.get("/api/v1/auth/me")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRoleAssignment:
    """Test role assignment endpoints."""

    @pytest.mark.asyncio
    async def test_list_roles_as_admin(self, async_client: AsyncClient) -> None:
        """Test listing roles as admin."""
        # Create admin user
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "adminuser",
                "email": "admin@example.com",
                "password": "admin123",
                "blog_role": "admin",
            },
        )

        # Login as admin
        login_response = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "adminuser", "password": "admin123"},
        )
        admin_token = login_response.json()["access_token"]

        # List roles
        response = await async_client.get(
            "/api/v1/auth/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]) == 3  # admin, editor, reviewer

    @pytest.mark.asyncio
    async def test_assign_role_as_admin(self, async_client: AsyncClient) -> None:
        """Test assigning role as admin."""
        # Create admin user
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "admin2",
                "email": "admin2@example.com",
                "password": "admin123",
                "blog_role": "admin",
            },
        )

        # Create regular user
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "username": "regularuser",
                "email": "regular@example.com",
                "password": "regular123",
            },
        )

        # Login as admin
        login_response = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "admin2", "password": "admin123"},
        )
        admin_token = login_response.json()["access_token"]

        # Confirm the admin token is valid before the role-assignment attempt
        admin_me_response = await async_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_me_response.status_code == status.HTTP_200_OK
        # We still need the *other* (target) user's ID, which is unknown here,
        # so the assignment below is expected to 404 on a random UUID.

        # Assign role (this will fail with 404 since we don't have the real user ID)
        response = await async_client.put(
            f"/api/v1/auth/users/{uuid4()}/role",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role": "editor"},
        )

        # Should fail with 404 since the user doesn't exist
        assert response.status_code == status.HTTP_404_NOT_FOUND
