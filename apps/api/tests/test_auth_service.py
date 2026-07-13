import pytest
"""Unit tests for authentication service."""

from datetime import timedelta

from nfm_db.models.user import User
from nfm_db.services.auth_service import (
    authenticate_user,
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
pytestmark = pytest.mark.xfail(reason="NFM-1366: passlib/bcrypt incompatibility with Python 3.14", strict=False)


class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_password(self) -> None:
        """Test that password hashing produces a hash."""
        password = "test_password_123"
        hashed = get_password_hash(password)

        assert hashed is not None
        assert hashed != password
        assert len(hashed) > 50  # bcrypt hashes are typically 60 chars

    def test_verify_correct_password(self) -> None:
        """Test that correct password is verified."""
        password = "correct_password"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_incorrect_password(self) -> None:
        """Test that incorrect password is rejected."""
        password = "correct_password"
        wrong_password = "wrong_password"
        hashed = get_password_hash(password)

        assert verify_password(wrong_password, hashed) is False


class TestJWTTokens:
    """Test JWT token creation and decoding."""

    def test_create_access_token(self) -> None:
        """Test creating an access token."""
        data = {"sub": "user-123"}
        token = create_access_token(data)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 50

    def test_decode_valid_token(self) -> None:
        """Test decoding a valid token."""
        data = {"sub": "user-456"}
        token = create_access_token(data)

        payload = decode_access_token(token)

        assert payload is not None
        assert payload.get("sub") == "user-456"

    def test_decode_invalid_token(self) -> None:
        """Test decoding an invalid token returns None."""
        invalid_token = "invalid.token.here"

        payload = decode_access_token(invalid_token)

        assert payload is None

    def test_token_expiration(self) -> None:
        """Test token with custom expiration."""
        data = {"sub": "user-789"}
        expires = timedelta(seconds=1)
        token = create_access_token(data, expires)

        # Should decode immediately
        payload = decode_access_token(token)
        assert payload is not None


class TestUserAuthentication:
    """Test user authentication."""

    def test_authenticate_user_success(self) -> None:
        """Test authenticating a user with correct credentials."""
        password = "test_password"
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password=get_password_hash(password),
        )

        assert authenticate_user(user, password) is True

    def test_authenticate_user_failure(self) -> None:
        """Test authenticating a user with incorrect password."""
        password = "correct_password"
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password=get_password_hash(password),
        )

        assert authenticate_user(user, "wrong_password") is False
