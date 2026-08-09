"""Integration tests for ``GET /api/v1/literature/{id}/recall`` (NFM-2697-T4).

Covers the spec-mandated per-literature recall endpoint:

- 200 with body ``{recall_rate, extracted_slots, expected_slots, gaps}``
- 404 if literature_id not found
- 422 if ontology_version missing
- Authz: domain_expert 200; unauthenticated 401; non-domain-expert 403
- OpenAPI lists the route.
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
from nfm_db.models.source import DataSource
from nfm_db.models.user import BlogRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(
    session: AsyncSession,
    *,
    role: BlogRole = BlogRole.DOMAIN_EXPERT,
) -> User:
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
    ontology_data: dict | None = None,
) -> OntologyVersion:
    user = await _seed_user(session)
    ov = OntologyVersion(
        id=uuid.uuid4(),
        version=f"1.0.0-{uuid.uuid4().hex[:8]}",
        status="published",
        created_by=user.id,
        ontology_data=ontology_data,
    )
    session.add(ov)
    await session.flush()
    return ov


async def _seed_literature(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None = None,
    corpus_id: str | None = None,
) -> DataSource:
    # The literature_id used in the URL is the DataSource UUID.  The
    # corpus_id is a string used to link DataSource rows to ExtractionJob
    # rows; we set it as the DataSource's doi for tests so jobs that
    # carry the same doi can be associated with this literature.
    lit_id = source_id or uuid.uuid4()
    lit = DataSource(
        id=lit_id,
        title=f"lit-{uuid.uuid4().hex[:8]}",
        source_type="literature",
        doi=corpus_id or str(lit_id),
    )
    session.add(lit)
    await session.flush()
    return lit


async def _seed_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID | None = None,
    corpus_id: str | None = None,
) -> ExtractionJob:
    job = ExtractionJob(
        id=job_id or uuid.uuid4(),
        status="completed",
        corpus_id=corpus_id,
    )
    session.add(job)
    await session.flush()
    return job


async def _seed_chunk(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> ExtractionChunk:
    chunk = ExtractionChunk(
        id=uuid.uuid4(),
        job_id=job_id,
        content="some content",
        source_reference="page-3",
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


_ONTOLOGY_TWO_PAIRS = {
    "entity_types": [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "melting_point"],
        },
    ],
    "relation_types": [],
}


# ---------------------------------------------------------------------------
# AuthZ
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_literature_recall_unauthenticated_returns_401(
    async_client, db_session,
) -> None:
    lit = await _seed_literature(db_session)
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        params={"ontology_version": str(ov.id)},
    )
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_literature_recall_non_expert_returns_403(
    async_client, db_session, editor_headers,
) -> None:
    lit = await _seed_literature(db_session)
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        params={"ontology_version": str(ov.id)},
        headers=editor_headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_literature_recall_missing_ontology_version_returns_422(
    async_client, db_session, domain_expert_headers,
) -> None:
    lit = await _seed_literature(db_session)
    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        headers=domain_expert_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_literature_recall_unknown_literature_returns_404(
    async_client, domain_expert_headers,
) -> None:
    response = await async_client.get(
        f"/api/v1/literature/{uuid.uuid4()}/recall",
        params={"ontology_version": str(uuid.uuid4())},
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_literature_recall_unknown_ontology_version_returns_404(
    async_client, db_session, domain_expert_headers,
) -> None:
    lit = await _seed_literature(db_session)
    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        params={"ontology_version": str(uuid.uuid4())},
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_literature_recall_returns_envelope_shape(
    async_client, db_session, domain_expert_headers,
) -> None:
    lit = await _seed_literature(db_session)
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        params={"ontology_version": str(ov.id)},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data.keys()) >= {
        "recall_rate",
        "extracted_slots",
        "expected_slots",
        "gaps",
    }
    assert isinstance(data["recall_rate"], float)
    assert isinstance(data["extracted_slots"], int)
    assert isinstance(data["expected_slots"], int)
    assert isinstance(data["gaps"], list)
    # 2 expected (entity_type, property) pairs and 0 open gaps -> full recall.
    assert data["expected_slots"] == 2
    assert data["extracted_slots"] == 2
    assert data["recall_rate"] == 1.0
    assert data["gaps"] == []


@pytest.mark.asyncio
async def test_literature_recall_includes_gaps_for_literature_chunks(
    async_client, db_session, domain_expert_headers,
) -> None:
    lit = await _seed_literature(db_session, corpus_id="corpus-xyz")
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)

    # Job whose corpus_id matches the literature's doi; chunks produced
    # for that job are the literature's chunks.
    job = await _seed_job(db_session, corpus_id=lit.doi)
    chunk = await _seed_chunk(db_session, job_id=job.id)
    # 1 open gap linked to the literature's chunk.
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="NuclearMaterial",
        property_name="density",
        gap_status="open",
        chunk_id=chunk.id,
    )
    # 1 filled gap (does not count as "uncovered").
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        entity_type="NuclearMaterial",
        property_name="melting_point",
        gap_status="filled",
        chunk_id=chunk.id,
    )

    response = await async_client.get(
        f"/api/v1/literature/{lit.id}/recall",
        params={"ontology_version": str(ov.id)},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    # 2 expected, 1 still open -> extracted=1, recall=0.5
    assert data["expected_slots"] == 2
    assert data["extracted_slots"] == 1
    assert data["recall_rate"] == 0.5
    # Gap list contains the single open gap.
    assert len(data["gaps"]) == 1
    assert data["gaps"][0]["entity_type"] == "NuclearMaterial"
    assert data["gaps"][0]["property"] == "density"


@pytest.mark.asyncio
async def test_literature_recall_registered_in_openapi(
    domain_expert_headers,
) -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})
    # The new endpoint must be present.
    assert "/api/v1/literature/{literature_id}/recall" in paths
