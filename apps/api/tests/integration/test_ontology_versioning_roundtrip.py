"""Round-trip integration test for ontology versioning (NFM-3592 / NFM-2868-P1-2-d).

Exercises the full ontology-versioning flow end-to-end:

    v0 baseline survives → register v1 (via POST /api/ontology/versions)
    → register v2 → loader picks up v2 types as the active set
    → v1 still queryable.

This is the single test that fails if any of Sibling 1 (Schema),
Sibling 2 (Loader), or Sibling 3 (Register endpoint) breaks version
isolation.

Acceptance-criterion checklist (one focused test per line):

- [x] test_roundtrip_v0_baseline_present
- [x] test_roundtrip_register_v1_resolvable_under_loader
- [x] test_roundtrip_register_v2_picked_up_as_active_set
- [x] test_roundtrip_v1_queryable_after_v2_registered
- [x] test_roundtrip_no_data_loss_baseline_and_original_types_preserved
- [x] test_roundtrip_unknown_version_reference_rejected_by_loader
- [x] test_roundtrip_checksum_mismatch_returns_400

Files exercised (sibling branches merged into NFM-3592 base):

- ``apps/api/migrations/versions/044_add_ontology_version.py``
  (v0 baseline; created by Sibling 1)
- ``apps/api/migrations/versions/055_add_ontology_version_fk_to_type_tables.py``
  (FK + backfill; created by Sibling 1)
- ``apps/api/src/nfm_db/models/ontology.py``
  (``KEntityType``, ``KRelationType`` with ``ontology_version_id`` FK)
- ``apps/api/src/nfm_db/models/ontology_version.py``
  (``OntologyVersion`` model)
- ``apps/api/src/nfm_db/services/ontology_loader.py``
  (``load_ontology_types``; created by Sibling 2)
- ``apps/api/src/nfm_db/services/ontology_register.py``
  (``register_ontology_version``; created by Sibling 3)
- ``apps/api/src/nfm_db/api/routes/ontology.py``
  (``POST /api/ontology/versions``; created by Sibling 3)
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from nfm_db.models import KEntityType, KRelationType, OntologyVersion
from nfm_db.services.ontology_loader import (
    OntologyVersionNotFoundError,
    load_ontology_types,
)

# ---------------------------------------------------------------------------
# Constants — match migration 044 (the v0 baseline) and the 044 seeds' author.
# ---------------------------------------------------------------------------

V0_VERSION_TAG = "0.1.0"
V0_AUTHOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
V0_AUTHOR_EMAIL = "system@nucpot.internal"
SYSTEM_USERNAME = "system"

REGISTER_URL = "/api/ontology/versions"

# v1 = first user-registered generation.
V1_ENTITY_TYPES: tuple[str, ...] = ("Material", "Sample")
V1_RELATION_TYPES: tuple[str, ...] = ("hasProperty",)

# v2 = second user-registered generation; disjoint name set so we can prove
# the loader returns v2-specific rows, not v1 rows that happen to share names.
V2_ENTITY_TYPES: tuple[str, ...] = ("MaterialV2", "SampleV2", "ProcessV2")
V2_RELATION_TYPES: tuple[str, ...] = ("hasPropertyV2",)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_prefixed(body: bytes) -> str:
    """Return the canonical ``sha256:<hex>`` checksum of ``body``."""
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


async def _seed_system_user(session: AsyncIterator) -> None:
    """Insert the migration-044 system author so the FK resolves.

    Real users are seeded by ``admin_user`` / similar fixtures, but v0's
    baseline author is the immutable SYSTEM_USER_ID used by the
    migration.  We use the SQLAlchemy ORM ``User`` model so that every
    NOT NULL server default (``is_active``, ``is_service_account``) is
    applied; a raw ``INSERT`` would silently fail on
    ``is_service_account`` (NOT NULL, no Python default).
    """
    # Guard against re-insert when the fixture runs against a session
    # that already contains the system user (defensive — each test gets
    # its own in-memory DB, so this is belt-and-braces).
    from sqlalchemy import select

    from nfm_db.models.user import User

    existing = (
        await session.execute(
            select(User).where(User.id == V0_AUTHOR_ID),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    system_user = User(
        id=V0_AUTHOR_ID,
        username=SYSTEM_USERNAME,
        email=V0_AUTHOR_EMAIL,
        full_name="NucPot System",
        hashed_password="!",  # unusable per migration 044
        is_active=False,
        is_service_account=True,
    )
    session.add(system_user)
    await session.commit()


async def _seed_v0_baseline(session: AsyncIterator) -> OntologyVersion:
    """Insert the v0 baseline row that migration 044 produces.

    The SQLite test environment runs ``Base.metadata.create_all`` rather
    than alembic, so we replicate the 044 seed here — same identity, same
    status, same system author.
    """
    ov = OntologyVersion(
        version=V0_VERSION_TAG,
        status="published",
        changelog="Initial ontology version.",
        created_by=V0_AUTHOR_ID,
        ontology_data={},
    )
    session.add(ov)
    await session.commit()
    await session.refresh(ov)
    return ov


async def _seed_v0_type_rows(
    session: AsyncIterator,
    *,
    v0_baseline_id: uuid.UUID,
) -> None:
    """Insert the original kg_entity_types / kg_relation_types rows.

    These rows existed pre-versioning and should be backfilled to v0 by
    migration 055.  In the test SQLite env we replicate the backfill by
    inserting them with ``ontology_version_id == v0_baseline_id``.
    """
    for name in ("LegacyMaterial",):
        session.add(
            KEntityType(name=name, ontology_version_id=v0_baseline_id),
        )
    for name in ("legacyHasRef",):
        session.add(
            KRelationType(name=name, ontology_version_id=v0_baseline_id),
        )
    await session.commit()


async def _seed_type_rows(
    session: AsyncIterator,
    *,
    ontology_version_id: uuid.UUID,
    entity_names: tuple[str, ...],
    relation_names: tuple[str, ...],
) -> None:
    """Insert ``KEntityType`` / ``KRelationType`` rows for a version.

    Simulates the loader's job for a given ``ontology_version_id`` — we
    use this to make v1/v2 resolvable under their own versions without
    invoking the loader's own write path.
    """
    for name in entity_names:
        session.add(
            KEntityType(name=name, ontology_version_id=ontology_version_id),
        )
    for name in relation_names:
        session.add(
            KRelationType(name=name, ontology_version_id=ontology_version_id),
        )
    await session.commit()


def _register_payload(
    *,
    version_tag: str,
    body_bytes: bytes,
    created_by: str = "integration-test@example.com",
) -> dict[str, Any]:
    """Build a valid POST /api/ontology/versions request body."""
    return {
        "version_tag": version_tag,
        "created_by": created_by,
        "source_url": f"file:///tmp/{version_tag}.json",
        "checksum": _sha256_prefixed(body_bytes),
    }


async def _post_register(
    async_client,
    *,
    version_tag: str,
    body_bytes: bytes,
    fetcher_mock_target: str,
) -> dict[str, Any]:
    """POST a register-version request with the network call mocked out."""
    payload = _register_payload(version_tag=version_tag, body_bytes=body_bytes)
    with patch(fetcher_mock_target, AsyncMock(return_value=body_bytes)):
        resp = await async_client.post(REGISTER_URL, json=payload)
    return {"response": resp, "payload": payload}


# ---------------------------------------------------------------------------
# v0 baseline fixture — bootstrap the test DB the way migration 044/055 do.
# ---------------------------------------------------------------------------


@pytest.fixture
async def v0_baseline(db_session: AsyncIterator) -> OntologyVersion:
    """Seed the v0 baseline + system user + original type rows.

    Mirrors the end state of migrations 044 + 055 (in the SQLite test
    environment) so the round-trip can exercise the post-migration shape
    directly without needing alembic.
    """
    await _seed_system_user(db_session)
    v0 = await _seed_v0_baseline(db_session)
    await _seed_v0_type_rows(db_session, v0_baseline_id=v0.id)
    return v0


# ---------------------------------------------------------------------------
# Acceptance-criterion tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_v0_baseline_present(
    db_session: AsyncIterator,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 1: bootstrap with v0 baseline (auto-applied by Sibling 1 migration).

    The fixture replicates the migration 044/055 end state.  We assert the
    v0 row is present with the right identity and status.
    """
    # v0 row exists with the expected identity.
    result = await db_session.execute(
        select(OntologyVersion).where(OntologyVersion.version == V0_VERSION_TAG),
    )
    v0 = result.scalar_one()
    assert v0.id == v0_baseline.id
    assert v0.status == "published"
    assert v0.created_by == V0_AUTHOR_ID

    # The original pre-versioning type rows are present and bound to v0.
    legacy_entity = (
        await db_session.execute(
            select(KEntityType).where(KEntityType.name == "LegacyMaterial"),
        )
    ).scalar_one()
    assert legacy_entity.ontology_version_id == v0.id

    legacy_relation = (
        await db_session.execute(
            select(KRelationType).where(KRelationType.name == "legacyHasRef"),
        )
    ).scalar_one()
    assert legacy_relation.ontology_version_id == v0.id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_register_v1_resolvable_under_loader(
    db_session: AsyncIterator,
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 2: register v1 (via the endpoint from Sibling 3) -> types resolvable under v1.

    We POST /api/ontology/versions to register v1, then seed type rows
    pointing at v1, then call ``load_ontology_types(v1_id)`` and assert
    the v1-specific entity and relation names are returned.
    """
    v1_body = b'{"entity_types": ["Material","Sample"], "relation_types": ["hasProperty"]}'
    result = await _post_register(
        authenticated_client,
        version_tag="v1",
        body_bytes=v1_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    resp = result["response"]
    assert resp.status_code == 201, resp.text
    v1_data = resp.json()
    assert v1_data["version_tag"] == "v1"
    v1_id = uuid.UUID(v1_data["id"])

    # Seed v1-bound type rows (the loader's write side is out of scope
    # for Sibling 3; we exercise its read side directly here).
    await _seed_type_rows(
        db_session,
        ontology_version_id=v1_id,
        entity_names=V1_ENTITY_TYPES,
        relation_names=V1_RELATION_TYPES,
    )

    # Loader returns v1-specific rows only.
    entities, relations = await load_ontology_types(db_session, v1_id)
    entity_names = {e.name for e in entities}
    relation_names = {r.name for r in relations}
    assert entity_names == set(V1_ENTITY_TYPES)
    assert relation_names == set(V1_RELATION_TYPES)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_register_v2_picked_up_as_active_set(
    db_session: AsyncIterator,
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 3: register v2 -> loader picks up v2 types as the active set.

    After v2 is registered and its type rows are seeded, calling
    ``load_ontology_types(v2_id)`` MUST return v2-specific entity/relation
    names — not v1's, not v0's.  This proves the loader is version-aware.
    """
    # Register v1 (precondition for the v0→v1→v2 progression).
    v1_body = b'{"entity_types": ["Material","Sample"], "relation_types": ["hasProperty"]}'
    v1_resp = await _post_register(
        authenticated_client,
        version_tag="v1",
        body_bytes=v1_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v1_resp["response"].status_code == 201, v1_resp["response"].text
    v1_id = uuid.UUID(v1_resp["response"].json()["id"])

    # Register v2.
    v2_body = b'{"entity_types": ["MaterialV2","SampleV2","ProcessV2"], "relation_types": ["hasPropertyV2"]}'
    v2_resp = await _post_register(
        authenticated_client,
        version_tag="v2",
        body_bytes=v2_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v2_resp["response"].status_code == 201, v2_resp["response"].text
    v2_id = uuid.UUID(v2_resp["response"].json()["id"])

    # Seed v1 + v2 type rows.
    await _seed_type_rows(
        db_session,
        ontology_version_id=v1_id,
        entity_names=V1_ENTITY_TYPES,
        relation_names=V1_RELATION_TYPES,
    )
    await _seed_type_rows(
        db_session,
        ontology_version_id=v2_id,
        entity_names=V2_ENTITY_TYPES,
        relation_names=V2_RELATION_TYPES,
    )

    # Loader picks up v2's types when asked for v2.
    entities, relations = await load_ontology_types(db_session, v2_id)
    entity_names = {e.name for e in entities}
    relation_names = {r.name for r in relations}
    assert entity_names == set(V2_ENTITY_TYPES)
    assert relation_names == set(V2_RELATION_TYPES)
    # Loader MUST NOT have leaked v1 names into the v2 result.
    assert not (entity_names & set(V1_ENTITY_TYPES))
    assert not (relation_names & set(V1_RELATION_TYPES))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_v1_queryable_after_v2_registered(
    db_session: AsyncIterator,
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 4: v1 still queryable after v2 registered (no destructive change).

    Registering v2 MUST NOT mutate v1's ontology_version_id on existing
    rows or otherwise prevent v1's type rows from loading.  This is the
    core "rollback story" guarantee from the parent NFM-3544 description.
    """
    # Register v1 then v2.
    v1_body = b'{"entity_types": ["Material","Sample"], "relation_types": ["hasProperty"]}'
    v1_resp = await _post_register(
        authenticated_client,
        version_tag="v1",
        body_bytes=v1_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v1_resp["response"].status_code == 201
    v1_id = uuid.UUID(v1_resp["response"].json()["id"])

    v2_body = b'{"entity_types": ["MaterialV2","SampleV2","ProcessV2"], "relation_types": ["hasPropertyV2"]}'
    v2_resp = await _post_register(
        authenticated_client,
        version_tag="v2",
        body_bytes=v2_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v2_resp["response"].status_code == 201
    v2_id = uuid.UUID(v2_resp["response"].json()["id"])

    # Seed v1 + v2 type rows.
    await _seed_type_rows(
        db_session,
        ontology_version_id=v1_id,
        entity_names=V1_ENTITY_TYPES,
        relation_names=V1_RELATION_TYPES,
    )
    await _seed_type_rows(
        db_session,
        ontology_version_id=v2_id,
        entity_names=V2_ENTITY_TYPES,
        relation_names=V2_RELATION_TYPES,
    )

    # After v2 is the latest registered version, v1's types MUST still
    # be loadable.  The "loader picks v2 as the active set" language in
    # the AC means: when asked for v2, you get v2; when asked for v1, you
    # still get v1 — not "v1 is now hidden behind v2".
    v1_entities, v1_relations = await load_ontology_types(db_session, v1_id)
    v1_entity_names = {e.name for e in v1_entities}
    v1_relation_names = {r.name for r in v1_relations}
    assert v1_entity_names == set(V1_ENTITY_TYPES)
    assert v1_relation_names == set(V1_RELATION_TYPES)

    # The ontology_versions row for v1 is also still present and unchanged.
    v1_row = (
        await db_session.execute(
            select(OntologyVersion).where(OntologyVersion.version == "v1"),
        )
    ).scalar_one()
    assert v1_row.id == v1_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_no_data_loss_baseline_and_original_types_preserved(
    db_session: AsyncIterator,
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 5: v0 baseline row + every original type row still present (no data loss).

    After registering v1 AND v2, the v0 baseline row and the original
    pre-versioning type rows MUST still exist and still point at v0.
    Registering new versions is additive, not destructive.
    """
    # Snapshot the original pre-v1 type-row state.
    original_entities = (
        await db_session.execute(
            select(KEntityType).where(KEntityType.name == "LegacyMaterial"),
        )
    ).scalars().all()
    original_relations = (
        await db_session.execute(
            select(KRelationType).where(KRelationType.name == "legacyHasRef"),
        )
    ).scalars().all()
    assert len(original_entities) == 1
    assert len(original_relations) == 1
    original_entity_id = original_entities[0].id
    original_relation_id = original_relations[0].id
    original_entity_ov_id = original_entities[0].ontology_version_id
    original_relation_ov_id = original_relations[0].ontology_version_id

    # Register v1 and v2.
    v1_body = b'{"entity_types": ["Material","Sample"], "relation_types": ["hasProperty"]}'
    v1_resp = await _post_register(
        authenticated_client,
        version_tag="v1",
        body_bytes=v1_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v1_resp["response"].status_code == 201
    v1_id = uuid.UUID(v1_resp["response"].json()["id"])
    v2_body = b'{"entity_types": ["MaterialV2","SampleV2","ProcessV2"], "relation_types": ["hasPropertyV2"]}'
    v2_resp = await _post_register(
        authenticated_client,
        version_tag="v2",
        body_bytes=v2_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v2_resp["response"].status_code == 201
    v2_id = uuid.UUID(v2_resp["response"].json()["id"])
    await _seed_type_rows(
        db_session,
        ontology_version_id=v1_id,
        entity_names=V1_ENTITY_TYPES,
        relation_names=V1_RELATION_TYPES,
    )
    await _seed_type_rows(
        db_session,
        ontology_version_id=v2_id,
        entity_names=V2_ENTITY_TYPES,
        relation_names=V2_RELATION_TYPES,
    )

    # v0 baseline row still present, unchanged.
    v0_after = (
        await db_session.execute(
            select(OntologyVersion).where(OntologyVersion.version == V0_VERSION_TAG),
        )
    ).scalar_one()
    assert v0_after.id == v0_baseline.id
    assert v0_after.status == "published"

    # Every original type row still present, bound to v0.
    entity_after = (
        await db_session.execute(
            select(KEntityType).where(KEntityType.id == original_entity_id),
        )
    ).scalar_one()
    assert entity_after.ontology_version_id == original_entity_ov_id == v0_after.id

    relation_after = (
        await db_session.execute(
            select(KRelationType).where(KRelationType.id == original_relation_id),
        )
    ).scalar_one()
    assert relation_after.ontology_version_id == original_relation_ov_id == v0_after.id

    # All three ontology_versions rows coexist: v0 + v1 + v2.
    all_versions = (
        await db_session.execute(select(OntologyVersion))
    ).scalars().all()
    version_tags = {v.version for v in all_versions}
    assert {V0_VERSION_TAG, "v1", "v2"}.issubset(version_tags)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_unknown_version_reference_rejected_by_loader(
    db_session: AsyncIterator,
) -> None:
    """AC 6: unknown version reference is rejected by the loader.

    The loader MUST raise ``OntologyVersionNotFoundError`` for any
    ``ontology_version_id`` that does not correspond to a row in
    ``ontology_versions`` — both for a brand-new UUID and for ``None``
    (NULL pointer) on a non-baseline load.
    """
    unknown_id = uuid.uuid4()

    with pytest.raises(OntologyVersionNotFoundError) as exc_info:
        await load_ontology_types(db_session, unknown_id)
    assert "OntologyVersion not found" in str(exc_info.value)

    # NULL pointer is also rejected on a non-baseline load — same error.
    with pytest.raises(OntologyVersionNotFoundError) as exc_info_none:
        await load_ontology_types(db_session, None)
    assert "NULL" in str(exc_info_none.value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_checksum_mismatch_returns_400(
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """AC 7: checksum mismatch on registration -> 400.

    When the supplied ``checksum`` does not match the SHA-256 of the
    fetched source body, the endpoint MUST return HTTP 400 with the
    structured ``checksum_mismatch`` error envelope from the API spec.
    """
    body = b'{"entity_types": ["Material"], "relation_types": ["hasProperty"]}'
    correct_checksum = _sha256_prefixed(body)
    # Flip the last hex char so the checksum is structurally valid but wrong.
    flipped_hex = ("0" if correct_checksum[-1] != "0" else "f") + correct_checksum[-63:]
    bad_checksum = correct_checksum[:-64] + flipped_hex
    payload = {
        "version_tag": "v_bad_checksum",
        "created_by": "integration-test@example.com",
        "source_url": "file:///tmp/bad-checksum.json",
        "checksum": bad_checksum,
    }

    with patch(
        "nfm_db.services.ontology_register._fetch_source_body",
        AsyncMock(return_value=body),
    ):
        resp = await authenticated_client.post(REGISTER_URL, json=payload)

    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail") or {}
    # The route layer wraps the service error in the spec'd envelope.
    assert detail.get("error") == "checksum_mismatch", detail
    # Detail string should name both expected and observed checksums.
    assert "expected" in detail.get("detail", "").lower()
    assert "got" in detail.get("detail", "").lower()


# ---------------------------------------------------------------------------
# Bonus — single combined "round-trip" smoke test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_roundtrip_full_progression_v0_to_v2(
    db_session: AsyncIterator,
    authenticated_client,
    v0_baseline: OntologyVersion,
) -> None:
    """End-to-end smoke test of the full progression: v0 → v1 → v2.

    Walks the exact body-AC sequence as one test: register v1, register
    v2, confirm loader picks v2, confirm v1 is still queryable, confirm
    v0 baseline is preserved.  Equivalent to running ACs 2-5 in series
    and verifying the steady state at the end.
    """
    # v1
    v1_body = b'{"entity_types": ["Material","Sample"], "relation_types": ["hasProperty"]}'
    v1_resp = await _post_register(
        authenticated_client,
        version_tag="v1",
        body_bytes=v1_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v1_resp["response"].status_code == 201
    v1_id = uuid.UUID(v1_resp["response"].json()["id"])

    # v2
    v2_body = b'{"entity_types": ["MaterialV2","SampleV2","ProcessV2"], "relation_types": ["hasPropertyV2"]}'
    v2_resp = await _post_register(
        authenticated_client,
        version_tag="v2",
        body_bytes=v2_body,
        fetcher_mock_target="nfm_db.services.ontology_register._fetch_source_body",
    )
    assert v2_resp["response"].status_code == 201
    v2_id = uuid.UUID(v2_resp["response"].json()["id"])

    # Seed type rows for v1 and v2.
    await _seed_type_rows(
        db_session,
        ontology_version_id=v1_id,
        entity_names=V1_ENTITY_TYPES,
        relation_names=V1_RELATION_TYPES,
    )
    await _seed_type_rows(
        db_session,
        ontology_version_id=v2_id,
        entity_names=V2_ENTITY_TYPES,
        relation_names=V2_RELATION_TYPES,
    )

    # Loader: v2 is the active set.
    active_entities, active_relations = await load_ontology_types(db_session, v2_id)
    assert {e.name for e in active_entities} == set(V2_ENTITY_TYPES)
    assert {r.name for r in active_relations} == set(V2_RELATION_TYPES)

    # Loader: v1 still queryable.
    v1_entities, v1_relations = await load_ontology_types(db_session, v1_id)
    assert {e.name for e in v1_entities} == set(V1_ENTITY_TYPES)
    assert {r.name for r in v1_relations} == set(V1_RELATION_TYPES)

    # v0 baseline row preserved.
    v0_after = (
        await db_session.execute(
            select(OntologyVersion).where(OntologyVersion.version == V0_VERSION_TAG),
        )
    ).scalar_one()
    assert v0_after.id == v0_baseline.id
