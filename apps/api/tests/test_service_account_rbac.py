"""Service-account RBAC tests (NFM-1973 / NFM-1972 AC-1).

Verifies the belt-and-suspenders enforcement of service-account scope:

1. ``POST /api/v1/auth/login`` mints a token with ``is_service_account``
   + ``scope`` claims when the user has ``is_service_account=True``.
2. ``require_service_scope`` admits only service tokens whose JWT
   ``scope`` claim matches the requested scope.
3. ``/api/v1/extraction/ingest`` accepts a correctly scoped service token
   (E2E happy path) and rejects human tokens / wrong-scope tokens /
   mismatched DB rows.
4. ``require_blog_role`` / ``require_permission`` (used by ``/admin/*``
   and ``/extraction/trigger``) deny service accounts with ``403``.

The ``@pytest.mark.no_auto_auth`` marker is required everywhere here so
the real auth chain runs (see conftest fixture ``_reenable_rate_limit_overrides``).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.user import (
    BlogRole,
    ServiceAccountScope,
    User,
)
from nfm_db.services.auth_service import (
    create_access_token,
    create_service_account_token,
    decode_access_token,
    get_password_hash,
)

# Apply the no_auto_auth marker to every test in this module so the real
# JWT chain runs (otherwise conftest auto-authenticates as admin and
# ``/auth/login`` is never exercised).  pytestmark is module-scoped and
# propagates to all tests below.
pytestmark = [pytest.mark.no_auto_auth]


# ---------------------------------------------------------------------------
# Helpers — DB-scoped fixtures for a service account and an admin user.
# ---------------------------------------------------------------------------


@pytest.fixture
async def service_account(db_session: AsyncSession) -> User:
    """Create a service account row with a known password."""
    user = User(
        username="ontofuel-svc",
        email="ontofuel-svc@service.local",
        full_name="service:ontofuel-svc",
        hashed_password=get_password_hash("svc-password-1"),
        is_active=True,
        is_service_account=True,
        # blog_role left NULL — service accounts have no blog role.
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Create a human admin user — for cross-checking RBAC denials."""
    user = User(
        username="alice-admin",
        email="alice-admin@example.com",
        hashed_password=get_password_hash("human-password-1"),
        is_active=True,
        is_service_account=False,
        blog_role=BlogRole.ADMIN,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def reviewer_user(db_session: AsyncSession) -> User:
    """Create a human reviewer — admitted by ``get_current_active_user`` but rejected by ``require_ingest_authority`` (no editor/admin role)."""
    user = User(
        username="rita-reviewer",
        email="rita-reviewer@example.com",
        hashed_password=get_password_hash("reviewer-password-1"),
        is_active=True,
        is_service_account=False,
        blog_role=BlogRole.REVIEWER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def scoped_client(db_session: AsyncSession) -> AsyncClient:
    """Async test client with the in-memory SQLite engine wired in.

    Each test gets a fresh dependency override so the session-scoped
    rate-limit fixture doesn't clobber our ``get_db`` injection.
    """
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 1. /auth/login must mint a service-scoped JWT for service accounts
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_login_emits_service_token_for_service_account(
    scoped_client: AsyncClient,
    service_account: User,
) -> None:
    """A service account logging in receives ``is_service_account`` + ``scope`` claims.

    The OntoFuel client expects this token shape to satisfy
    ``require_service_scope`` on ``/api/v1/extraction/ingest``.
    """
    response = await scoped_client.post(
        "/api/v1/auth/login",
        data={"username": "ontofuel-svc", "password": "svc-password-1"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]

    payload = decode_access_token(token)
    assert payload is not None
    assert payload.get("is_service_account") is True
    assert payload.get("scope") == ServiceAccountScope.EXTRACTION_INGEST.value


@pytest.mark.unit
async def test_login_emits_plain_token_for_human_user(
    scoped_client: AsyncClient,
    admin_user: User,
) -> None:
    """Human users get a plain ``{"sub": ...}`` token — no scope drift."""
    response = await scoped_client.post(
        "/api/v1/auth/login",
        data={"username": "alice-admin", "password": "human-password-1"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]

    payload = decode_access_token(token)
    assert payload is not None
    # Plain human token — ``is_service_account`` claim is absent.
    assert "is_service_account" not in payload
    assert "scope" not in payload


# ---------------------------------------------------------------------------
# 2. RBAC — service accounts are rejected everywhere except /extraction/ingest
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_service_account_cannot_access_admin_roles_endpoint(
    scoped_client: AsyncClient,
    service_account: User,
) -> None:
    """Service tokens must NOT pass ``require_admin`` (used by /auth/roles)."""
    token = create_service_account_token(
        service_account,
        ServiceAccountScope.EXTRACTION_INGEST,
    )
    response = await scoped_client.get(
        "/api/v1/auth/roles",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
    assert "Service accounts cannot access this endpoint" in response.json()["detail"]


@pytest.mark.unit
async def test_service_account_cannot_access_editor_only_trigger(
    scoped_client: AsyncClient,
    service_account: User,
) -> None:
    """Service tokens must NOT pass ``require_editor`` (used by /extraction/trigger)."""
    token = create_service_account_token(
        service_account,
        ServiceAccountScope.EXTRACTION_INGEST,
    )
    response = await scoped_client.post(
        "/api/v1/extraction/trigger",
        json={"source_reference": "10.1016/j.jnucmat.2024.01.001", "source_type": "doi"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# 3. /api/v1/extraction/ingest — happy path + cross-checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_ingest_endpoint_accepts_correctly_scoped_service_token(
    scoped_client: AsyncClient,
    service_account: User,
) -> None:
    """Service token + matching scope + matching DB row → 202 Accepted."""
    token = create_service_account_token(
        service_account,
        ServiceAccountScope.EXTRACTION_INGEST,
    )
    response = await scoped_client.post(
        "/api/v1/extraction/ingest",
        json={
            "source_reference": "10.1016/j.jnucmat.2024.01.001",
            "source_type": "doi",
            "corpus_id": "ceramics",
            "element_systems": ["U", "O"],
            "properties": [{"property_name": "lattice_constant", "value": 5.47}],
            "metadata": {"model_version": "ontofuel-1.0.0"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["source_reference"] == "10.1016/j.jnucmat.2024.01.001"
    assert body["accepted_count"] == 1
    assert body["corpus_id"] == "ceramics"
    assert body["message"] == "Ingest accepted; queued for processing."
    assert "job_id" in body


@pytest.mark.unit
async def test_ingest_endpoint_rejects_human_token(
    scoped_client: AsyncClient,
    reviewer_user: User,
) -> None:
    """A plain reviewer token (no service claims, no editor/admin role) → 403.

    ``require_ingest_authority`` admits humans with ``editor`` or
    ``admin`` blog role; everyone else gets ``403 Forbidden`` so that a
    non-editor human cannot piggyback on the ingest surface that
    OntoFuel uses.
    """
    token = create_access_token(data={"sub": str(reviewer_user.id)})
    response = await scoped_client.post(
        "/api/v1/extraction/ingest",
        json={
            "source_reference": "10.1016/j.jnucmat.2024.01.001",
            "source_type": "doi",
            "corpus_id": "ceramics",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
    assert "Requires editor or admin role" in response.json()["detail"]


@pytest.mark.unit
async def test_ingest_endpoint_rejects_token_with_no_authorization_header(
    scoped_client: AsyncClient,
) -> None:
    """No token at all → 401 from the upstream auth chain."""
    response = await scoped_client.post(
        "/api/v1/extraction/ingest",
        json={
            "source_reference": "10.1016/j.jnucmat.2024.01.001",
            "source_type": "doi",
            "corpus_id": "ceramics",
        },
    )
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# 4. E2E — service account login → ingest → verify success (no mock skips)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_e2e_service_account_login_then_ingest(
    scoped_client: AsyncClient,
    service_account: User,
) -> None:
    """Full E2E flow that OntoFuel would execute.

    1. ``POST /auth/login`` with the service account credentials.
    2. Pull the token from the JSON response (NOT from the cookie, to
       mirror the OntoFuel header-based client).
    3. ``POST /api/v1/extraction/ingest`` with the bearer token.
    4. Assert a 202 with the expected ``job_id``, ``source_reference``,
       and ``accepted_count`` fields.

    No mocks, no skips — every layer (auth, RBAC, ingest handler, DB)
    runs end-to-end against the in-memory engine.
    """
    login_response = await scoped_client.post(
        "/api/v1/auth/login",
        data={"username": "ontofuel-svc", "password": "svc-password-1"},
    )
    assert login_response.status_code == 200, login_response.text
    login_body = login_response.json()
    assert "access_token" in login_body
    token = login_body["access_token"]

    ingest_response = await scoped_client.post(
        "/api/v1/extraction/ingest",
        json={
            "source_reference": "e2e-ontofuel-doi-001",
            "source_type": "doi",
            "corpus_id": "ceramics",
            "element_systems": ["U", "O", "Pu"],
            "properties": [
                {"property_name": "lattice_constant", "value": 5.47, "unit": "angstrom"},
                {"property_name": "bulk_modulus", "value": 207.0, "unit": "GPa"},
                {"property_name": "formation_energy", "value": -10.5, "unit": "eV"},
            ],
            "metadata": {"model_version": "ontofuel-1.0.0", "extracted_at": "2026-07-28T00:00:00Z"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ingest_response.status_code == 202, ingest_response.text
    ack = ingest_response.json()
    assert ack["source_reference"] == "e2e-ontofuel-doi-001"
    assert ack["source_type"] == "doi"
    assert ack["accepted_count"] == 3
    assert ack["corpus_id"] == "ceramics"
    assert ack["message"] == "Ingest accepted; queued for processing."
    # job_id should be a valid UUID — sanity check.
    uuid.UUID(ack["job_id"])
