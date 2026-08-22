"""Integration tests for /api/v1/ontology/versions CRUD endpoints (NFM-2580).

Covers: paginated list, download with Content-Disposition, create draft,
update draft, publish with auto-semver, publish without changelog (422),
role gating (403), upload validation, deprecate.

Uses ``@pytest.mark.no_auto_auth`` to exercise the real auth chain.
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models.ontology_version import OntologyVersion

BASE = "/api/v1/ontology/versions"

# Seed user IDs from conftest.py (must exist in the test DB).
_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")

# Valid ontology payload for upload tests.
_VALID_ONTOLOGY = {
    "entity_types": [
        {"name": "NuclearFuel", "description": "Base class for nuclear fuels"},
    ],
    "relation_types": [
        {"name": "contains", "source": "Material", "target": "Element"},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_version(session, *, version="0.1.0", status="draft", user_id=None, **overrides):
    """Create and flush an OntologyVersion in the test DB."""
    ov = OntologyVersion(
        version=version,
        status=status,
        created_by=user_id or _SEED_USER_ID,
        **overrides,
    )
    session.add(ov)
    await session.flush()
    await session.refresh(ov)
    return ov


# ---------------------------------------------------------------------------
# GET /ontology/versions — paginated list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_versions_empty(db_session, async_client):
    resp = await async_client.get(BASE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_versions_paginated(db_session, async_client):
    for i in range(3):
        await _create_version(db_session, version=f"0.{i + 1}.0")
    await db_session.commit()

    resp = await async_client.get(BASE, params={"page": 1, "per_page": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert data["pages"] == 2


@pytest.mark.asyncio
async def test_list_versions_filter_by_status(db_session, async_client):
    await _create_version(db_session, version="0.1.0", status="draft")
    await _create_version(db_session, version="0.2.0", status="published")
    await _create_version(db_session, version="0.3.0", status="deprecated")
    await db_session.commit()

    resp = await async_client.get(BASE, params={"status": "published"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["version"] == "0.2.0"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_list_versions_unauthenticated(async_client):
    resp = await async_client.get(BASE)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /ontology/versions/latest/download
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_latest(db_session, async_client):
    test_data = {"key": "value"}
    await _create_version(
        db_session, version="1.0.0", status="published",
        ontology_data=test_data,
    )
    await db_session.commit()

    resp = await async_client.get(f"{BASE}/latest/download")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "ontology-v1.0.0.json" in resp.headers["content-disposition"]
    assert resp.json() == test_data


@pytest.mark.asyncio
async def test_download_latest_no_published(db_session, async_client):
    await _create_version(db_session, version="0.1.0", status="draft")
    await db_session.commit()

    resp = await async_client.get(f"{BASE}/latest/download")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /ontology/versions — create draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_create_draft_unauthenticated(async_client):
    resp = await async_client.post(BASE, json={"changelog": "initial"})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_create_draft_non_expert_forbidden(async_client, editor_headers):
    """Editor users get 403 on write endpoints."""
    resp = await async_client.post(
        BASE, json={"changelog": "initial"}, headers=editor_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_create_draft_domain_expert_ok(
    async_client, domain_expert_headers, db_session,
):
    resp = await async_client.post(
        BASE, json={"changelog": "initial"}, headers=domain_expert_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["version"] == "0.1.0"
    assert data["changelog"] == "initial"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_create_draft_with_ontology_data(
    async_client, domain_expert_headers, db_session,
):
    resp = await async_client.post(
        BASE,
        json={"changelog": "with data", "ontology_data": {"key": "val"}},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["ontology_data"] == {"key": "val"}


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_create_draft_second_does_not_collide_nfm928(
    async_client, domain_expert_headers, db_session,
):
    """NFM-928: hardcoded 0.1.0 placeholder hit the UNIQUE constraint
    (version spans all statuses) → IntegrityError → 500 on the second
    create once any 0.1.0 row existed. The placeholder must be allocated
    past the table max instead."""
    await _create_version(db_session, version="0.1.0", status="published")
    await db_session.commit()

    resp = await async_client.post(
        BASE, json={"changelog": "second draft"}, headers=domain_expert_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["version"] != "0.1.0"
    assert data["version"] == "0.1.1"

    # And a third one keeps incrementing.
    resp2 = await async_client.post(
        BASE, json={"changelog": "third draft"}, headers=domain_expert_headers,
    )
    assert resp2.status_code == 201
    assert resp2.json()["version"] == "0.1.2"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_ontology_second_does_not_collide_nfm928(
    async_client, domain_expert_headers, db_session,
):
    """Same NFM-928 collision via the upload endpoint."""
    await _create_version(db_session, version="0.2.0", status="published")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/upload",
        json={"changelog": "uploaded draft", "ontology_data": _VALID_ONTOLOGY},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == "0.2.1"


# ---------------------------------------------------------------------------
# PUT /ontology/versions/{id} — update draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_update_draft_ok(async_client, domain_expert_headers, db_session):
    ov = await _create_version(
        db_session, version="0.1.0", status="draft", changelog="old",
    )
    await db_session.commit()

    resp = await async_client.put(
        f"{BASE}/{ov.id}",
        json={"changelog": "updated", "ontology_data": {"new": True}},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["changelog"] == "updated"
    assert data["ontology_data"] == {"new": True}


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_update_published_rejected(async_client, domain_expert_headers, db_session):
    ov = await _create_version(db_session, version="0.1.0", status="published")
    await db_session.commit()

    resp = await async_client.put(
        f"{BASE}/{ov.id}",
        json={"changelog": "nope"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422
    assert "draft" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_update_nonexistent(async_client, domain_expert_headers):
    resp = await async_client.put(
        f"{BASE}/{uuid.uuid4()}",
        json={"changelog": "nope"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /ontology/versions/{id}/publish — auto-semver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_first_version(async_client, domain_expert_headers, db_session):
    ov = await _create_version(db_session, version="0.1.0", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov.id}/publish",
        json={"changelog": "first release"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "published"
    assert data["version"] == "0.1.0"
    assert data["changelog"] == "first release"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_patch_bump(async_client, domain_expert_headers, db_session):
    await _create_version(db_session, version="0.1.0", status="published")
    # The draft needs a version string distinct from the published 0.1.0 row:
    # ontology_versions.version is UNIQUE. publish_version() recomputes the
    # version from the latest *published* row and overwrites this placeholder,
    # so its value does not affect the assertion below.
    ov2 = await _create_version(db_session, version="0.0.0-draft", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov2.id}/publish",
        json={"changelog": "patch bump", "bump": "patch"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == "0.1.1"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_minor_bump(async_client, domain_expert_headers, db_session):
    await _create_version(db_session, version="0.1.0", status="published")
    # The draft needs a version string distinct from the published 0.1.0 row:
    # ontology_versions.version is UNIQUE. publish_version() recomputes the
    # version from the latest *published* row and overwrites this placeholder,
    # so its value does not affect the assertion below.
    ov2 = await _create_version(db_session, version="0.0.0-draft", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov2.id}/publish",
        json={"changelog": "minor bump", "bump": "minor"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == "0.2.0"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_major_bump(async_client, domain_expert_headers, db_session):
    await _create_version(db_session, version="0.1.0", status="published")
    # The draft needs a version string distinct from the published 0.1.0 row:
    # ontology_versions.version is UNIQUE. publish_version() recomputes the
    # version from the latest *published* row and overwrites this placeholder,
    # so its value does not affect the assertion below.
    ov2 = await _create_version(db_session, version="0.0.0-draft", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov2.id}/publish",
        json={"changelog": "major bump", "bump": "major"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.0.0"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_concurrent_version_conflict_returns_409(
    async_client, domain_expert_headers, db_session, monkeypatch,
):
    """Concurrent publish that hits unique version constraint returns 409 (NFM-2634)."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    await _create_version(db_session, version="0.1.0", status="published")
    ov = await _create_version(db_session, version="0.0.0-draft", status="draft")
    await db_session.commit()

    # Patch session.commit to raise IntegrityError on first call, simulating
    # a concurrent publish that already inserted the same version string.
    real_commit = db_session.commit
    call_count = 0

    async def _commit_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SAIntegrityError(
                "insert",
                {},
                Exception(
                    'duplicate key value violates unique constraint '
                    '"uq_ontology_versions_version"'
                ),
            )
        return await real_commit(*args, **kwargs)

    monkeypatch.setattr(db_session, "commit", _commit_side_effect)

    resp = await async_client.post(
        f"{BASE}/{ov.id}/publish",
        json={"changelog": "concurrent publish"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "version" in detail
    assert "conflict" in detail or "already exists" in detail


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_rejects_without_changelog(
    async_client, domain_expert_headers, db_session,
):
    ov = await _create_version(db_session, version="0.1.0", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov.id}/publish",
        json={"changelog": ""},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_publish_draft_only(async_client, domain_expert_headers, db_session):
    ov = await _create_version(db_session, version="0.1.0", status="published")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov.id}/publish",
        json={"changelog": "should fail"},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422
    assert "draft" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /ontology/versions/upload — validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_valid(async_client, domain_expert_headers, db_session):
    resp = await async_client.post(
        f"{BASE}/upload",
        json={
            "ontology_data": _VALID_ONTOLOGY,
            "changelog": "upload test",
        },
        headers=domain_expert_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["ontology_data"] == _VALID_ONTOLOGY


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_missing_entity_types(async_client, domain_expert_headers):
    resp = await async_client.post(
        f"{BASE}/upload",
        json={"ontology_data": {"relation_types": []}},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422
    assert "entity_types" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_missing_relation_types(async_client, domain_expert_headers):
    resp = await async_client.post(
        f"{BASE}/upload",
        json={"ontology_data": {"entity_types": []}},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422
    assert "relation_types" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_missing_both_keys(async_client, domain_expert_headers):
    resp = await async_client.post(
        f"{BASE}/upload",
        json={"ontology_data": {"other": "data"}},
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_upload_non_expert_forbidden(async_client, editor_headers):
    resp = await async_client.post(
        f"{BASE}/upload",
        json={"ontology_data": _VALID_ONTOLOGY},
        headers=editor_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /ontology/versions/{id}/deprecate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_deprecate_published(async_client, domain_expert_headers, db_session):
    ov = await _create_version(db_session, version="1.0.0", status="published")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov.id}/deprecate",
        headers=domain_expert_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deprecated"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_deprecate_draft_rejected(async_client, domain_expert_headers, db_session):
    ov = await _create_version(db_session, version="0.1.0", status="draft")
    await db_session.commit()

    resp = await async_client.post(
        f"{BASE}/{ov.id}/deprecate",
        headers=domain_expert_headers,
    )
    assert resp.status_code == 422
    assert "published" in resp.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_deprecate_nonexistent(async_client, domain_expert_headers):
    resp = await async_client.post(
        f"{BASE}/{uuid.uuid4()}/deprecate",
        headers=domain_expert_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin can also access write endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_admin_can_create_draft(async_client, admin_headers, db_session):
    """Admin role is also accepted (included in require_domain_expert)."""
    resp = await async_client.post(
        BASE, json={"changelog": "admin draft"}, headers=admin_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_reviewer_cannot_create_draft(async_client, reviewer_headers):
    """Reviewer role gets 403."""
    resp = await async_client.post(
        BASE, json={"changelog": "nope"}, headers=reviewer_headers,
    )
    assert resp.status_code == 403
