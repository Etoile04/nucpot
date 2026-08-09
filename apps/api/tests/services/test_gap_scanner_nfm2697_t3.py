"""Tests for NFM-2697-T3 additions to GapScanService (ADR Section 2).

Covers the three new methods mandated by the parent ADR:

* ``GapScanService.scan_literature(literature_id, ontology_version,
  *, only_open=True, persist=True)``
* ``GapScanService.compute_recall(literature_id, ontology_version)``
* ``GapScanService.compute_coverage(ontology_version)``

And the supporting ``CoverageMetrics`` dataclass.

**Schema bridge (T1/T2 not yet merged)**
T1 (migration 050) adds the ``literature_id`` column and the
``ontology_version`` TEXT column to ``extraction_gaps``. T2 aligns the
SQLAlchemy model.  Until those land, this implementation bridges to the
current NFM-2575 schema:

* ``ontology_version: str`` is resolved via ``OntologyVersion.version``.
* ``literature_id`` is treated as a soft identifier — the service matches
  ``ExtractionJob.corpus_id`` (or ``source_reference``) against the
  string form of the literature id.  This keeps the public API
  signature stable per ADR Section 2 while the FK is added by T1.

The integration task NFM-2736 will merge T1+T2+T3 and switch the
implementation to use the new columns directly.
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest
from sqlalchemy import select

from nfm_db.models import (
    ExtractionChunk,
    ExtractionGap,
    ExtractionJob,
    ExtractionStep,
    OntologyVersion,
)
from nfm_db.services.gap_scanner import (
    CoverageMetrics,
    GapScanService,
    RecallMetrics,
)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _ontology_data(entity_types: list[dict]) -> dict:
    """Wrap entity_types in the minimal ontology payload."""
    return {"entity_types": entity_types, "relation_types": []}


async def _seed_ontology_version(
    session,
    *,
    version: str,
    entity_types: list[dict],
    status: str = "published",
) -> OntologyVersion:
    """Create a fresh OntologyVersion with the given semver."""
    ov = OntologyVersion(
        version=version,
        status=status,
        created_by=_SEED_USER_ID,
        ontology_data=_ontology_data(entity_types),
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
    source_type: str = "doi",
    status: str = "completed",
    ontology_version_id: uuid.UUID | None = None,
) -> ExtractionJob:
    """Create an ExtractionJob tagged with a literature proxy.

    * ``ontology_version_id`` -- ties the job to a specific
      OntologyVersion.  Required for the NFM-2697-T3 regression test
      that asserts ``compute_coverage`` is per-OV scoped.
    """
    job = ExtractionJob(
        corpus_id=corpus_id,
        source_reference=source_reference,
        source_type=source_type,
        status=status,
        ontology_version_id=ontology_version_id,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


# ---------------------------------------------------------------------------
# CoverageMetrics dataclass contract
# ---------------------------------------------------------------------------


def test_coverage_metrics_is_frozen_dataclass() -> None:
    """CoverageMetrics is a frozen dataclass with the documented fields."""
    expected_fields = {
        "ontology_version",
        "literature_total",
        "literature_fully_covered",
        "gap_distribution",
        "coverage_rate",
        "computed_at",
    }
    assert is_dataclass(CoverageMetrics)
    actual = {f.name for f in fields(CoverageMetrics)}
    assert actual == expected_fields

    metrics = CoverageMetrics(
        ontology_version="1.0.0",
        literature_total=4,
        literature_fully_covered=3,
        gap_distribution={("Material", "density"): 1},
        coverage_rate=0.75,
    )
    assert metrics.coverage_rate == 0.75
    with pytest.raises(FrozenInstanceError):
        metrics.literature_total = 99  # type: ignore[misc]


def test_recall_metrics_shape() -> None:
    """RecallMetrics exposes the recall_rate + counts surface."""
    fields_ = {f.name for f in fields(RecallMetrics)}
    assert "recall_rate" in fields_
    assert "total_expected" in fields_
    assert "open_gaps" in fields_
    assert "computed_at" in fields_


# ---------------------------------------------------------------------------
# scan_literature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_literature_creates_gaps_for_missing_properties(
    db_session,
) -> None:
    """AC1: scan_literature records ExtractionGap rows for missing data."""
    entity_types = [
        {"name": "Material", "properties": ["density", "melting_point"]},
    ]
    ov = await _seed_ontology_version(
        db_session, version="1.0.0", entity_types=entity_types,
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="10.1000/example",
        ontology_version_id=ov.id,
    )
    db_session.add(
        ExtractionChunk(
            job_id=job.id,
            content="The density is 10.5 g/cm3.",
            chunk_index=0,
            token_count=8,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    gaps = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )

    assert len(gaps) == 1
    gap = gaps[0]
    assert isinstance(gap, ExtractionGap)
    assert gap.entity_type == "Material"
    assert gap.property == "melting_point"
    assert gap.gap_status == "open"
    assert gap.ontology_version_id == ov.id


@pytest.mark.asyncio
async def test_scan_literature_is_idempotent_with_existing_gap(
    db_session,
) -> None:
    """AC1 idempotency: pre-existing filled gap is not duplicated."""
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session, corpus_id=str(literature_id), source_reference="src",
    )
    db_session.add(
        ExtractionChunk(
            job_id=job.id, content="no value here", chunk_index=0, token_count=3,
        ),
    )
    db_session.add(
        ExtractionGap(
            ontology_version_id=ov.id,
            entity_type="Material",
            property="density",
            gap_status="filled",
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    gaps = await svc.scan_literature(
        literature_id=literature_id, ontology_version="1.0.0",
    )

    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1
    assert rows[0].gap_status == "filled"
    # Returns the pre-existing gap (idempotent view).
    assert {g.id for g in gaps} == {rows[0].id}


@pytest.mark.asyncio
async def test_scan_literature_persist_false_does_not_write(
    db_session,
) -> None:
    """AC4: persist=False (dry-run) does not add any rows to the session."""
    await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session, corpus_id=str(literature_id), source_reference="src",
    )
    db_session.add(
        ExtractionChunk(
            job_id=job.id, content="nothing", chunk_index=0, token_count=1,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    would_persist = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
        persist=False,
    )

    assert len(would_persist) == 1
    assert would_persist[0].entity_type == "Material"
    assert would_persist[0].property == "density"
    # Nothing actually written.
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert rows == []
    steps = (
        await db_session.execute(
            select(ExtractionStep).where(ExtractionStep.step_type == "gap_scan"),
        )
    ).scalars().all()
    assert steps == []


@pytest.mark.asyncio
async def test_scan_literature_unknown_ontology_version_raises(
    db_session,
) -> None:
    """Unknown ontology_version string raises ValueError."""
    literature_id = uuid.uuid4()
    svc = GapScanService(db_session)
    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await svc.scan_literature(
            literature_id=literature_id, ontology_version="9.9.9-does-not-exist",
        )


# ---------------------------------------------------------------------------
# compute_recall (instance method, per-literature)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_recall_zero_zero_returns_one(db_session) -> None:
    """AC2: 0/0 → recall_rate = 1.0 per ADR."""
    await _seed_ontology_version(
        db_session, version="1.0.0", entity_types=[],
    )
    literature_id = uuid.uuid4()
    svc = GapScanService(db_session)

    metrics = await svc.compute_recall(
        literature_id=literature_id, ontology_version="1.0.0",
    )
    assert isinstance(metrics, RecallMetrics)
    assert metrics.total_expected == 0
    assert metrics.recall_rate == 1.0


@pytest.mark.asyncio
async def test_compute_recall_full_coverage_is_one(db_session) -> None:
    """All expected properties present → recall_rate = 1.0."""
    await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_id = uuid.uuid4()
    svc = GapScanService(db_session)

    metrics = await svc.compute_recall(
        literature_id=literature_id, ontology_version="1.0.0",
    )
    assert metrics.recall_rate == 1.0
    assert metrics.total_expected == 1
    assert metrics.open_gaps == 0


@pytest.mark.asyncio
async def test_compute_recall_partial_coverage_fraction(
    db_session,
) -> None:
    """Half properties missing → recall_rate = 0.5."""
    await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[
            {"name": "Material", "properties": ["density", "melting_point"]},
        ],
    )
    literature_id = uuid.uuid4()
    job = await _seed_job(
        db_session, corpus_id=str(literature_id), source_reference="src",
    )
    ov = (
        await db_session.execute(select(OntologyVersion))
    ).scalars().one()
    db_session.add(
        ExtractionGap(
            ontology_version_id=ov.id,
            entity_type="Material",
            property="density",
            gap_status="open",
        ),
    )
    await db_session.flush()
    assert job.id is not None

    svc = GapScanService(db_session)
    metrics = await svc.compute_recall(
        literature_id=literature_id, ontology_version="1.0.0",
    )
    assert metrics.total_expected == 2
    assert metrics.open_gaps == 1
    assert metrics.recall_rate == 0.5


@pytest.mark.asyncio
async def test_compute_recall_unknown_ontology_raises(
    db_session,
) -> None:
    """compute_recall raises ValueError when ontology_version is unknown."""
    literature_id = uuid.uuid4()
    svc = GapScanService(db_session)
    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await svc.compute_recall(
            literature_id=literature_id, ontology_version="nope",
        )


# ---------------------------------------------------------------------------
# compute_coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_coverage_zero_literatures_returns_zero(
    db_session,
) -> None:
    """AC3: 0/0 → coverage_rate = 0.0 (documented choice)."""
    await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    svc = GapScanService(db_session)
    metrics = await svc.compute_coverage(ontology_version="1.0.0")
    assert isinstance(metrics, CoverageMetrics)
    assert metrics.literature_total == 0
    assert metrics.literature_fully_covered == 0
    assert metrics.coverage_rate == 0.0


@pytest.mark.asyncio
async def test_compute_coverage_all_fully_covered_is_one(
    db_session,
) -> None:
    """Two literatures, both fully covered → coverage_rate = 1.0."""
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    for _ in range(2):
        await _seed_job(
            db_session,
            corpus_id=str(uuid.uuid4()),
            source_reference="src",
            ontology_version_id=ov.id,
        )
    await db_session.flush()

    svc = GapScanService(db_session)
    metrics = await svc.compute_coverage(ontology_version="1.0.0")
    assert metrics.literature_total == 2
    assert metrics.literature_fully_covered == 2
    assert metrics.coverage_rate == 1.0
    assert metrics.gap_distribution == {}


@pytest.mark.asyncio
async def test_compute_coverage_partial_fraction(db_session) -> None:
    """Two literatures, one fully covered, one with a gap → 0.5."""
    ov = await _seed_ontology_version(
        db_session,
        version="1.0.0",
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    literature_with_gap_id = uuid.uuid4()
    literature_clean_id = uuid.uuid4()
    bad_job = await _seed_job(
        db_session,
        corpus_id=str(literature_with_gap_id),
        source_reference="bad",
        ontology_version_id=ov.id,
    )
    await _seed_job(
        db_session,
        corpus_id=str(literature_clean_id),
        source_reference="clean",
        ontology_version_id=ov.id,
    )
    # Link the gap to a chunk in bad_job so coverage can attribute it
    # to bad_job's literature (the only path until T1 adds
    # ``literature_id`` on ``extraction_gaps``).
    bad_chunk = ExtractionChunk(
        job_id=bad_job.id,
        content="missing density value",
        chunk_index=0,
        token_count=4,
    )
    db_session.add(bad_chunk)
    await db_session.flush()
    db_session.add(
        ExtractionGap(
            ontology_version_id=ov.id,
            entity_type="Material",
            property="density",
            gap_status="open",
            chunk_id=bad_chunk.id,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    metrics = await svc.compute_coverage(ontology_version="1.0.0")
    assert metrics.literature_total == 2
    assert metrics.literature_fully_covered == 1
    assert metrics.coverage_rate == 0.5
    assert metrics.gap_distribution == {("Material", "density"): 1}


@pytest.mark.asyncio
async def test_compute_coverage_unknown_ontology_raises(
    db_session,
) -> None:
    """compute_coverage raises ValueError for an unknown version string."""
    svc = GapScanService(db_session)
    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await svc.compute_coverage(ontology_version="missing")


# ---------------------------------------------------------------------------
# CR-rejected regression: compute_coverage must be per-OV scoped
# (NFM-2697-T3 bug fix; CR comment 7d9c688a-fed1-497f-bf7c-2ecc4d61954e)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_coverage_is_per_ontology_version_scoped(
    db_session,
) -> None:
    """Two OVs, each with its own literature: coverage reports per-OV totals.

    Regression for the CR revert-probe.  Prior to the fix,
    ``_literature_ids_for_ontology`` loaded every ``ExtractionJob`` row
    without an OV filter, so both OVs reported the same union of all
    literatures and identical coverage rates.  After the fix each OV's
    coverage reflects only its own ExtractionJob rows.
    """
    entity_types = [{"name": "Material", "properties": ["density"]}]
    ov_a = await _seed_ontology_version(
        db_session, version="1.0.0", entity_types=entity_types,
    )
    ov_b = await _seed_ontology_version(
        db_session, version="2.0.0", entity_types=entity_types,
    )

    # OV-A owns literature_a only.
    literature_a = uuid.uuid4()
    await _seed_job(
        db_session,
        corpus_id=str(literature_a),
        source_reference="src-a",
        ontology_version_id=ov_a.id,
    )
    # OV-B owns literature_b only.
    literature_b = uuid.uuid4()
    await _seed_job(
        db_session,
        corpus_id=str(literature_b),
        source_reference="src-b",
        ontology_version_id=ov_b.id,
    )
    await db_session.flush()

    svc = GapScanService(db_session)

    cov_a = await svc.compute_coverage(ontology_version="1.0.0")
    cov_b = await svc.compute_coverage(ontology_version="2.0.0")

    # Each OV must see only its own literature, NOT the union.
    assert cov_a.literature_total == 1, (
        f"OV 1.0.0 expected literature_total=1 (just literature_a), "
        f"got {cov_a.literature_total}"
    )
    assert cov_b.literature_total == 1, (
        f"OV 2.0.0 expected literature_total=1 (just literature_b), "
        f"got {cov_b.literature_total}"
    )
    # Fully covered (no gaps attached to either literature yet).
    assert cov_a.literature_fully_covered == 1
    assert cov_b.literature_fully_covered == 1
    assert cov_a.coverage_rate == 1.0
    assert cov_b.coverage_rate == 1.0


@pytest.mark.asyncio
async def test_compute_coverage_per_ov_does_not_count_other_ovs_gaps(
    db_session,
) -> None:
    """Gaps tied to OV-B's job must not bleed into OV-A's coverage.

    Reinforces the per-OV scoping for the open-gap attribution query.
    """
    entity_types = [{"name": "Material", "properties": ["density"]}]
    ov_a = await _seed_ontology_version(
        db_session, version="1.0.0", entity_types=entity_types,
    )
    ov_b = await _seed_ontology_version(
        db_session, version="2.0.0", entity_types=entity_types,
    )

    literature_a = uuid.uuid4()
    literature_b = uuid.uuid4()
    job_a = await _seed_job(
        db_session,
        corpus_id=str(literature_a),
        source_reference="src-a",
        ontology_version_id=ov_a.id,
    )
    bad_job_b = await _seed_job(
        db_session,
        corpus_id=str(literature_b),
        source_reference="src-b",
        ontology_version_id=ov_b.id,
    )
    bad_chunk_b = ExtractionChunk(
        job_id=bad_job_b.id,
        content="missing density",
        chunk_index=0,
        token_count=2,
    )
    db_session.add(bad_chunk_b)
    # Job-A also has a chunk so the literature attribution join finds it.
    db_session.add(
        ExtractionChunk(
            job_id=job_a.id,
            content="some density mention",
            chunk_index=0,
            token_count=3,
        ),
    )
    await db_session.flush()
    # An open gap tied to OV-B's chunk must NOT count for OV-A.
    db_session.add(
        ExtractionGap(
            ontology_version_id=ov_b.id,
            entity_type="Material",
            property="density",
            gap_status="open",
            chunk_id=bad_chunk_b.id,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    cov_a = await svc.compute_coverage(ontology_version="1.0.0")
    cov_b = await svc.compute_coverage(ontology_version="2.0.0")

    # OV-A: only literature_a, no OV-A gaps -> fully covered.
    assert cov_a.literature_total == 1
    assert cov_a.literature_fully_covered == 1
    assert cov_a.coverage_rate == 1.0
    assert cov_a.gap_distribution == {}

    # OV-B: only literature_b, but has a gap -> not fully covered.
    assert cov_b.literature_total == 1
    assert cov_b.literature_fully_covered == 0
    assert cov_b.coverage_rate == 0.0
    assert cov_b.gap_distribution == {("Material", "density"): 1}


@pytest.mark.asyncio
async def test_scan_literature_per_ov_does_not_match_other_ovs_jobs(
    db_session,
) -> None:
    """scan_literature must scope jobs to the requested OV only.

    Without the OV filter on the job lookup, scanning a literature
    under OV-A would also pull in jobs tagged with OV-B that happen
    to share a corpus_id, producing false matches.
    """
    entity_types = [{"name": "Material", "properties": ["density"]}]
    ov_a = await _seed_ontology_version(
        db_session, version="1.0.0", entity_types=entity_types,
    )
    ov_b = await _seed_ontology_version(
        db_session, version="2.0.0", entity_types=entity_types,
    )

    literature_id = uuid.uuid4()
    # Job tagged with OV-A whose chunk mentions density -> present.
    job_a = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src-a",
        ontology_version_id=ov_a.id,
    )
    db_session.add(
        ExtractionChunk(
            job_id=job_a.id,
            content="density is 7.85",
            chunk_index=0,
            token_count=4,
        ),
    )
    # Job tagged with OV-B under the same literature id but a chunk
    # WITHOUT density -> should not be matched under OV-A scan.
    job_b = await _seed_job(
        db_session,
        corpus_id=str(literature_id),
        source_reference="src-b",
        ontology_version_id=ov_b.id,
    )
    db_session.add(
        ExtractionChunk(
            job_id=job_b.id,
            content="no value here",
            chunk_index=0,
            token_count=3,
        ),
    )
    await db_session.flush()

    svc = GapScanService(db_session)
    # Scan under OV-A: job_a's chunk mentions density, so no gap.
    gaps_a = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="1.0.0",
    )
    assert gaps_a == []

    # Scan under OV-B: job_b's chunk has no density -> gap created.
    gaps_b = await svc.scan_literature(
        literature_id=literature_id,
        ontology_version="2.0.0",
    )
    assert len(gaps_b) == 1
    assert gaps_b[0].ontology_version_id == ov_b.id
    assert gaps_b[0].property == "density"
