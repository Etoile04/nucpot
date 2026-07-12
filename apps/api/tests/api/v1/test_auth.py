"""Integration tests for auth endpoints and auth dependency behavior.

Covers:
- POST /api/v1/auth/login
- POST /api/v1/auth/register
- GET  /api/v1/auth/me
- GET  /api/v1/auth/roles
- PUT  /api/v1/auth/users/{user_id}/role
- Auth dependency edge cases (missing token, inactive user, wrong role)

NOTE: The test environment has a passlib/bcrypt incompatibility (Python 3.14).
Tests that require password hashing use bcrypt directly and mock the
passlib-based auth_service functions at the endpoint layer.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import bcrypt
import pytest

from nfm_db.models.user import BlogRole, User
from nfm_db.services.auth_service import create_access_token

# ---------------------------------------------------------------------------
# Helpers — bypass passlib, use bcrypt directly
# ---------------------------------------------------------------------------

_HASHED_PW = bcrypt.hashpw(b"testpass123", bcrypt.gensalt(rounds=4)).decode()


def _hash_password(plaintext: str) -> str:
    """Hash a password using bcrypt directly (bypasses broken passlib)."""
    return bcrypt.hashpw(
        plaintext.encode("utf-8"),
        bcrypt.gensalt(rounds=4),
    ).decode()


def _verify_password_direct(plaintext: str, hashed: str) -> bool:
    """Verify a password using bcrypt directly (bypasses broken passlib)."""
    return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))


async def _seed_user(
    db_session,
    *,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpass123",
    blog_role: BlogRole | None = None,
    is_active: bool = True,
) -> tuple[User, str]:
    """Create and persist a user, returning (user, hashed_password).

    Uses bcrypt directly to avoid passlib/bcrypt compatibility issues.
    """
    hashed = _hash_password(password)
    user = User(
        username=username,
        email=email,
        hashed_password=hashed,
        blog_role=blog_role,
        is_active=is_active,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, hashed


def _auth_headers(user: User) -> dict[str, str]:
    """Build Authorization header for a given user."""
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# Shared patches for endpoint tests that go through the API auth flow.
# These patches replace the broken passlib calls in auth_endpoints.py.
def _auth_patches(
    mock_hash_return: str | None = None,
    mock_verify_return: bool = False,
):
    """Return a dict of patches for auth_endpoints module functions.

    Usage:
        with patch(...), patch(...):
            ...
    """
    h = mock_hash_return or _HASHED_PW
    return {
        "target": "nfm_db.api.v1.auth_endpoints.get_password_hash",
        "return_value": h,
    }, {
        "target": "nfm_db.api.v1.auth_endpoints.authenticate_user",
        "return_value": mock_verify_return,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/auth/login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(async_client, db_session) -> None:
    """Valid credentials return a bearer token."""
    user, _hashed = await _seed_user(db_session, username="logintest", password="secret123")

    with patch(
        "nfm_db.api.v1.auth_endpoints.authenticate_user",
        return_value=True,
    ):
        response = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "logintest", "password": "secret123"},
        )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_updates_last_login(async_client, db_session) -> None:
    """Successful login persists last_login timestamp."""
    user, _hashed = await _seed_user(db_session, username="lastlogin", password="pw")
    assert user.last_login is None

    with patch(
        "nfm_db.api.v1.auth_endpoints.authenticate_user",
        return_value=True,
    ):
        await async_client.post(
            "/api/v1/auth/login",
            data={"username": "lastlogin", "password": "pw"},
        )

    await db_session.refresh(user)
    assert user.last_login is not None


@pytest.mark.asyncio
async def test_login_wrong_password(async_client, db_session) -> None:
    """Incorrect password returns 401."""
    await _seed_user(db_session, username="wrongpw", password="correct")

    with patch(
        "nfm_db.api.v1.auth_endpoints.authenticate_user",
        return_value=False,
    ):
        response = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "wrongpw", "password": "incorrect"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(async_client, db_session) -> None:
    """Login with unknown username returns 401."""
    response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "ghost", "password": "nope"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_missing_fields(async_client) -> None:
    """Missing form fields return 422."""
    response = await async_client.post("/api/v1/auth/login", data={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/auth/register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_success(_mock_hash, async_client) -> None:
    """Registering a new user returns 201 with user data."""
    payload = {
        "username": "newuser",
        "email": "newuser@example.com",
        "password": "securepassword1",
        "full_name": "New User",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "newuser"
    assert body["email"] == "newuser@example.com"
    assert body["full_name"] == "New User"
    assert "id" in body
    assert body["is_active"] is True


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_with_role(_mock_hash, async_client) -> None:
    """Registering with a blog_role persists the role."""
    payload = {
        "username": "roleuser",
        "email": "role@example.com",
        "password": "securepassword1",
        "blog_role": "editor",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["blog_role"] == "editor"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_duplicate_username(_mock_hash, async_client, db_session) -> None:
    """Registering with an existing username returns 400."""
    # Seed user directly in DB (no password hashing needed)
    user = User(
        username="dup",
        email="dup@old.com",
        hashed_password=_HASHED_PW,
    )
    db_session.add(user)
    await db_session.commit()

    payload = {
        "username": "dup",
        "email": "dup@new.com",
        "password": "securepassword1",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 400
    body = response.json()
    assert "Username already exists" in body["detail"]


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_duplicate_email(_mock_hash, async_client, db_session) -> None:
    """Registering with an existing email returns 400."""
    user = User(
        username="emaildup",
        email="same@example.com",
        hashed_password=_HASHED_PW,
    )
    db_session.add(user)
    await db_session.commit()

    payload = {
        "username": "other",
        "email": "same@example.com",
        "password": "securepassword1",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 400
    body = response.json()
    assert "Email already exists" in body["detail"]


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_short_password_rejects(_mock_hash, async_client) -> None:
    """Password shorter than 8 chars returns 422."""
    payload = {
        "username": "shortpw",
        "email": "short@example.com",
        "password": "abc",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
async def test_register_missing_username_rejects(_mock_hash, async_client) -> None:
    """Missing username returns 422."""
    payload = {
        "email": "noname@example.com",
        "password": "securepassword1",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/auth/me — current user info (auth dependency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_current_user(async_client, admin_user) -> None:
    """Authenticated admin can fetch their own profile."""
    headers = _auth_headers(admin_user)
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["username"] == "admin"
    assert data["blog_role"] == "admin"


@pytest.mark.asyncio
async def test_me_no_token_returns_401(async_client) -> None:
    """Request without Authorization header returns 401."""
    response = await async_client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_invalid_token_returns_401(async_client) -> None:
    """Request with malformed JWT returns 401."""
    headers = {"Authorization": "Bearer invalid.token.here"}
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_token_for_nonexistent_user(async_client, db_session) -> None:
    """Token referencing a user not in DB returns 401."""
    fake_id = uuid.uuid4()
    token = create_access_token(data={"sub": str(fake_id)})
    headers = {"Authorization": f"Bearer {token}"}

    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_inactive_user_returns_403(async_client, db_session) -> None:
    """Inactive user gets 403 from get_current_active_user."""
    user, _hashed = await _seed_user(
        db_session,
        username="inactive",
        email="inactive@example.com",
        is_active=False,
    )
    headers = _auth_headers(user)

    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_me_missing_bearer_prefix(async_client) -> None:
    """Authorization header without 'Bearer' prefix returns 401/403."""
    headers = {"Authorization": "Token sometoken"}
    response = await async_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/auth/roles — admin only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roles_admin_success(async_client, admin_user) -> None:
    """Admin can list all blog roles."""
    headers = _auth_headers(admin_user)
    response = await async_client.get("/api/v1/auth/roles", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    roles = body["data"]
    assert len(roles) == 3
    role_values = {r["role"] for r in roles}
    assert role_values == {"admin", "editor", "reviewer"}


@pytest.mark.asyncio
async def test_roles_editor_forbidden(async_client, editor_user) -> None:
    """Editor gets 403 when trying to list roles."""
    headers = _auth_headers(editor_user)
    response = await async_client.get("/api/v1/auth/roles", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_roles_reviewer_forbidden(async_client, reviewer_user) -> None:
    """Reviewer gets 403 when trying to list roles."""
    headers = _auth_headers(reviewer_user)
    response = await async_client.get("/api/v1/auth/roles", headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_roles_no_token_returns_401(async_client) -> None:
    """Unauthenticated request to /roles returns 401."""
    response = await async_client.get("/api/v1/auth/roles")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_roles_response_structure(async_client, admin_user) -> None:
    """Each role entry has role, display_name, description, permissions."""
    headers = _auth_headers(admin_user)
    response = await async_client.get("/api/v1/auth/roles", headers=headers)
    body = response.json()
    role_entry = body["data"][0]
    assert "role" in role_entry
    assert "display_name" in role_entry
    assert "description" in role_entry
    assert "permissions" in role_entry


# ---------------------------------------------------------------------------
# PUT /api/v1/auth/users/{user_id}/role — admin only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_role_success(async_client, admin_user, db_session) -> None:
    """Admin can assign a role to another user."""
    target, _hashed = await _seed_user(
        db_session, username="targetrole", email="target@example.com"
    )
    headers = _auth_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "editor"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["username"] == "targetrole"
    assert data["previous_role"] is None
    assert data["new_role"] == "editor"
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_assign_role_change_existing(async_client, admin_user, db_session) -> None:
    """Admin can change a user's existing role."""
    target, _hashed = await _seed_user(
        db_session,
        username="changerole",
        email="change@example.com",
        blog_role=BlogRole.EDITOR,
    )
    headers = _auth_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "reviewer"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["previous_role"] == "editor"
    assert data["new_role"] == "reviewer"


