"""Tests for AC-6 rate limiting on the ingest endpoint (NFM-1982).

Covers:
* Batch size limit: properties array > 500 → 400 Bad Request.
* Service-account rate limit: 10 req/min per account, keyed on JWT subject.
* 429 response includes Retry-After header (seconds).
* Different service accounts have independent counters.

All tests use the ``no_auto_auth`` marker to exercise the real
auth chain (service-account JWT → ``require_ingest_authority``).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.user import ServiceAccountScope, User
from nfm_db.services.auth_service import create_service_account_token

# Deterministic service-account UUIDs (independent of conftest seed data).
_SVC_USER_A_ID = uuid.UUID("c0000000-0000-0000-0000-000000000001")
_SVC_USER_B_ID = uuid.UUID("c0000000-0000-0000-0000-000000000002")

INGEST_URL = "/api/v1/extraction/ingest"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_service_user(
    db_session: AsyncSession,
    user_id: uuid.UUID,
    username: str,
) -> User:
    """Insert a service-account user and return the refreshed row."""
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@svc.local",
        hashed_password="hashed",
        is_service_account=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _service_headers(user: User) -> dict[str, str]:
    """Return Authorization headers for the given service-account user."""
    token = create_service_account_token(user, ServiceAccountScope.EXTRACTION_INGEST)
    return {"Authorization": f"Bearer {token}"}


def _make_properties(n: int) -> list[dict[str, str]]:
    """Create *n* minimal property records."""
    return [{"property_name": f"prop_{i}", "value": "1.0"} for i in range(n)]


def _ingest_payload(
    corpus_id: str = "ontofuel",
    properties: list[dict[str, str]] | None = None,
) -> dict:
    """Build a minimal ingest request body."""
    return {
        "source_reference": "10.1234/test.0",
        "source_type": "doi",
        "corpus_id": corpus_id,
        "properties": properties or [],
    }


# ---------------------------------------------------------------------------
# AC: Batch size > 500 → 400
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_batch_size_501_returns_400(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST with 501 properties → 400 Bad Request."""
    svc_user = await _seed_service_user(db_session, _SVC_USER_A_ID, "svc_a")
    headers = _service_headers(svc_user)
    payload = _ingest_payload(properties=_make_properties(501))

    response = await async_client.post(INGEST_URL, headers=headers, json=payload)

    assert response.status_code == 400, response.text
    body = response.json()
    detail = body.get("detail", "")
    assert "500" in detail or "batch" in detail.lower() or "properties" in detail.lower()


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_batch_size_500_returns_202(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST with exactly 500 properties → 202 Accepted (boundary)."""
    svc_user = await _seed_service_user(db_session, _SVC_USER_A_ID, "svc_a")
    headers = _service_headers(svc_user)
    payload = _ingest_payload(properties=_make_properties(500))

    response = await async_client.post(INGEST_URL, headers=headers, json=payload)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total_received"] == 500


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_batch_size_1_returns_202(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST with 1 property → 202 (happy path)."""
    svc_user = await _seed_service_user(db_session, _SVC_USER_A_ID, "svc_a")
    headers = _service_headers(svc_user)
    payload = _ingest_payload(properties=_make_properties(1))

    response = await async_client.post(INGEST_URL, headers=headers, json=payload)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total_received"] == 1


# ---------------------------------------------------------------------------
# AC: Service-account rate limit 10 req/min → 429 + Retry-After
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_rate_limit_11th_request_returns_429(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Same service account, 11 requests within 1 minute → 11th returns 429.

    The ingest rate limiter (10/minute) is separate from the global
    slowapi limiter (100/minute).  The conftest disables the global
    limiter; this test explicitly enables the ingest-specific limiter.
    """
    from nfm_db.services.rate_limit import ingest_rate_limiter

    svc_user = await _seed_service_user(db_session, _SVC_USER_A_ID, "svc_a")
    headers = _service_headers(svc_user)
    payload = _ingest_payload()

    # Reset the limiter so prior test runs don't pollute the counter.
    ingest_rate_limiter.reset()

    # First 10 requests → 202.
    for i in range(10):
        response = await async_client.post(INGEST_URL, headers=headers, json=payload)
        assert response.status_code == 202, f"request {i+1} failed: {response.text}"

    # 11th request → 429.
    response = await async_client.post(INGEST_URL, headers=headers, json=payload)
    assert response.status_code == 429, f"expected 429, got {response.status_code}: {response.text}"

    # Must include Retry-After header.
    retry_after = response.headers.get("retry-after")
    assert retry_after is not None, "429 response must include Retry-After header"
    assert int(retry_after) >= 1, f"Retry-After must be >= 1 second, got {retry_after}"


# ---------------------------------------------------------------------------
# AC: Different service accounts have independent counters
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_rate_limit_independent_per_service_account(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Two service accounts each get their own 10/minute quota."""
    from nfm_db.services.rate_limit import ingest_rate_limiter

    svc_a = await _seed_service_user(db_session, _SVC_USER_A_ID, "svc_a")
    svc_b = await _seed_service_user(db_session, _SVC_USER_B_ID, "svc_b")
    headers_a = _service_headers(svc_a)
    headers_b = _service_headers(svc_b)
    payload = _ingest_payload()

    ingest_rate_limiter.reset()

    # Service A burns all 10 requests.
    for i in range(10):
        response = await async_client.post(INGEST_URL, headers=headers_a, json=payload)
        assert response.status_code == 202, f"svc_a request {i+1}: {response.text}"

    # Service A's 11th → 429.
    response = await async_client.post(INGEST_URL, headers=headers_a, json=payload)
    assert response.status_code == 429, f"svc_a 11th should be 429, got {response.status_code}"

    # Service B's 1st request → 202 (independent counter).
    response = await async_client.post(INGEST_URL, headers=headers_b, json=payload)
    assert response.status_code == 202, f"svc_b 1st should be 202, got {response.status_code}: {response.text}"
