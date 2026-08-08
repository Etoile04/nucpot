"""Integration tests for ``/api/v1/extraction-gaps`` endpoints (NFM-2599).

Covers:
  - GET   /api/v1/extraction-gaps              — paginated + filtered list
  - GET   /api/v1/extraction-gaps/{gap_id}     — detail with chunk source_reference
  - PATCH /api/v1/extraction-gaps/{gap_id}/status — status transitions + 409 + 404
  - GET   /api/v1/extraction-gaps/recall/{id}  — recall metrics

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
    ontology_data: dict | None = None,
) -> OntologyVersion:
    """Insert a minimal OntologyVersion row."""
    user = await _seed_user(session)
    ov = OntologyVersion(
        id=version_id or uuid.uuid4(),
        version=f"1.0.0-{uuid.uuid4().hex[:8]}",
        status="published",
        created_by=user.id,
        ontology_data=ontology_data,
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
# Auth guards (NFM-2629)
# ---------------------------------------------------------------------------

_RECALL_ONTOLOGY_DATA = {
    "entity_types": [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "melting_point"],
        },
        {
            "name": "Isotope",
            "properties": [
                {"name": "half_life", "datatype": "float"},
            ],
        },
    ],
    "relation_types": [],
}


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_list_unauthenticated_returns_401(async_client, db_session) -> None:
    """Unauthenticated GET /extraction-gaps returns 401."""
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={"ontology_version_id": str(ov.id)},
    )
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_detail_unauthenticated_returns_401(async_client, db_session) -> None:
    """Unauthenticated GET /extraction-gaps/{id} returns 401."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(db_session, ontology_version_id=ov.id)
    response = await async_client.get(f"/api/v1/extraction-gaps/{gap.id}")
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_recall_unauthenticated_returns_401(async_client, db_session) -> None:
    """Unauthenticated GET /extraction-gaps/recall/{id} returns 401."""
    ov = await _seed_ontology(db_session, ontology_data=_RECALL_ONTOLOGY_DATA)
    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{ov.id}",
    )
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_patch_status_unauthenticated_returns_401(async_client, db_session) -> None:
    """Unauthenticated PATCH /extraction-gaps/{id}/status returns 401."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open",
    )
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filling"},
    )
    assert response.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_patch_status_non_expert_returns_403(
    async_client, db_session, editor_headers,
) -> None:
    """PATCH with editor (non-expert) role returns 403."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open",
    )
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filling"},
        headers=editor_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_authenticated_returns_200(
    async_client, db_session, domain_expert_headers,
) -> None:
    """Authenticated GET /extraction-gaps returns 200."""
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={"ontology_version_id": str(ov.id)},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_patch_status_domain_expert_succeeds(
    async_client, db_session, domain_expert_headers,
) -> None:
    """PATCH with domain_expert role succeeds."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open",
    )
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filling"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    assert response.json()["gap_status"] == "filling"


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requires_ontology_version_id(async_client, domain_expert_headers) -> None:
    response = await async_client.get(
        "/api/v1/extraction-gaps", headers=domain_expert_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_returns_envelope(async_client, db_session, domain_expert_headers) -> None:
    ov = await _seed_ontology(db_session)
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={"ontology_version_id": str(ov.id)},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    paginated = body["data"]
    assert isinstance(paginated["items"], list)
    assert paginated["total"] == 0
    assert paginated["limit"] == 50
    assert paginated["page"] == 1
    assert paginated["pages"] == 0


@pytest.mark.asyncio
async def test_list_filters_by_entity_type(async_client, db_session, domain_expert_headers) -> None:
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
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]["items"]
    assert len(data) == 1
    assert data[0]["entity_type"] == "Isotope"


@pytest.mark.asyncio
async def test_list_filters_by_gap_status(async_client, db_session, domain_expert_headers) -> None:
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
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]["items"]
    assert len(data) == 1
    assert data[0]["gap_status"] == "filled"


@pytest.mark.asyncio
async def test_list_filters_by_job_id(async_client, db_session, domain_expert_headers) -> None:
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
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]["items"]
    assert len(data) == 1
    assert data[0]["id"] != str(other.id)


@pytest.mark.asyncio
async def test_list_pagination_limit_and_offset(async_client, db_session, domain_expert_headers) -> None:
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
            "page": 2,
        },
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]["items"]) == 3
    assert body["data"]["total"] == 7
    assert body["data"]["limit"] == 3
    assert body["data"]["page"] == 2
    assert body["data"]["pages"] == 3


@pytest.mark.asyncio
async def test_list_limit_capped_at_200(async_client, domain_expert_headers) -> None:
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(uuid.uuid4()),
            "limit": 999,
        },
        headers=domain_expert_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_rejects_invalid_gap_status(async_client, domain_expert_headers) -> None:
    response = await async_client.get(
        "/api/v1/extraction-gaps",
        params={
            "ontology_version_id": str(uuid.uuid4()),
            "gap_status": "bogus",
        },
        headers=domain_expert_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps/{gap_id} — detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_returns_gap(async_client, db_session, domain_expert_headers) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(db_session, ontology_version_id=ov.id)

    response = await async_client.get(
        f"/api/v1/extraction-gaps/{gap.id}", headers=domain_expert_headers,
    )
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
    async_client, db_session, domain_expert_headers,
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

    response = await async_client.get(
        f"/api/v1/extraction-gaps/{gap.id}", headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == str(chunk.id)
    assert body["source_reference"] == "page-3"


@pytest.mark.asyncio
async def test_detail_404_on_missing(async_client, domain_expert_headers) -> None:
    response = await async_client.get(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/extraction-gaps/{gap_id}/status — transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_open_to_filling_succeeds(async_client, db_session, domain_expert_headers) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filling"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "filling"
    assert body["resolved_at"] is None


@pytest.mark.asyncio
async def test_patch_open_to_wont_fix_succeeds(async_client, db_session, domain_expert_headers) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "wont_fix"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_filling_to_filled_sets_resolved_at(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filling"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "filled"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_filling_to_wont_fix_sets_resolved_at(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filling"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["gap_status"] == "wont_fix"
    assert body["resolved_at"] is not None


@pytest.mark.asyncio
async def test_patch_open_to_filled_rejected(async_client, db_session, domain_expert_headers) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="open"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
        headers=domain_expert_headers,
    )
    # Spec: open → filling|wont_fix; open → filled is invalid.
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_patch_filled_is_immutable_returns_409(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filled"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "wont_fix"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_wont_fix_is_immutable_returns_409(
    async_client, db_session, domain_expert_headers,
) -> None:
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="wont_fix"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_same_terminal_status_returns_409(
    async_client, db_session, domain_expert_headers,
) -> None:
    """Transitioning filled -> filled is rejected (terminal is immutable)."""
    ov = await _seed_ontology(db_session)
    gap = await _seed_gap(
        db_session, ontology_version_id=ov.id, gap_status="filled"
    )

    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{gap.id}/status",
        json={"status": "filled"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_patch_404_on_missing_gap(async_client, domain_expert_headers) -> None:
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}/status",
        json={"status": "filling"},
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_rejects_invalid_status_value(async_client, domain_expert_headers) -> None:
    """`status: open` is not in {filling, filled, wont_fix}."""
    response = await async_client.patch(
        f"/api/v1/extraction-gaps/{uuid.uuid4()}/status",
        json={"status": "open"},
        headers=domain_expert_headers,
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
    assert "/api/v1/extraction-gaps/recall/{ontology_version_id}" in paths

    _list_op = paths["/api/v1/extraction-gaps"]["get"]
    components = schema.get("components", {}).get("schemas", {})
    assert "ExtractionGapResponse" in components
    assert "RecallMetricsResponse" in components


# ---------------------------------------------------------------------------
# GET /api/v1/extraction-gaps/recall/{ontology_version_id} — recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_returns_correct_shape(async_client, db_session, domain_expert_headers) -> None:
    """GET /recall/{ov_id} returns all RecallMetricsResponse fields."""
    ov = await _seed_ontology(
        db_session, ontology_data=_RECALL_ONTOLOGY_DATA,
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        gap_status="open",
    )

    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{ov.id}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["ontology_version_id"] == str(ov.id)
    assert data["total_expected"] == 3
    assert data["total_gaps"] == 1
    assert data["open_gaps"] == 1
    assert data["filled_gaps"] == 0
    assert data["wont_fix_gaps"] == 0
    assert "recall_rate" in data
    assert isinstance(data["recall_rate"], float)
    assert "computed_at" in data
    assert isinstance(data["computed_at"], str)


@pytest.mark.asyncio
async def test_recall_404_for_unknown_ontology(async_client, domain_expert_headers) -> None:
    """GET /recall/{unknown_id} returns 404."""
    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{uuid.uuid4()}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_recall_no_gaps_returns_full_recall(
    async_client, db_session, domain_expert_headers,
) -> None:
    """No gaps → recall_rate = 1.0."""
    ov = await _seed_ontology(
        db_session, ontology_data=_RECALL_ONTOLOGY_DATA,
    )

    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{ov.id}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["recall_rate"] == 1.0
    assert body["data"]["total_expected"] == 3
    assert body["data"]["total_gaps"] == 0


@pytest.mark.asyncio
async def test_recall_mixed_gaps_correct_rate(
    async_client, db_session, domain_expert_headers,
) -> None:
    """1 open + 1 filled out of 3 expected → recall = (3-1)/3 ≈ 0.667."""
    ov = await _seed_ontology(
        db_session, ontology_data=_RECALL_ONTOLOGY_DATA,
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        gap_status="open",
    )
    await _seed_gap(
        db_session,
        ontology_version_id=ov.id,
        property_name="melting_point",
        gap_status="filled",
    )

    response = await async_client.get(
        f"/api/v1/extraction-gaps/recall/{ov.id}",
        headers=domain_expert_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total_expected"] == 3
    assert data["total_gaps"] == 2
    assert data["open_gaps"] == 1
    assert data["filled_gaps"] == 1
    assert data["recall_rate"] == pytest.approx(2 / 3)
