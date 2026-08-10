"""ADR Section 5 unit + integration tests (NFM-2697-T5).

Covers every item in the ADR Section 5 test inventory that is NOT already
covered by the T3 / T4 test suites:

  * test_gap_scanner_nfm2697_t3.py   -- service-layer unit tests
  * test_compute_recall.py            -- module-level compute_recall

Gaps addressed here:

scan_literature
  - filling-state dedup (re-scan does NOT duplicate a filling gap)
  - filled-state dedup (re-scan does NOT reopen a filled gap)
  - idempotency (same scan twice -> identical row set)
  - dry-run (persist=False returns gaps without writing)

compute_recall
  - empty literature with no chunks -> recall_rate = 1.0
  - fully extracted (all properties mentioned) -> recall_rate = 1.0
  - partial extraction (some properties missing) -> recall_rate < 1.0

compute_coverage
  - empty corpus distribution contract
  - fully covered (all literature have zero gaps) -> coverage_rate = 1.0
  - partially covered -> coverage_rate < 1.0

API endpoint contract
  - GET /api/v1/literature/{id}/recall -- 200 / 404 / 422
  - GET /api/v1/ontology/{version}/coverage -- 200 / 404

Integration (end-to-end)
  - scan_literature -> compute_recall -> compute_coverage pipeline
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from nfm_db.api.v1.auth import get_current_user
from nfm_db.database import get_db
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
from nfm_db.services.gap_scanner import GapScanService

# ---------------------------------------------------------------------------
# Seed helpers (mirror existing test patterns)
# ---------------------------------------------------------------------------


_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _ontology_data(entity_types: list[dict]) -> dict:
    """Wrap entity_types in the minimal ontology payload."""
    return {"entity_types": entity_types, "relation_types": []}


async def _seed_user(session) -> User:
    """Return the pre-seeded user (conftest db_session creates it)."""
    user = await session.get(User, _SEED_USER_ID)
    if user is None:
        user = User(
            id=_SEED_USER_ID,
            username="t5_test_user",
            email="t5@test.com",
            hashed_password="hashed",
            blog_role=BlogRole.DOMAIN_EXPERT,
            is_active=True,
        )
        session.add(user)
        await session.flush()
    return user


async def _seed_ontology_version(
    session,
    *,
    version: str = "1.0.0",
    entity_types: list[dict] | None = None,
) -> OntologyVersion:
    """Create a fresh OntologyVersion with the given semver."""
    await _seed_user(session)
    ov = OntologyVersion(
        version=version,
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=_ontology_data(entity_types or []),
    )
    session.add(ov)
    await session.flush()
    await session.refresh(ov)
    return ov


async def _seed_job(
    session,
    *,
    corpus_id: str | None = None,
    source_reference: str | None = None,
    ontology_version_id: uuid.UUID | None = None,
) -> ExtractionJob:
    """Create an ExtractionJob tagged with a literature proxy."""
    job = ExtractionJob(
        corpus_id=corpus_id,
        source_reference=source_reference,
        source_type="doi",
        status="completed",
        ontology_version_id=ontology_version_id,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


async def _seed_gap(
    session,
    *,
    ontology_version: str,
    entity_type: str = "Material",
    property_name: str = "density",
    gap_status: str = "open",
    chunk_id: uuid.UUID | None = None,
) -> ExtractionGap:
    """Insert a minimal ExtractionGap row."""
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


async def _seed_data_source(
    session,
    *,
    ds_id: uuid.UUID,
    title: str = "Test Literature",
    doi: str | None = None,
) -> DataSource:
    """Insert a minimal DataSource row (required by module-level recall/coverage)."""
    ds = DataSource(
        id=ds_id,
        title=title,
        doi=doi,
        source_type="literature",
    )
    session.add(ds)
    await session.flush()
    return ds


# ---------------------------------------------------------------------------
# ASGI test client helper (bypasses JWT auth + injects test DB session)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _api_client(
    db_session,
) -> AsyncGenerator[AsyncClient, None]:
    """Yield an AsyncClient with dependency overrides for testing.

    Injects the test db_session and bypasses JWT auth by returning a
    pre-seeded domain-expert user.
    """
    user = await _seed_user(db_session)

    async def _override_get_db():
        yield db_session

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# scan_literature -- filling-state dedup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_literature_filling_gap_not_duplicated(db_session) -> None:
    """Re-scanning a gap already in 'filling' does NOT create a duplicate.

    ADR Section 5: filling-state test.  The existing T3 test only
    checks 'filled' status dedup; this covers 'filling'.
    """
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions nothing -> density is missing.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="no property values here",
            chunk_index=0,
            token_count=4,
        ),
    )
    # Pre-existing gap in 'filling' status.
    await _seed_gap(
        db_session,
        ontology_version=ov.version,
        gap_status="filling",
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    # Should return the pre-existing gap, not create a new one.
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1, "filling gap must not be duplicated"
    assert rows[0].gap_status == "filling"
    assert len(gaps) == 1
    assert gaps[0].id == rows[0].id


# ---------------------------------------------------------------------------
# scan_literature -- filled-state dedup (no reopen)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_literature_filled_gap_not_reopened(db_session) -> None:
    """Re-scanning a 'filled' gap does NOT reopen it.

    ADR Section 5: filled-state test.  The existing T3 test checks
    this for ``only_open=True`` (default); this test explicitly
    asserts the gap stays filled and no new open gap is created.
    """
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="no values",
            chunk_index=0,
            token_count=2,
        ),
    )
    await _seed_gap(
        db_session,
        ontology_version=ov.version,
        gap_status="filled",
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1, "filled gap must not be reopened"
    assert rows[0].gap_status == "filled"
    assert len(gaps) == 1
    assert gaps[0].id == rows[0].id


# ---------------------------------------------------------------------------
# scan_literature -- idempotency (identical row set on repeat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_literature_idempotent_identical_row_set(db_session) -> None:
    """Same scan twice with only_open=True returns identical row set.

    ADR Section 5: idempotency test.  Verifies that the set of gap
    IDs returned by two consecutive scans are identical (no new rows
    created on the second scan).
    """
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions density but not melting_point.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density is 8.9 g/cm3",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    # First scan.
    gaps_first = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
        only_open=True,
    )
    first_ids = {g.id for g in gaps_first}

    # Second scan (identical).
    gaps_second = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
        only_open=True,
    )
    second_ids = {g.id for g in gaps_second}

    assert first_ids == second_ids, (
        "Two consecutive scans with only_open=True must return "
        "the same set of gap IDs"
    )
    # Total rows in DB should be exactly what the scan returns.
    all_rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(all_rows) == len(first_ids)


# ---------------------------------------------------------------------------
# scan_literature -- dry-run (persist=False returns gaps without writing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_literature_dry_run_no_persist(db_session) -> None:
    """scan_literature with persist=False returns gaps but writes nothing.

    ADR Section 5: dry-run test.  Verifies the scan computes the correct
    gap set in-memory but does NOT insert any rows into the database.
    """
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions density but not melting_point -> one gap.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density is 8.9 g/cm3",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
        persist=False,
    )

    # Should find the gap in-memory.
    assert len(gaps) == 1
    assert gaps[0].entity_type == "Material"
    assert gaps[0].property == "melting_point"

    # But nothing should be persisted to DB.
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 0, "dry-run must not persist gaps"


# ---------------------------------------------------------------------------
# compute_recall -- empty literature (no chunks) -> 1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_recall_empty_literature_no_chunks(db_session) -> None:
    """Literature with no extraction chunks -> recall_rate = 1.0.

    ADR Section 5: empty literature test.  When a literature has
    no associated ExtractionJob / ExtractionChunk rows, there are
    zero gaps, so recall is 1.0 (fully covered by default).
    """
    await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    # No job or chunk created for this literature.

    svc = GapScanService(db_session)
    metrics = await svc.compute_recall(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    assert isinstance(metrics.recall_rate, float)
    assert metrics.recall_rate == 1.0
    assert metrics.total_expected == 1
    assert metrics.open_gaps == 0


# ---------------------------------------------------------------------------
# compute_recall -- fully extracted (all properties mentioned) -> 1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_recall_fully_extracted(db_session) -> None:
    """All expected properties present in chunks -> recall_rate = 1.0.

    ADR Section 5: fully-extracted test.  When every expected property
    for the ontology appears in at least one chunk, recall is perfect.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions BOTH properties -> no gaps.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density=8.5 g/cm3, melting_point=1440K",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    metrics = await svc.compute_recall(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    assert metrics.recall_rate == 1.0
    assert metrics.total_expected == 2
    assert metrics.open_gaps == 0