@pytest.mark.asyncio
async def test_assign_role_remove_role(async_client, admin_user, db_session) -> None:
    """Admin can remove a role by passing null."""
    target, _hashed = await _seed_user(
        db_session,
        username="removerole",
        email="remove@example.com",
        blog_role=BlogRole.REVIEWER,
    )
    headers = _auth_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": None},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["previous_role"] == "reviewer"
    assert data["new_role"] is None


@pytest.mark.asyncio
async def test_assign_role_nonexistent_user(async_client, admin_user) -> None:
    """Assigning role to nonexistent user returns 404."""
    headers = _auth_headers(admin_user)
    fake_id = uuid.uuid4()

    response = await async_client.put(
        f"/api/v1/auth/users/{fake_id}/role",
        json={"role": "editor"},
        headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_assign_role_editor_forbidden(async_client, editor_user, db_session) -> None:
    """Editor cannot assign roles (403)."""
    target, _hashed = await _seed_user(
        db_session, username="forbidtarget", email="forbid@example.com"
    )
    headers = _auth_headers(editor_user)

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "reviewer"},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_assign_role_no_token_returns_401(async_client, db_session) -> None:
    """Unauthenticated role assignment returns 401."""
    target, _hashed = await _seed_user(db_session, username="unauth", email="unauth@example.com")

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "editor"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_assign_role_invalid_uuid_returns_422(async_client, admin_user) -> None:
    """Non-UUID user_id returns 422."""
    headers = _auth_headers(admin_user)

    response = await async_client.put(
        "/api/v1/auth/users/not-a-uuid/role",
        json={"role": "editor"},
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_assign_role_invalid_role_value(async_client, admin_user, db_session) -> None:
    """Invalid role value returns 422."""
    target, _hashed = await _seed_user(db_session, username="badrole", email="badrole@example.com")
    headers = _auth_headers(admin_user)

    response = await async_client.put(
        f"/api/v1/auth/users/{target.id}/role",
        json={"role": "superadmin"},
        headers=headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Login + Me integration: full auth flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_then_me_flow(async_client, db_session) -> None:
    """Login returns token, then /me with that token returns user info."""
    await _seed_user(db_session, username="flowuser", password="flowpass")

    with patch(
        "nfm_db.api.v1.auth_endpoints.authenticate_user",
        return_value=True,
    ):
        login_resp = await async_client.post(
            "/api/v1/auth/login",
            data={"username": "flowuser", "password": "flowpass"},
        )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    me_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["username"] == "flowuser"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.auth_endpoints.get_password_hash", return_value=_HASHED_PW)
@patch("nfm_db.api.v1.auth_endpoints.authenticate_user", return_value=True)
async def test_register_then_login(_mock_auth, _mock_hash, async_client, db_session) -> None:
    """Register a user, then login with the same credentials."""
    register_payload = {
        "username": "reglogin",
        "email": "reglogin@example.com",
        "password": "mypassword1",
    }
    reg_resp = await async_client.post("/api/v1/auth/register", json=register_payload)
    assert reg_resp.status_code == 201

    login_resp = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "reglogin", "password": "mypassword1"},
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()
