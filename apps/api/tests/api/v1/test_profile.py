"""Tests for profile endpoints — focused on change-password (NFM-3489).

Covers:
- POST /api/v1/auth/change-password  (happy path, hash lands in DB)
- 401 on wrong current password
- 400 on weak new password (strength validator)
- 422 on new_password below schema min_length
- 401 without auth token (real auth chain, no_auto_auth)

NOTE on the conftest auto-auth: unmarked tests get a global
``get_current_active_user`` override returning an *in-memory* user, which
endpoint mutations would never persist.  Following the conftest's own
guidance ("tests deliberately set tighter overrides in their test bodies
after the fixture runs"), each test here seeds a real user row and
re-overrides the dependency to return that DB-attached instance.

NOTE: The test environment has a passlib/bcrypt incompatibility (Python 3.14).
Password hashing here uses bcrypt directly and mocks the passlib-based
auth_service functions at the endpoint layer, mirroring test_auth.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import bcrypt
import pytest

from nfm_db.models.user import User


def _hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode()


async def _seed_user(db_session, *, username: str) -> User:
    """Seed a unique user row and return the DB-attached instance."""
    user = User(
        id=uuid.uuid4(),
        username=username,
        email=f"{username}@example.com",
        hashed_password=_hash_password("OldPass123"),
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _act_as(user: User) -> None:
    """Point the auth dependency at the given DB-attached user."""
    from nfm_db.api.v1.auth import get_current_active_user
    from nfm_db.main import app

    async def _current() -> User:
        return user

    app.dependency_overrides[get_current_active_user] = _current


# ---------------------------------------------------------------------------
# POST /api/v1/auth/change-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_success(async_client, db_session) -> None:
    """Happy path: correct current password updates the stored hash."""
    user = await _seed_user(db_session, username="chg_ok")
    _act_as(user)
    old_hash = user.hashed_password

    new_hash = _hash_password("NewPass456")
    with (
        patch(
            "nfm_db.api.v1.profile.authenticate_user",
            return_value=True,
        ),
        patch(
            "nfm_db.api.v1.profile.get_password_hash",
            return_value=new_hash,
        ),
    ):
        response = await async_client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "OldPass123", "new_password": "NewPass456"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    fresh = await db_session.get(User, user.id)
    assert fresh.hashed_password == new_hash
    assert fresh.hashed_password != old_hash


@pytest.mark.asyncio
async def test_change_password_wrong_current(async_client, db_session) -> None:
    """Wrong current password → 401 and the stored hash is untouched."""
    user = await _seed_user(db_session, username="chg_bad")
    _act_as(user)
    old_hash = user.hashed_password

    with patch(
        "nfm_db.api.v1.profile.authenticate_user",
        return_value=False,
    ):
        response = await async_client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "WrongOld99", "new_password": "NewPass456"},
        )

    assert response.status_code == 401
    fresh = await db_session.get(User, user.id)
    assert fresh.hashed_password == old_hash


@pytest.mark.asyncio
async def test_change_password_weak_new(async_client, db_session) -> None:
    """Weak new password (no digit) → 400, hash untouched."""
    user = await _seed_user(db_session, username="chg_weak")
    _act_as(user)
    old_hash = user.hashed_password

    with patch(
        "nfm_db.api.v1.profile.authenticate_user",
        return_value=True,
    ):
        response = await async_client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "OldPass123", "new_password": "nodigits"},
        )

    assert response.status_code == 400
    fresh = await db_session.get(User, user.id)
    assert fresh.hashed_password == old_hash


@pytest.mark.asyncio
async def test_change_password_short_new(async_client, db_session) -> None:
    """<8 chars → 422 (schema min_length fires before the handler)."""
    user = await _seed_user(db_session, username="chg_short")
    _act_as(user)

    response = await async_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "OldPass123", "new_password": "Ab1"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_change_password_requires_auth(async_client, db_session) -> None:
    """No token → 401 through the real auth chain."""
    response = await async_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "OldPass123", "new_password": "NewPass456"},
    )
    assert response.status_code == 401
