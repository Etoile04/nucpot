"""Integration tests for re-extraction queue endpoints (NFM-2581).

Covers:
  - POST /api/v1/ontology/versions/{id}/re-extract — trigger
  - GET  /api/v1/re-extraction/queue — list with filters
  - GET  /api/v1/re-extraction/queue/{id} — detail
  - POST /api/v1/re-extraction/queue/{id}/cancel — cancel
  - Role gating (403/401 for non-domain-expert)
  - Idempotency guard (skip pending/running duplicates)

Uses ``@pytest.mark.no_auto_auth`` to exercise the real auth chain
where role gating is verified.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Corpus, OntologyVersion, ReExtractionQueue, User
from nfm_db.models.user import BlogRole

BASE_QUEUE = "/api/v1/re-extraction/queue"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    role: BlogRole = BlogRole.DOMAIN_EXPERT,
) -> User:
    """Create and flush a test user with the given role."""
    uid = user_id or uuid.uuid4()
    user = User(
        id=uid,
        username=f"test_{role.value}_{uid.hex[:8]}",
        email=f"test_{uid.hex[:8]}@test.com",
        hashed_password="hashed",
        blog_role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ontology_version(
    session: AsyncSession,
    *,
    version_id: uuid.UUID | None = None,
) -> OntologyVersion:
    """Create and flush an OntologyVersion."""
    user = await _seed_user(session)
    vid = version_id or uuid.uuid4()
    ov = OntologyVersion(
        id=vid,
        version=f"1.0.0-{vid.hex[:8]}",
        status="published",
        created_by=user.id,
    )
    session.add(ov)
    await session.flush()
    return ov


async def _seed_corpus(
    session: AsyncSession,
    *,
    cid: uuid.UUID | None = None,
) -> Corpus:
    """Create and flush a Corpus."""
    uid = cid or uuid.uuid4()
    corpus = Corpus(
        id=uid,
        corpus_id=f"test-corpus-{uid.hex[:8]}",
        name="Test Corpus",
    )
    session.add(corpus)
    await session.flush()
    return corpus


async def _seed_queue_entry(
    session: AsyncSession,
    *,
    entry_id: uuid.UUID | None = None,
    ontology_version_id: uuid.UUID | None = None,
    corpus_id: uuid.UUID | None = None,
    status: str = "pending",
) -> ReExtractionQueue:
    """Create and flush a ReExtractionQueue entry.

    When ``ontology_version_id`` or ``corpus_id`` are *not* provided, real
    parent rows are seeded so that FK constraints are satisfied.  When
    they *are* provided the caller is responsible for ensuring the
    referenced rows already exist.
    """
    user = await _seed_user(session)
    if ontology_version_id is None:
        ov = await _seed_ontology_version(session)
        ontology_version_id = ov.id
    if corpus_id is None:
        corpus = await _seed_corpus(session)
        corpus_id = corpus.id
    entry = ReExtractionQueue(
        id=entry_id or uuid.uuid4(),
        ontology_version_id=ontology_version_id,
        corpus_id=corpus_id,
        triggered_by=user.id,
        status=status,
    )
    session.add(entry)
    await session.flush()
    return entry


# ---------------------------------------------------------------------------
# POST /ontology/versions/{id}/re-extract
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_trigger_creates_pending_entries(db_session, async_client):
    """Trigger re-extraction creates queue entries with status=pending."""
    ov = await _seed_ontology_version(db_session)
    corpus = await _seed_corpus(db_session)
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["status"] == "pending"
    assert data["created"][0]["ontology_version_id"] == str(ov.id)
    assert data["created"][0]["corpus_id"] == str(corpus.id)
    assert data["skipped"] == []

    # Verify DB state.
    rows = (await db_session.execute(select(ReExtractionQueue))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"


@pytest.mark.integration
async def test_trigger_idempotency_skips_pending(db_session, async_client):
    """Trigger skips corpus that already has a pending entry."""
    ov = await _seed_ontology_version(db_session)
    corpus = await _seed_corpus(db_session)
    await _seed_queue_entry(
        db_session,
        ontology_version_id=ov.id,
        corpus_id=corpus.id,
        status="pending",
    )
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 0
    assert len(data["skipped"]) == 1
    assert "already queued" in data["skipped"][0]["reason"]


@pytest.mark.integration
async def test_trigger_idempotency_skips_running(db_session, async_client):
    """Trigger skips corpus that already has a running entry."""
    ov = await _seed_ontology_version(db_session)
    corpus = await _seed_corpus(db_session)
    await _seed_queue_entry(
        db_session,
        ontology_version_id=ov.id,
        corpus_id=corpus.id,
        status="running",
    )
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 0
    assert len(data["skipped"]) == 1


@pytest.mark.integration
async def test_trigger_allows_after_completed(db_session, async_client):
    """Trigger creates a new entry when previous one is completed."""
    ov = await _seed_ontology_version(db_session)
    corpus = await _seed_corpus(db_session)
    await _seed_queue_entry(
        db_session,
        ontology_version_id=ov.id,
        corpus_id=corpus.id,
        status="completed",
    )
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 1
    assert data["skipped"] == []


@pytest.mark.integration
async def test_trigger_404_for_missing_version(db_session, async_client):
    """Trigger returns 404 when ontology version does not exist."""
    fake_id = uuid.uuid4()
    corpus = await _seed_corpus(db_session)
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{fake_id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_trigger_skips_missing_corpus(db_session, async_client):
    """Trigger skips corpus IDs that do not exist in the database."""
    ov = await _seed_ontology_version(db_session)
    await db_session.commit()

    fake_corpus_id = uuid.uuid4()
    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(fake_corpus_id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 0
    assert len(data["skipped"]) == 1
    assert "corpus not found" in data["skipped"][0]["reason"]


@pytest.mark.integration
async def test_trigger_multiple_corpora(db_session, async_client):
    """Trigger handles multiple corpora, mixing valid and missing."""
    ov = await _seed_ontology_version(db_session)
    corpus1 = await _seed_corpus(db_session)
    corpus2 = await _seed_corpus(db_session)
    await db_session.commit()

    missing_id = uuid.uuid4()
    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus1.id), str(missing_id), str(corpus2.id)]},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["created"]) == 2
    assert len(data["skipped"]) == 1


# ---------------------------------------------------------------------------
# GET /re-extraction/queue
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_queue_empty(db_session, async_client):
    """List returns empty list when no entries exist."""
    resp = await async_client.get(BASE_QUEUE)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.integration
async def test_list_queue_returns_entries(db_session, async_client):
    """List returns all queue entries."""
    await _seed_queue_entry(db_session, status="pending")
    await _seed_queue_entry(db_session, status="completed")
    await db_session.commit()

    resp = await async_client.get(BASE_QUEUE)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.integration
async def test_list_queue_filter_by_status(db_session, async_client):
    """List filters entries by status query param."""
    await _seed_queue_entry(db_session, status="pending")
    await _seed_queue_entry(db_session, status="completed")
    await db_session.commit()

    resp = await async_client.get(BASE_QUEUE, params={"status": "pending"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"


@pytest.mark.integration
async def test_list_queue_filter_by_ontology_version(db_session, async_client):
    """List filters entries by ontology_version_id query param."""
    ov1 = await _seed_ontology_version(db_session)
    ov2 = await _seed_ontology_version(db_session)
    await _seed_queue_entry(db_session, ontology_version_id=ov1.id)
    await _seed_queue_entry(db_session, ontology_version_id=ov2.id)
    await db_session.commit()

    resp = await async_client.get(
        BASE_QUEUE, params={"ontology_version_id": str(ov1.id)}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


@pytest.mark.integration
async def test_list_queue_filter_by_corpus(db_session, async_client):
    """List filters entries by corpus_id query param."""
    corpus1 = await _seed_corpus(db_session)
    corpus2 = await _seed_corpus(db_session)
    await _seed_queue_entry(db_session, corpus_id=corpus1.id)
    await _seed_queue_entry(db_session, corpus_id=corpus2.id)
    await db_session.commit()

    resp = await async_client.get(
        BASE_QUEUE, params={"corpus_id": str(corpus1.id)}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


@pytest.mark.integration
async def test_list_queue_rejects_invalid_status(db_session, async_client):
    """List returns 400 for invalid status filter."""
    resp = await async_client.get(
        BASE_QUEUE, params={"status": "nonexistent"}
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /re-extraction/queue/{id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_entry_detail(db_session, async_client):
    """Get returns a single queue entry."""
    entry = await _seed_queue_entry(db_session)
    await db_session.commit()

    resp = await async_client.get(f"{BASE_QUEUE}/{entry.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(entry.id)
    assert data["status"] == "pending"


@pytest.mark.integration
async def test_get_entry_404(db_session, async_client):
    """Get returns 404 for missing entry."""
    resp = await async_client.get(f"{BASE_QUEUE}/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /re-extraction/queue/{id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cancel_pending_entry(db_session, async_client):
    """Cancel sets a pending entry to cancelled."""
    entry = await _seed_queue_entry(db_session, status="pending")
    await db_session.commit()

    resp = await async_client.post(f"{BASE_QUEUE}/{entry.id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"


@pytest.mark.integration
async def test_cancel_running_rejected(db_session, async_client):
    """Cancel returns 409 for a running entry."""
    entry = await _seed_queue_entry(db_session, status="running")
    await db_session.commit()

    resp = await async_client.post(f"{BASE_QUEUE}/{entry.id}/cancel")
    assert resp.status_code == 409


@pytest.mark.integration
async def test_cancel_completed_rejected(db_session, async_client):
    """Cancel returns 409 for a completed entry."""
    entry = await _seed_queue_entry(db_session, status="completed")
    await db_session.commit()

    resp = await async_client.post(f"{BASE_QUEUE}/{entry.id}/cancel")
    assert resp.status_code == 409


@pytest.mark.integration
async def test_cancel_already_cancelled_rejected(db_session, async_client):
    """Cancel returns 409 for an already cancelled entry."""
    entry = await _seed_queue_entry(db_session, status="cancelled")
    await db_session.commit()

    resp = await async_client.post(f"{BASE_QUEUE}/{entry.id}/cancel")
    assert resp.status_code == 409


@pytest.mark.integration
async def test_cancel_404_missing(db_session, async_client):
    """Cancel returns 404 for a missing entry."""
    resp = await async_client.post(f"{BASE_QUEUE}/{uuid.uuid4()}/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Role gating — 401 for unauthenticated (no_auto_auth exercises real chain)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.no_auto_auth
async def test_trigger_requires_auth(db_session, async_client):
    """Trigger returns 401 for an unauthenticated user."""
    ov = await _seed_ontology_version(db_session)
    corpus = await _seed_corpus(db_session)
    await db_session.commit()

    resp = await async_client.post(
        f"/api/v1/ontology/versions/{ov.id}/re-extract",
        json={"corpus_ids": [str(corpus.id)]},
    )
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.no_auto_auth
async def test_list_queue_requires_auth(db_session, async_client):
    """List returns 401 for unauthenticated user."""
    resp = await async_client.get(BASE_QUEUE)
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.no_auto_auth
async def test_get_entry_requires_auth(db_session, async_client):
    """Get returns 401 for unauthenticated user."""
    resp = await async_client.get(f"{BASE_QUEUE}/{uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.no_auto_auth
async def test_cancel_requires_auth(db_session, async_client):
    """Cancel returns 401 for unauthenticated user."""
    resp = await async_client.post(f"{BASE_QUEUE}/{uuid.uuid4()}/cancel")
    assert resp.status_code == 401
