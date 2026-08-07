"""Integration tests for ``/api/v1/extraction-gaps`` endpoints (NFM-2599).

Covers:
  - GET   /api/v1/extraction-gaps              — paginated + filtered list
  - GET   /api/v1/extraction-gaps/{gap_id}     — detail with chunk source_reference
  - PATCH /api/v1/extraction-gaps/{gap_id}/status — status transitions + 409 + 404

OpenAPI registration is asserted via ``app.openapi()``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.main import app
from nfm_db.models import (
    ExtractionChunk,
    ExtractionGap,
    ExtractionJob,
    OntologyVersion,
    User,
)
from nfm_db.models.user import BlogRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    session: AsyncSession,
    *,
    role: BlogRole = BlogRole.DOMAIN_EXPERT,
) -> User:
    """Insert a minimal user (FK target for OntologyVersion.created_by)."""
    user = User(
        id=uuid.uuid4(),
        username=f"seed_{role.value}_{uuid.uuid4().hex[:8]}",
        email=f"seed_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="hashed",
        blog_role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ontology(
    session: AsyncSession,
    *,
    version_id: uuid.UUID | None = None,
) -> OntologyVersion:
    """Insert a minimal OntologyVersion row."""
    user = await _seed_user(session)
    ov = OntologyVersion(
        id=version_id or uuid.uuid4(),
        version=f"1.0.0-{uuid.uuid4().hex[:8]}",
        status="published",
        created_by=user.id,
    )
    session.add(ov)
    await session.flush()
    return ov


async def _seed_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
) -> ExtractionJob:
    """Insert a minimal ExtractionJob row (FK target for ExtractionChunk)."""
    job = ExtractionJob(
        id=job_id or uuid.uuid4(),
        status="completed",
    )
    session.add(job)
    await session.flush()
    return job


async def _seed_chunk(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    source_reference: str = "page-3",
) -> ExtractionChunk:
    """Insert a minimal ExtractionChunk row."""
    chunk = ExtractionChunk(
        id=uuid.uuid4(),
        job_id=job_id,
        content="some content",
        source_reference=source_reference,
        chunk_index=0,
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def _seed_gap(
    session: AsyncSession,
    *,
    ontology_version_id: uuid.UUID,
    entity_type: str = "NuclearMaterial",
    property_name: str = "density",
    gap_status: str = "open",
    chunk_id: uuid.UUID | None = None,
) -> ExtractionGap:
    """Insert a minimal ExtractionGap row."""
    gap = ExtractionGap(
        id=uuid.uuid4(),
        ontology_version_id=ontology_version_id,
        entity_type=entity_type,
        property=property_name,
        gap_status=gap_status,
        chunk_id=chunk_id,
    )
    session.add(gap)
    await session.flush()
    return gap


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_ontology_version_id(async_client) -> None:
    response = await async_client.get("/api/v1/extraction-gaps")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_envelope(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={"ontology_version_id": str(ov.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert "meta" in body
    data = body["data"]
    meta = body["meta"]
    assert isinstance(data, list)
    assert meta["total"] == 0
    assert meta["limit"] == 50
    assert meta["offset"] == 0


@pytest.mark.asyncio
async def test_list_filters_by_entity_type(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    await _seed_gap(
        db_session, ontology_version_id=ov.id, entity_type="NuclearMaterial",
    )
    await _seed_gap(
        db_session, ontology_version_id=ov.id, entity_type="Isotope",
    )

    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(ov.id),
            "entity_type": "Isotope",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["entity_type"] == "Isotope"


@pytest.mark.asyncio
async def test_list_filters_by_gap_status(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        gap_status="open",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="Isotope",
        property_name="half_life",
        gap_status="filled",
    )

    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(ov.id),
            "gap_status": "filled",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["gap_status"] == "filled"


@pytest.mark.asyncio
async def test_list_filters_by_job_id(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    job = await _seed_job(db_session)
    chunk = await _seed_chunk(db_session, job_id=job.id)
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        chunk_id=chunk.id,
    )
    other = await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="Isotope",
        property_name="half_life",
    )

    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(ov.id),
            "job_id": str(job.id),
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["id"] != str(other.id)


@pytest.mark.asyncio
async def test_list_pagination_limit_and_offset(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    for i in range(7):
        await _seed_gap(
            db_session,
            ontology_version_id=ov.id,
            property_name=f"prop_{i}",
        )

    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(ov.id),
            "limit": 3,
            "offset": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 3
    assert body["meta"]["total"] == 7
    assert body["meta"]["limit"] == 3
    assert body["meta"]["offset"] == 3


@pytest.mark.asyncio
async def test_list_limit_capped_at_200(async_client) -> None:
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(uuid.uuid4()),
            "limit": 999,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_rejects_invalid_gap_status(async_client) -> None:
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(uuid.uuid4()),
            "gap_status": "bogus",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps/{gap_id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_returns_gap(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(db_session, ontology_version_id=ov.id)

    response = await async_client.get(f"/api/v1/extraction-gaps/{gap.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(gap.id)
    assert body["ontology_version_id"] == str(ov.id)
    assert body["entity_type"] == "NuclearMaterial"
    assert body["property"] == "density"
    assert body["gap_status"] == "open"
    # No chunk attached in this test.
    assert body.get("chunk_id") is None
    assert body.get("source_reference") is None


@pytest.mark.asyncio
async def test_detail_includes_chunk_source_reference(
    async_client, db_session
) -> None:
    ov = await _seed_ontology(db_session)
    job = await _seed_job(db_session)
    chunk = await _seed_chunk(
        db_session, job_id=job.id, source_reference="page-3"
    )
    gap = await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        chunk_id=chunk.id,
    )

    response = await async_client.get(f"/api/v1/extraction-gaps/{gap.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == str(chunk.id)
    assert body["source_reference"] == "page-3"


@pytest.mark.asyncio
async def test_detail_404_on_missing(async_client) -> None:
    response = await async_client.get(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/extraction-gaps/{gap_id}/status — transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_open_to_filling_succeeds(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filling"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "filling"
    assert body["resolved_at"] is None


@pytest.mark.asyncio
async def test_patch_open_to_wont_fix_succeeds(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "wont_fix"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_filling_to_filled_sets_resolved_at(
    async_client, db_session
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filling"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "filled"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_filling_to_wont_fix_sets_resolved_at(
    async_client, db_session
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filling"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "wont_fix"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_open_to_filled_rejected(async_client, db_session) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
    )
    # Spec: open → filling|wont_fix; open → filled is invalid.
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_patch_filled_is_immutable_returns_409(
    async_client, db_session
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filled"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_wont_fix_is_immutable_returns_409(
    async_client, db_session
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="wont_fix"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_same_terminal_status_returns_409(
    async_client, db_session
) -> None:
    """Transitioning filled -> filled is rejected (terminal is immutable)."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filled"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_404_on_missing_gap(async_client) -> None:
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}/status",
        json={"status": "filling"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_rejects_invalid_status_value(async_client) -> None:
    """`status: open` is not in {filling, filled, wont_fix}."""
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}/status",
        json={"status": "open"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# OpenAPI registration
# ---------------------------------------------------------------------------


def test_extraction_gap_paths_registered_in_openapi() -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/extraction-gaps" in paths
    assert "/api/v1/extraction-gaps/{gap_id}" in paths
    assert "/api/v1/extraction-gaps/{gap_id}/status" in paths

    list_op = paths["/api/v1/extraction-gaps"]["get"]
    components = schema.get("components", {}).get("schemas", {})
    assert "ExtractionGapResponse" in components