# ---------------------------------------------------------------------------
# compute_recall -- partial extraction -> recall_rate < 1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_recall_partial_extraction(db_session) -> None:
    """Some properties missing from chunks -> recall_rate < 1.0.

    ADR Section 5: partial-extraction test.  Verifies that recall
    correctly reports <1.0 when only a subset of expected properties
    are present in the chunk content.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions only density -> melting_point is a gap.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="The material density is 8.5 g/cm3.",
            chunk_index=0,
            token_count=8,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    # Scan first to create the gap, then compute recall.
    await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    metrics = await svc.compute_recall(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    assert metrics.recall_rate < 1.0
    assert metrics.total_expected == 2
    assert metrics.open_gaps >= 1


# ---------------------------------------------------------------------------
# compute_coverage -- empty corpus distribution contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_coverage_empty_corpus_distribution_empty(
    db_session,
) -> None:
    """Empty ontology version: gap_distribution must be empty dict."""
    await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    svc = GapScanService(db_session)
    metrics = await svc.compute_coverage(ontology_version="1.0.0")

    assert metrics.gap_distribution == {}
    assert metrics.coverage_rate == 0.0
    assert metrics.literature_total == 0


# ---------------------------------------------------------------------------
# compute_coverage -- fully covered -> coverage_rate = 1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_coverage_fully_covered(db_session) -> None:
    """All literature fully covered -> coverage_rate = 1.0.

    ADR Section 5: fully-covered test.  When every literature in the
    ontology has zero open/filling gaps, coverage_rate is 1.0 and
    gap_distribution is empty.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions density -> no gaps.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density is 10.0 g/cm3",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    metrics = await svc.compute_coverage(ontology_version="1.0.0")

    assert metrics.coverage_rate == 1.0
    assert metrics.literature_total == 1
    assert metrics.literature_fully_covered == 1
    assert metrics.gap_distribution == {}


