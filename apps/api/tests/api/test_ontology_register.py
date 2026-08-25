"""Unit + integration tests for POST /api/ontology/versions (NFM-3591).

Covers the register-version API surface:

* happy path — server fetches ``source_url`` body, computes SHA-256,
  matches the supplied ``checksum``, and inserts a new ``OntologyVersion``
  row whose ``version_tag`` (API name) maps to the model's ``version``
  (semver string).  ``source_url`` and ``checksum`` are persisted in
  ``ontology_data`` so no destructive migration is required.
* checksum mismatch → 400 ``checksum_mismatch`` with diagnostic detail
* missing ``source_url`` → 400 ``source_url_required``
* duplicate ``version_tag`` → 409 ``version_tag_exists``
* "No destructive change" AC: existing ``k_entity_types`` /
  ``k_relation_types`` rows are not mutated by registration.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from nfm_db.models.ontology_version import OntologyVersion

BASE = "/api/ontology/versions"

_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_prefixed(body: bytes) -> str:
    """Return the canonical ``sha256:<hex>`` checksum of ``body``."""
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


async def _seed_existing_version(
    session,
    *,
    version: str = "v0",
    created_by: uuid.UUID | None = None,
) -> OntologyVersion:
    """Persist an OntologyVersion row so we can test duplicate-detection."""
    ov = OntologyVersion(
        version=version,
        status="published",
        created_by=created_by or _SEED_USER_ID,
        ontology_data={"source_url": None, "checksum": "sha256:baseline"},
    )
    session.add(ov)
    await session.commit()
    await session.refresh(ov)
    return ov


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_version_happy_path(db_session, async_client):
    """Server verifies SHA-256 of source body and inserts a new version."""
    body = b'{"entity_types": [], "relation_types": []}'
    checksum = _sha256_prefixed(body)
    payload = {
        "version_tag": "v1",
        "created_by": "human@example.com",
        "source_url": "https://example.invalid/ontology.json",
        "checksum": checksum,
    }

    # Patch the URL fetcher so the test never hits the network.
    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ) as fetch_mock:
        resp = await async_client.post(BASE, json=payload)

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["version_tag"] == "v1"
    assert data["created_by"] == "human@example.com"
    assert data["source_url"] == payload["source_url"]
    assert data["checksum"] == checksum
    # Server MUST have actually fetched the URL exactly once.
    fetch_mock.assert_awaited_once_with(payload["source_url"])

    # Row persisted in ontology_data so we don't mutate the schema.
    result = await db_session.execute(
        select(OntologyVersion).where(OntologyVersion.version == "v1")
    )
    ov = result.scalar_one()
    assert ov.ontology_data is not None
    assert ov.ontology_data["source_url"] == payload["source_url"]
    assert ov.ontology_data["checksum"] == checksum


@pytest.mark.asyncio
async def test_register_version_does_not_mutate_type_tables(
    db_session, async_client
):
    """Register must not touch existing ``k_entity_types`` /
    ``k_relation_types`` rows (no destructive change AC)."""
    # The db_session fixture creates the kg tables via ``Base.metadata.create_all``
    # but no rows; assert the count remains zero after the call.
    from sqlalchemy import text

    before = await db_session.execute(
        text("SELECT COUNT(*) FROM kg_entity_types")
    )
    before_count = before.scalar_one()
    before_rel = await db_session.execute(
        text("SELECT COUNT(*) FROM kg_relation_types")
    )
    before_rel_count = before_rel.scalar_one()

    body = b"{}"
    payload = {
        "version_tag": "v1",
        "created_by": "human@example.com",
        "source_url": "https://example.invalid/ontology.json",
        "checksum": _sha256_prefixed(body),
    }
    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        resp = await async_client.post(BASE, json=payload)

    assert resp.status_code == 201

    after = await db_session.execute(
        text("SELECT COUNT(*) FROM kg_entity_types")
    )
    after_rel = await db_session.execute(
        text("SELECT COUNT(*) FROM kg_relation_types")
    )
    assert after.scalar_one() == before_count
    assert after_rel.scalar_one() == before_rel_count


# ---------------------------------------------------------------------------
# Checksum mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_version_checksum_mismatch(db_session, async_client):
    body = b"the-real-body"
    payload = {
        "version_tag": "v1",
        "created_by": "human@example.com",
        "source_url": "https://example.invalid/ontology.json",
        "checksum": "sha256:" + ("0" * 64),  # wrong on purpose
    }

    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        resp = await async_client.post(BASE, json=payload)

    assert resp.status_code == 400, resp.text
    data = resp.json()
    # The spec demands an ``error: 'checksum_mismatch'`` envelope; the
    # route wraps the structured payload in ``detail`` as a dict.
    detail = data.get("detail", "")
    if isinstance(detail, dict):
        body_text = (
            detail.get("error", "") or ""
        ) + (detail.get("detail", "") or "")
    else:
        body_text = (detail or "") + (data.get("error", "") or "")
    assert "checksum" in body_text.lower()
    # No row should have been inserted.
    result = await db_session.execute(
        select(OntologyVersion).where(OntologyVersion.version == "v1")
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Missing source_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_version_missing_source_url(db_session, async_client):
    payload: dict[str, Any] = {
        "version_tag": "v1",
        "created_by": "human@example.com",
        "checksum": "sha256:" + ("a" * 64),
    }

    resp = await async_client.post(BASE, json=payload)

    assert resp.status_code == 400, resp.text
    detail = resp.json()
    detail_obj = detail.get("detail", "")
    if isinstance(detail_obj, dict):
        body_text = (detail_obj.get("error", "") or "") + (
            detail_obj.get("detail", "") or ""
        )
    else:
        body_text = detail_obj or ""
    assert "source_url" in body_text.lower()
    # No row inserted.
    result = await db_session.execute(
        select(OntologyVersion).where(OntologyVersion.version == "v1")
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Duplicate version_tag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_version_duplicate_tag(db_session, async_client):
    await _seed_existing_version(db_session, version="v0")

    body = b"{}"
    payload = {
        "version_tag": "v0",
        "created_by": "human@example.com",
        "source_url": "https://example.invalid/ontology.json",
        "checksum": _sha256_prefixed(body),
    }
    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        resp = await async_client.post(BASE, json=payload)

    assert resp.status_code == 409, resp.text
    detail = resp.json()
    detail_obj = detail.get("detail", "")
    if isinstance(detail_obj, dict):
        body_text = (detail_obj.get("error", "") or "") + (
            detail_obj.get("detail", "") or ""
        )
    else:
        body_text = detail_obj or ""
    assert "version_tag" in body_text.lower()

    # Only one row for ``v0`` should remain (the seed).
    result = await db_session.execute(
        select(OntologyVersion).where(OntologyVersion.version == "v0")
    )
    assert len(result.scalars().all()) == 1


# ---------------------------------------------------------------------------
# Service-layer unit tests (no FastAPI)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_register_persists_atomically(db_session):
    """The service should commit exactly one new row inside a single tx."""
    from nfm_db.services.ontology_register import register_ontology_version

    body = b"abc"
    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        ov = await register_ontology_version(
            db_session,
            version_tag="v1",
            created_by_user_id=_SEED_USER_ID,
            created_by_display="human@example.com",
            source_url="https://example.invalid/o.json",
            checksum=_sha256_prefixed(body),
        )

    assert ov.id is not None
    assert ov.version == "v1"
    assert ov.created_by == _SEED_USER_ID
    assert ov.ontology_data is not None
    assert ov.ontology_data["source_url"].endswith("o.json")
    assert ov.ontology_data["checksum"] == _sha256_prefixed(body)
    assert ov.ontology_data["created_by_raw"] == "human@example.com"


@pytest.mark.asyncio
async def test_service_register_rejects_mismatched_checksum(db_session):
    from nfm_db.services import ontology_register as svc

    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=b"real"),
    ):
        with pytest.raises(svc.ChecksumMismatchError) as excinfo:
            await svc.register_ontology_version(
                db_session,
                version_tag="v1",
                created_by_user_id=_SEED_USER_ID,
                created_by_display="human@example.com",
                source_url="https://example.invalid/o.json",
                checksum="sha256:" + ("f" * 64),
            )

    expected = _sha256_prefixed(b"real")
    assert expected in str(excinfo.value)
    assert "sha256:" + ("f" * 64) in str(excinfo.value)


@pytest.mark.asyncio
async def test_service_register_rejects_missing_source_url(db_session):
    from nfm_db.services import ontology_register as svc

    with pytest.raises(svc.SourceUrlRequiredError):
        await svc.register_ontology_version(
            db_session,
            version_tag="v1",
            created_by_user_id=_SEED_USER_ID,
            created_by_display="human@example.com",
            source_url=None,
            checksum="sha256:" + ("a" * 64),
        )


@pytest.mark.asyncio
async def test_service_register_rejects_duplicate_tag(db_session):
    from nfm_db.services import ontology_register as svc

    await _seed_existing_version(db_session, version="v0")
    body = b"{}"
    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        with pytest.raises(svc.VersionTagExistsError):
            await svc.register_ontology_version(
                db_session,
                version_tag="v0",
                created_by_user_id=_SEED_USER_ID,
                created_by_display="human@example.com",
                source_url="https://example.invalid/o.json",
                checksum=_sha256_prefixed(body),
            )
