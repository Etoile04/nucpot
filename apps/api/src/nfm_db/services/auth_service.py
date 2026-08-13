"""Authentication service for password hashing and JWT token management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext

from nfm_db.config import get_settings
from nfm_db.models.user import ServiceAccountScope, User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token.

    Each call mints a token with a fresh ``jti`` (JWT ID, RFC 7519 §4.1.7)
    so two tokens issued in the same second are still byte-distinct. This
    is required by the sliding-window refresh endpoint (NFM-2236) so a
    re-issued cookie is observable as a *different* token, even when the
    underlying subject and expiry are identical.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=15)
    to_encode.update({"exp": expire, "jti": uuid.uuid4().hex})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT access token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError:
        return None


def authenticate_user(user: User, password: str) -> bool:
    """Authenticate a user by verifying their password.

    Service accounts authenticate via the same password flow as humans —
    no special-cased credential format.  The ``is_service_account`` flag
    only governs **what** the token-holder is permitted to do once logged
    in, not how they prove their identity.
    """
    return verify_password(password, user.hashed_password)


# ---------------------------------------------------------------------------
# Service-account token issuance (NFM-1973 / NFM-1972 AC-1)
# ---------------------------------------------------------------------------


def create_service_account_token(
    user: User,
    scope: ServiceAccountScope,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a JWT for a service account, scoped to a single authority.

    The encoded payload distinguishes service tokens from human tokens via
    two claims:

    * ``is_service_account: true`` — the JWT came from a service identity.
    * ``scope`` — the single ``ServiceAccountScope`` value the bearer is
      authorized for.  Endpoints opt-in by depending on
      ``require_service_scope(scope)``; everything else returns 403.

    TTL defaults to ``settings.service_jwt_ttl_minutes`` (configurable via
    ``NUCPOT_SERVICE_JWT_TTL_MINUTES``) so the standard login endpoint can
    reuse this helper without knowing the knob.
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.service_jwt_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "is_service_account": True,
        "scope": scope.value,
    }
    return create_access_token(payload, expires_delta=expires_delta)


def is_service_token_payload(payload: dict[str, Any] | None) -> bool:
    """Return True iff the JWT payload identifies a service account."""
    if not payload:
        return False
    return bool(payload.get("is_service_account"))


def token_scope(payload: dict[str, Any] | None) -> ServiceAccountScope | None:
    """Return the ``ServiceAccountScope`` declared in the JWT, if any."""
    if not payload:
        return None
    raw = payload.get("scope")
    if not isinstance(raw, str):
        return None
    try:
        return ServiceAccountScope(raw)
    except ValueError:
        return None


__all__ = [
    "authenticate_user",
    "create_access_token",
    "create_service_account_token",
    "decode_access_token",
    "get_password_hash",
    "is_service_token_payload",
    "token_scope",
    "verify_password",
]
