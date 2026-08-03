"""Tests for POST /api/v1/auth/refresh — sliding-window session extension.

NFM-2236 AC: the frontend calls /auth/refresh before the access cookie
expires. The endpoint must:
  - return 401 when no valid cookie is present (so the frontend surfaces
    an explicit re-auth prompt rather than silently failing),
  - return 200 with a new access_token cookie AND a JSON body containing
    ``expires_at`` (so JS, which cannot read the HttpOnly cookie, can
    schedule the next refresh),
  - the new JWT must be different from the old one (otherwise refresh
    would be a no-op),
  - the new ``expires_at`` must lie within the cookie max-age window of
    now (so the client knows when the next refresh should fire).

These tests are currently expected to fail at the bcrypt layer
(NFM-1366 — passlib/bcrypt incompatibility with Python 3.14) on the
``test_login_sets_cookie`` path. Once that issue is resolved, this
file passes alongside the existing ``test_auth_endpoints.py`` module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from nfm_db.models import User
from nfm_db.services.auth_service import (
    create_access_token,
    get_password_hash,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.no_auto_auth]


async def _register_and_login(
    async_client: AsyncClient,
    db_session,
    *,
    username: str = "refreshuser",
    email: str = "refresh@example.com",
    password: str = "refresh_pwd_123",
) -> str:
    """Create a user via direct DB insert and return the access cookie value."""
    user = User(
        id=uuid4(),
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    cookie = response.cookies.get("access_token")
    assert cookie, "login did not set access_token cookie"
    return cookie


class TestAuthRefresh:
    """POST /api/v1/auth/refresh contract."""

    async def test_refresh_without_cookie_returns_401(self, async_client: AsyncClient) -> None:
        """No cookie → 401 (frontend will then show re-auth prompt)."""
        response = await async_client.post("/api/v1/auth/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert "detail" in body

    async def test_refresh_with_invalid_cookie_returns_401(self, async_client: AsyncClient) -> None:
        """A tampered cookie → 401, not 500."""
        async_client.cookies.set("access_token", "not.a.real.jwt")
        response = await async_client.post("/api/v1/auth/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_refresh_with_expired_jwt_returns_401(
        self, async_client: AsyncClient, db_session
    ) -> None:
        """JWT whose `exp` is in the past → 401 (no sliding window for already-expired tokens)."""
        user = User(
            id=uuid4(),
            username="expireduser",
            email="expired@example.com",
            hashed_password=get_password_hash("expired_pwd_123"),
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        # Token that expired an hour ago.
        expired = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=timedelta(hours=-1),
        )
        async_client.cookies.set("access_token", expired)

        response = await async_client.post("/api/v1/auth/refresh")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    async def test_refresh_with_valid_cookie_returns_new_token_and_expiry(
        self, async_client: AsyncClient, db_session
    ) -> None:
        """Happy path: new cookie set + JSON body with new access_token + expires_at."""
        original_cookie = await _register_and_login(async_client, db_session)

        # Defensive: explicitly set the captured value on the client.
        async_client.cookies.set("access_token", original_cookie)

        before = datetime.now(UTC)
        response = await async_client.post("/api/v1/auth/refresh")
        after = datetime.now(UTC)

        assert response.status_code == status.HTTP_200_OK, response.text

        body = response.json()
        assert "access_token" in body, body
        assert "expires_at" in body, body

        # 1. New JWT must differ from the original.
        assert body["access_token"] != original_cookie

        # 2. expires_at must parse as ISO 8601 and lie in [before, after + max_age + slack].
        expires_at = datetime.fromisoformat(body["expires_at"])
        assert expires_at.tzinfo is not None  # timezone-aware

        # COOKIE_MAX_AGE = 1800 s in auth_endpoints.py — give 60 s slack
        # so a slow CI runner doesn't flake this assertion.
        max_window = max(after + timedelta(seconds=1800 + 60), before)
        assert before <= expires_at <= max_window, (
            f"expires_at {expires_at} not in [{before}, {max_window}]"
        )

        # 3. New Set-Cookie header must be present with HttpOnly + Max-Age=1800.
        set_cookie = response.headers.get("set-cookie", "")
        assert "access_token=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Max-Age=1800" in set_cookie

    async def test_refresh_is_idempotent_across_repeated_calls(
        self, async_client: AsyncClient, db_session
    ) -> None:
        """Two consecutive refreshes must each return a valid token (refresh does not revoke)."""
        login_cookie = await _register_and_login(async_client, db_session)
        async_client.cookies.set("access_token", login_cookie)

        first = await async_client.post("/api/v1/auth/refresh")
        assert first.status_code == status.HTTP_200_OK, first.text
        first_cookie = (
            first.cookies.get("access_token")
            or first.headers.get("set-cookie", "").split("access_token=")[1].split(";")[0]
        )
        # Defensive: ASGI test clients don't always propagate the
        # response cookie back to the request jar, so explicitly mirror
        # the new cookie before the second call.
        async_client.cookies.set("access_token", first_cookie)

        second = await async_client.post("/api/v1/auth/refresh")
        assert second.status_code == status.HTTP_200_OK, second.text

        # Each refresh mints a fresh JWT (different `jti` claim).
        assert first.json()["access_token"] != second.json()["access_token"]
