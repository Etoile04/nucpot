"""Integration tests for ``GET /api/v1/ontology/{version}/coverage`` (NFM-2697-T4).

Covers the spec-mandated ontology coverage endpoint:

- 200 with body ``{coverage_rate, literature_total,
  literature_fully_covered, gap_distribution}``
- 404 if version not found
- Authz: admin OR domain_expert 200; unauthenticated 401;
  non-eligible role (editor) 403
- OpenAPI lists the route.
- The legacy ``/extraction-gaps/recall/{id}`` endpoint emits a
  ``Deprecation`` response header pointing to the new URL.
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
    corpus_id: str | None = None,
) -> DataSource:
    lit_id = uuid.uuid4()
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
    corpus_id: str | None = None,
) -> ExtractionJob:
    job = ExtractionJob(
        id=uuid.uuid4(),
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
    ontology_version: str,
    entity_type: str = "NuclearMaterial",
    property_name: str = "density",
    gap_status: str = "open",
    chunk_id: uuid.UUID | None = None,
) -> ExtractionGap:
    gap = ExtractionGap(
        id=uuid.uuid4(),
        ontology_version=ontology_version,
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
async def test_ontology_coverage_unauthenticated_returns_401(
    async_client, db_session,
) -> None:
    ov = await _seed_ontology(db_session)
    response = await async_client.get(f"/api/v1/ontology/{ov.id}/coverage")
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_ontology_coverage_editor_returns_403(
    async_client, db_session, editor_headers,
) -> None:
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=editor_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_ontology_coverage_admin_succeeds(
    async_client, db_session, admin_headers,
) -> None:
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=admin_headers,
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_coverage_unknown_version_returns_404(
    async_client, domain_expert_headers,
) -> None:
    response = await async_client.get(
        f"/api/v1/ontology/{uuid.uuid4()}/coverage",
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_coverage_returns_envelope_shape(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert set(data.keys()) >= {
        "coverage_rate",
        "literature_total",
        "literature_fully_covered",
        "gap_distribution",
    }
    assert isinstance(data["coverage_rate"], float)
    assert isinstance(data["literature_total"], int)
    assert isinstance(data["literature_fully_covered"], int)
    assert isinstance(data["gap_distribution"], dict)


@pytest.mark.asyncio
async def test_ontology_coverage_zero_literature_is_full_coverage(
    async_client, db_session, domain_expert_headers,
) -> None:
    """With no literature, coverage_rate defaults to 1.0 (vacuous truth)."""
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=domain_expert_headers,
    )
    data = response.json()["data"]
    assert data["literature_total"] == 0
    assert data["literature_fully_covered"] == 0
    assert data["coverage_rate"] == 1.0
    assert data["gap_distribution"] == {}


@pytest.mark.asyncio
async def test_ontology_coverage_literature_fully_covered(
    async_client, db_session, domain_expert_headers,
) -> None:
    """A literature whose chunks have no open/filling gaps is fully covered."""
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    lit = await _seed_literature(db_session, corpus_id="corpus-ok")
    job = await _seed_job(db_session, corpus_id=lit.doi)
    await _seed_chunk(db_session, job_id=job.id)
    # No gaps -> fully covered.

    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=domain_expert_headers,
    )
    data = response.json()["data"]
    assert data["literature_total"] == 1
    assert data["literature_fully_covered"] == 1
    assert data["coverage_rate"] == 1.0


@pytest.mark.asyncio
async def test_ontology_coverage_literature_with_open_gap_not_covered(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    lit = await _seed_literature(db_session, corpus_id="corpus-bad")
    job = await _seed_job(db_session, corpus_id=lit.doi)
    chunk = await _seed_chunk(db_session, job_id=job.id)
    await _seed_gap(
        db_session,
        ontology_version=ov.version,
        entity_type="NuclearMaterial",
        property_name="density",
        gap_status="open",
        chunk_id=chunk.id,
    )

    response = await async_client.get(
        f"/api/v1/ontology/{ov.id}/coverage",
        headers=domain_expert_headers,
    )
    data = response.json()["data"]
    assert data["literature_total"] == 1
    assert data["literature_fully_covered"] == 0
    assert data["coverage_rate"] == 0.0
    # gap_distribution counts (entity_type+property) -> number of open gaps
    assert data["gap_distribution"]["NuclearMaterial.density"] == 1


@pytest.mark.asyncio
async def test_ontology_coverage_registered_in_openapi(
    domain_expert_headers,
) -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})
    matches = [p for p in paths if p.endswith("/coverage") and "/ontology/" in p]
    assert matches, f"ontology coverage path not in OpenAPI: {list(paths)}"


# ---------------------------------------------------------------------------
# Legacy endpoint deprecation header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_extraction_gaps_recall_has_deprecation_header(
    async_client, db_session, domain_expert_headers,
) -> None:
    """The old ``/extraction-gaps/recall/{id}`` must emit a Deprecation header."""
    ov = await _seed_ontology(db_session, ontology_data=_ONTOLOGY_TWO_PAIRS)
    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{ov.id}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    header_names = {k.lower() for k in response.headers}
    assert "deprecation" in header_names
    deprecation_value = response.headers.get("deprecation", "")
    assert "ontology" in deprecation_value.lower()