# ---------------------------------------------------------------------------
# compute_coverage -- partially covered -> coverage_rate < 1.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_coverage_partially_covered(db_session) -> None:
    """Some literature have gaps -> coverage_rate < 1.0.

    ADR Section 5: partially-covered test.  Verifies that coverage
    correctly reports <1.0 and gap_distribution reflects the missing
    properties.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions only density -> melting_point is a gap.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="The density is 8.5 g/cm3.",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    # Scan to create the gap, then compute coverage.
    await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    metrics = await svc.compute_coverage(ontology_version="1.0.0")

    assert metrics.coverage_rate < 1.0
    assert metrics.literature_total == 1
    assert metrics.literature_fully_covered == 0
    assert ("Material", "melting_point") in metrics.gap_distribution


# ---------------------------------------------------------------------------
# API endpoint contract -- GET /api/v1/literature/{id}/recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_literature_recall_200(db_session) -> None:
    """GET /api/v1/literature/{id}/recall returns 200 + payload."""
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    # Module-level compute_literature_recall queries DataSource, not just Job.
    # The join is via DataSource.doi == ExtractionJob.corpus_id.
    await _seed_data_source(
        db_session, ds_id=literature_id, doi=str(literature_id),
    )
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density is 10.0",
            chunk_index=0,
            token_count=4,
        ),
    )
    await db_session.flush()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/literature/{literature_id}/recall",
            params={"ontology_version": str(ov.id)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["recall_rate"] == 1.0
    assert data["expected_slots"] == 1
    assert data["extracted_slots"] == 1
    assert data["gaps"] == []


@pytest.mark.asyncio
async def test_api_literature_recall_404_missing_literature(
    db_session,
) -> None:
    """GET /api/v1/literature/{id}/recall returns 404 for missing literature."""
    ov = await _seed_ontology_version(db_session)
    fake_id = uuid.uuid4()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/literature/{fake_id}/recall",
            params={"ontology_version": str(ov.id)},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_literature_recall_404_missing_ontology(
    db_session,
) -> None:
    """GET /api/v1/literature/{id}/recall returns 404 for missing OV."""
    literature_id = uuid.uuid4()
    await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
    )
    await db_session.flush()
    fake_ov_id = uuid.uuid4()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/literature/{literature_id}/recall",
            params={"ontology_version": str(fake_ov_id)},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_literature_recall_422_missing_param(db_session) -> None:
    """GET /api/v1/literature/{id}/recall without ontology_version -> 422."""
    literature_id = uuid.uuid4()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/literature/{literature_id}/recall",
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# API endpoint contract -- GET /api/v1/ontology/{version}/coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_ontology_coverage_200(db_session) -> None:
    """GET /api/v1/ontology/{version}/coverage returns 200 + payload."""
    ov = await _seed_ontology_version(
        db_session,
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    # Module-level compute_ontology_coverage queries DataSource.
    # The join is via DataSource.doi == ExtractionJob.corpus_id.
    await _seed_data_source(
        db_session, ds_id=literature_id, doi=str(literature_id),
    )
    await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    await db_session.flush()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/ontology/{ov.id}/coverage",
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["coverage_rate"] == 1.0
    assert data["literature_total"] == 1
    assert data["literature_fully_covered"] == 1
    assert data["gap_distribution"] == {}


@pytest.mark.asyncio
async def test_api_ontology_coverage_404_missing(db_session) -> None:
    """GET /api/v1/ontology/{version}/coverage returns 404 for missing OV."""
    fake_id = uuid.uuid4()

    async with _api_client(db_session) as client:
        resp = await client.get(
            f"/api/v1/ontology/{fake_id}/coverage",
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration: scan_literature -> compute_recall -> compute_coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_scan_recall_coverage_pipeline(db_session) -> None:
    """End-to-end: scan produces gaps -> recall reflects -> coverage drops.

    ADR Section 5 integration test.  Seeds a literature with two
    expected properties, one present in the chunk and one missing.
    After scan_literature, compute_recall should show <1.0 and
    compute_coverage should reflect the gap.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions density but not melting_point -> one gap expected.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="The material density is 8.5 g/cm3.",
            chunk_index=0,
            token_count=8,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    # Step 1: scan_literature
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    assert len(gaps) == 1
    assert gaps[0].entity_type == "Material"
    assert gaps[0].property == "melting_point"
    assert gaps[0].gap_status == "open"

    # Step 2: compute_recall -- one gap open -> recall < 1.0
    recall = await svc.compute_recall(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    assert recall.total_expected == 2
    assert recall.open_gaps >= 1
    assert recall.recall_rate < 1.0

    # Step 3: compute_coverage -- one literature with a gap -> not fully covered
    coverage = await svc.compute_coverage(ontology_version="1.0.0")
    assert coverage.literature_total == 1
    assert coverage.literature_fully_covered == 0
    assert coverage.coverage_rate == 0.0
    assert ("Material", "melting_point") in coverage.gap_distribution


@pytest.mark.asyncio
async def test_e2e_scan_full_coverage_pipeline(db_session) -> None:
    """End-to-end: all properties present -> no gaps -> full recall+coverage.

    Control test: when all expected properties are mentioned in the
    chunk content, scan produces zero gaps, and both recall and
    coverage report 1.0.
    """
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src",
        ontology_version_id=ov.id,
    )
    # Chunk mentions BOTH properties -> no gaps.
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="density=8.5, melting_point=1440K",
            chunk_index=0,
            token_count=6,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    # Step 1: scan -- no gaps
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    assert gaps == []

    # Step 2: recall = 1.0
    recall = await svc.compute_recall(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    assert recall.recall_rate == 1.0

    # Step 3: coverage = 1.0
    coverage = await svc.compute_coverage(ontology_version="1.0.0")
    assert coverage.coverage_rate == 1.0
    assert coverage.gap_distribution == {}
