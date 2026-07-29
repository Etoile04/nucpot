"""Tests for AC-5 corpus_id management (NFM-1980).

Covers the contract decided by the CPO for the OntoFuel → NucPot ingest
surface:

* A **service account** calling ``/extraction/ingest`` with an unregistered
  ``corpus_id`` auto-creates the corpus row (``is_auto_created=True``,
  ``owner_id=None``) and the ingest succeeds with ``202 Accepted``.
* A **human** (admin / editor) calling the same endpoint with an
  unregistered ``corpus_id`` is rejected with ``400 Bad Request`` carrying
  a hint that the corpus must be registered by an admin first.
* A service account re-ingesting under an already-registered
  ``corpus_id`` does NOT create a duplicate row (the ``UNIQUE`` constraint
  on ``corpus.corpus_id`` plus the existence-check guard make the call
  idempotent).

Each test exercises the real auth chain (the
``@pytest.mark.no_auto_auth`` marker drops the session-wide admin
override so that the service-account branch can run).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Corpus, User
from nfm_db.models.user import ServiceAccountScope
from nfm_db.services.auth_service import create_service_account_token


SERVICE_USER_ID = uuid.UUID("b0000000-0000-0000-0000-000000000099")
ONTOFUEL_CORPUS_ID = "ontofuel"
HUMAN_MISSING_CORPUS_ID = "human-missing"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_service_user(db_session: AsyncSession) -> User:
    """Insert a service-account user and return the refreshed row."""
    user = User(
        id=SERVICE_USER_ID,
        username="ontofuel_svc",
        email="ontofuel@svc.local",
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


def _detail_text(response_json: object) -> str:
    """Coerce any FastAPI error payload into a lowercased string for substring checks."""
    if isinstance(response_json, dict):
        detail = response_json.get("detail")
        if isinstance(detail, str):
            return detail.lower()
        return str(response_json).lower()
    return str(response_json).lower()


# ---------------------------------------------------------------------------
# Test 1: service account + new corpus → auto-create + 202
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_service_account_new_corpus_auto_creates_and_returns_202(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Service account ingests under a fresh corpus_id → auto-create + 202."""
    svc_user = await _seed_service_user(db_session)

    response = await async_client.post(
        "/api/v1/extraction/ingest",
        headers=_service_headers(svc_user),
        json={
            "source_reference": "10.1234/ontofuel.0",
            "source_type": "doi",
            "corpus_id": ONTOFUEL_CORPUS_ID,
            "properties": [],
        },
    )

    assert response.status_code == 202, response.text

    rows = (
        await db_session.execute(select(Corpus).where(Corpus.corpus_id == ONTOFUEL_CORPUS_ID))
    ).scalars().all()
    assert len(rows) == 1, "exactly one corpus row should exist after first ingest"
    row = rows[0]
    assert row.is_auto_created is True
    assert row.owner_id is None
    assert row.name == ONTOFUEL_CORPUS_ID


# ---------------------------------------------------------------------------
# Test 2: human user + new corpus → 400 with admin-contact hint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_human_user_missing_corpus_returns_400(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Admin (auto-authed, human) ingests under a missing corpus → 400.

    The conftest installs a session-wide admin override; the combined
    ``require_ingest_authority`` admits admin/editor humans, then the
    handler rejects the missing-corpus case with 400 because the caller
    is **not** a service account.
    """
    response = await async_client.post(
        "/api/v1/extraction/ingest",
        json={
            "source_reference": "10.1234/human.0",
            "source_type": "doi",
            "corpus_id": HUMAN_MISSING_CORPUS_ID,
            "properties": [],
        },
    )

    assert response.status_code == 400, response.text
    body = response.json()
    detail = _detail_text(body)
    assert HUMAN_MISSING_CORPUS_ID in detail
    assert "registered" in detail or "admin" in detail


# ---------------------------------------------------------------------------
# Test 3: repeat service ingest → no duplicate (UNIQUE constraint + guard)
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_repeat_service_ingest_does_not_duplicate_corpus(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Two service ingests under the same corpus_id → only one Corpus row exists."""
    svc_user = await _seed_service_user(db_session)
    headers = _service_headers(svc_user)
    payload = {
        "source_reference": "10.1234/ontofuel.repeat",
        "source_type": "doi",
        "corpus_id": ONTOFUEL_CORPUS_ID,
        "properties": [],
    }

    first = await async_client.post("/api/v1/extraction/ingest", headers=headers, json=payload)
    assert first.status_code == 202, first.text

    second = await async_client.post("/api/v1/extraction/ingest", headers=headers, json=payload)
    assert second.status_code == 202, second.text

    rows = (
        await db_session.execute(select(Corpus).where(Corpus.corpus_id == ONTOFUEL_CORPUS_ID))
    ).scalars().all()
    assert len(rows) == 1, (
        f"UNIQUE(corpus_id) must keep duplicate inserts idempotent; got {len(rows)} rows"
    )