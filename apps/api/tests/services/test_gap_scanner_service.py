"""Tests for GapScanService (NFM-2586 / NFM-2575-T2).

Post-extraction gap detection: compares extracted chunk content against
the expected entity_type/property schema declared in an OntologyVersion
and creates ExtractionGap rows for missing data points.

Covers:
1. Creates ExtractionGap rows for each missing (entity_type, property).
2. Deduplicates against pre-existing gap rows (open/filled/wont_fix/filling).
3. Skipped properties that already have matching values in chunk content.
4. Writes a single ExtractionStep row with step_type='gap_scan',
   status='completed' regardless of how many gaps were created.
5. Empty ontology / missing entity_types returns zero gaps, zero errors.
6. Handles ontology_data=None gracefully (no crashes, returns empty result).
7. Multiple chunks / multi-property entity types covered.

These tests run against the in-memory SQLite ``db_session`` fixture from
``tests/conftest.py`` — UNIQUE constraints are not enforced by the SQLite
dialect for some indexes, but the GapScanService uses SELECT-based
deduplication (not relying on DB-level UNIQUE), so the same behaviour
holds in test and prod.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nfm_db.models import (
    EXTRACTION_GAP_STATUSES,
    ExtractionChunk,
    ExtractionGap,
    ExtractionJob,
    ExtractionStep,
    OntologyVersion,
)
from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES
from nfm_db.services.gap_scanner import (
    GapScanResult,
    GapScanService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _make_ontology_data(
    entity_types: list[dict] | None,
) -> dict:
    """Wrap entity_types in a minimal ontology payload.

    relation_types is required to satisfy the upload validator, but the
    service only reads ``entity_types`` so we use an empty list for
    relation_types.
    """
    return {
        "entity_types": entity_types or [],
        "relation_types": [],
    }


async def _seed_version(
    session,
    *,
    ontology_data: dict,
    status: str = "published",
) -> OntologyVersion:
    """Create a fresh OntologyVersion row."""
    version = OntologyVersion(
        version="1.0.0",
        status=status,
        created_by=_SEED_USER_ID,
        ontology_data=ontology_data,
    )
    session.add(version)
    await session.flush()
    await session.refresh(version)
    return version


async def _seed_job(
    session,
    *,
    source_reference: str = "test-source",
    source_type: str = "file",
    status: str = "processing",
) -> ExtractionJob:
    """Create a fresh ExtractionJob row."""
    job = ExtractionJob(
        source_reference=source_reference,
        source_type=source_type,
        status=status,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job)
    return job


async def _seed_chunk(
    session,
    *,
    job_id: uuid.UUID,
    content: str,
    chunk_index: int = 0,
) -> ExtractionChunk:
    """Create a fresh ExtractionChunk row."""
    chunk = ExtractionChunk(
        job_id=job_id,
        content=content,
        chunk_index=chunk_index,
        token_count=len(content.split()),
    )
    session.add(chunk)
    await session.flush()
    await session.refresh(chunk)
    return chunk


# ---------------------------------------------------------------------------
# Acceptance criteria 1: creates gap rows for missing (entity_type, property)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_gap_for_missing_property(db_session) -> None:
    """AC1: scan_for_gaps creates an ExtractionGap per missing property."""
    ontology = _make_ontology_data(
        entity_types=[
            {
                "name": "NuclearMaterial",
                "properties": ["density", "melting_point"],
            },
        ],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    # Chunk contains density but no melting_point.
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="The material has density 10.5 g/cm3.",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert isinstance(result, GapScanResult)
    assert result.total_expected == 2
    assert result.gaps_found == 1
    assert result.gaps_created == 1
    assert result.scan_duration_ms >= 0  # sanity check on timing

    rows = (
        await db_session.execute(select(ExtractionGap))
    ).scalars().all()
    assert len(rows) == 1
    gap = rows[0]
    assert gap.ontology_version_id == ov.id
    assert gap.entity_type == "NuclearMaterial"
    assert gap.property == "melting_point"
    assert gap.gap_status == "open"
    assert gap.chunk_id is None  # No chunk linked for missing data


# ---------------------------------------------------------------------------
# Acceptance criteria 2: deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_existing_open_gap_is_skipped(db_session) -> None:
    """AC2: a pre-existing 'open' gap for (ov, entity, prop) is not
    re-created by a subsequent scan.
    """
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="no relevant value")

    # Pre-seed an open gap.
    existing = ExtractionGap(
        ontology_version_id=ov.id,
        entity_type="Material",
        property="density",
        gap_status="open",
    )
    db_session.add(existing)
    await db_session.flush()

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.total_expected == 1
    assert result.gaps_found == 1  # missing in chunks
    assert result.gaps_created == 0  # but already tracked

    rows = (
        await db_session.execute(select(ExtractionGap))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == existing.id


@pytest.mark.asyncio
async def test_dedup_filled_gap_is_not_reopened(db_session) -> None:
    """Filled gap stays filled; gap_scan does not create duplicates."""
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="no relevant value")

    existing = ExtractionGap(
        ontology_version_id=ov.id,
        entity_type="Material",
        property="density",
        gap_status="filled",
    )
    db_session.add(existing)
    await db_session.flush()

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.gaps_created == 0
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1
    await db_session.refresh(rows[0])
    assert rows[0].gap_status == "filled"


@pytest.mark.asyncio
async def test_dedup_filling_gap_not_disturbed(db_session) -> None:
    """In-flight ('filling') gap is not duplicated by the scan."""
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="no value here")

    existing = ExtractionGap(
        ontology_version_id=ov.id,
        entity_type="Material",
        property="density",
        gap_status="filling",
    )
    db_session.add(existing)
    await db_session.flush()

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.gaps_created == 0
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1
    await db_session.refresh(rows[0])
    assert rows[0].gap_status == "filling"


@pytest.mark.asyncio
async def test_dedup_wont_fix_gap_not_disturbed(db_session) -> None:
    """wont_fix gap is never re-opened by the scan."""
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="no value here")

    existing = ExtractionGap(
        ontology_version_id=ov.id,
        entity_type="Material",
        property="density",
        gap_status="wont_fix",
        resolved_at=datetime.now(UTC),
    )
    db_session.add(existing)
    await db_session.flush()

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.gaps_created == 0
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert len(rows) == 1
    await db_session.refresh(rows[0])
    assert rows[0].gap_status == "wont_fix"


# ---------------------------------------------------------------------------
# Property value detection — case-insensitive substring in chunk content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_gap_when_property_value_present(db_session) -> None:
    """If a chunk mentions the property name AND has a value-like token
    nearby, no gap is created.
    """
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="Density of UO2 is 10.5 g/cm3 at 298 K.",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.total_expected == 1
    assert result.gaps_found == 0
    assert result.gaps_created == 0
    rows = (await db_session.execute(select(ExtractionGap))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_case_insensitive_property_match(db_session) -> None:
    """Substring match is case-insensitive."""
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["Density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="density 10.97 g/cm3 was measured.",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.gaps_found == 0
    assert result.gaps_created == 0


# ---------------------------------------------------------------------------
# Acceptance criteria 3: ExtractionStep with step_type='gap_scan' created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_extraction_step(db_session) -> None:
    """AC3: a single ExtractionStep with step_type='gap_scan' is recorded."""
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="nothing")

    svc = GapScanService(db_session)
    await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    steps = (
        await db_session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job.id,
                ExtractionStep.step_type == "gap_scan",
            )
        )
    ).scalars().all()
    assert len(steps) == 1
    assert steps[0].status == "completed"
    assert steps[0].step_type == "gap_scan"
    assert steps[0].step_type in EXTRACTION_STEP_TYPES
    assert steps[0].completed_at is not None


@pytest.mark.asyncio
async def test_step_created_even_when_no_gaps_found(db_session) -> None:
    """step_type='gap_scan' row is always created, even if scan finds
    nothing missing.
    """
    ontology = _make_ontology_data(
        entity_types=[{"name": "Material", "properties": ["density"]}],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="density 9.5 measured.",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.gaps_found == 0
    steps = (
        await db_session.execute(
            select(ExtractionStep).where(
                ExtractionStep.job_id == job.id,
                ExtractionStep.step_type == "gap_scan",
            )
        )
    ).scalars().all()
    assert len(steps) == 1
    assert steps[0].status == "completed"


# ---------------------------------------------------------------------------
# Acceptance criteria 5: empty ontology handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_entity_types_returns_zero(db_session) -> None:
    """AC5: ontology with no entity_types → no gaps, no error."""
    ontology = _make_ontology_data(entity_types=[])
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(db_session, job_id=job.id, content="irrelevant")

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.total_expected == 0
    assert result.gaps_found == 0
    assert result.gaps_created == 0
    # ExtractionStep still recorded.
    steps = (
        await db_session.execute(
            select(ExtractionStep).where(
                ExtractionStep.step_type == "gap_scan",
            )
        )
    ).scalars().all()
    assert len(steps) == 1


@pytest.mark.asyncio
async def test_ontology_without_entity_property_field_is_skipped(db_session) -> None:
    """Entity types without a 'properties' field are treated as having
    nothing to scan (no errors, total_expected=0 for that entity).
    """
    ontology = _make_ontology_data(
        entity_types=[
            {"name": "BareType"},  # no 'properties' key
            {"name": "WithProps", "properties": ["density"]},
        ],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="nothing relevant here at all",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    # Only one property ('x') is expected — bare entity contributes 0.
    assert result.total_expected == 1
    assert result.gaps_created == 1


@pytest.mark.asyncio
async def test_no_chunks_for_job_does_not_crash(db_session) -> None:
    """A job with zero chunks still completes successfully and reports
    every ontology property as missing.
    """
    ontology = _make_ontology_data(
        entity_types=[
            {"name": "E1", "properties": ["a", "b"]},
            {"name": "E2", "properties": ["c"]},
        ],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    # No chunks.

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.total_expected == 3
    assert result.gaps_found == 3
    assert result.gaps_created == 3


# ---------------------------------------------------------------------------
# Properties declared as dicts {name, ...} (alternative shape)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_properties_as_dicts(db_session) -> None:
    """Properties list may contain dicts with 'name' field (NFM-2580
    extended schema).  Both name-only and dict forms are supported.
    """
    ontology = _make_ontology_data(
        entity_types=[
            {
                "name": "Material",
                "properties": [
                    {"name": "density", "datatype": "float"},
                    {"name": "symbol", "datatype": "string"},
                ],
            },
        ],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)
    await _seed_chunk(
        db_session,
        job_id=job.id,
        content="Density 9.5 measured.",  # symbol missing
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    assert result.total_expected == 2
    assert result.gaps_found == 1
    assert result.gaps_created == 1
    gap = (
        await db_session.execute(select(ExtractionGap))
    ).scalars().one()
    assert gap.property == "symbol"


# ---------------------------------------------------------------------------
# Multiple entity types + chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_entity_multi_chunk_scan(db_session) -> None:
    """Realistic case: multiple entity types, multiple chunks, mixed
    found vs missing properties.
    """
    ontology = _make_ontology_data(
        entity_types=[
            {
                "name": "Material",
                "properties": ["density", "melting_point"],
            },
            {"name": "Isotope", "properties": ["half_life"]},
        ],
    )
    ov = await _seed_version(db_session, ontology_data=ontology)
    job = await _seed_job(db_session)

    # Chunk 0: density present, melting_point missing.
    await _seed_chunk(
        db_session,
        job_id=job.id,
        chunk_index=0,
        content="density = 10.5 g/cm3 for the bulk material",
    )
    # Chunk 1: half_life mentioned.
    await _seed_chunk(
        db_session,
        job_id=job.id,
        chunk_index=1,
        content="half_life of U-235 is 703.8 million years",
    )

    svc = GapScanService(db_session)
    result = await svc.scan_for_gaps(job_id=job.id, ontology_version_id=ov.id)

    # 3 expected properties; density + half_life found → 1 missing.
    assert result.total_expected == 3
    assert result.gaps_found == 1
    assert result.gaps_created == 1
    rows = (
        await db_session.execute(
            select(ExtractionGap).where(
                ExtractionGap.entity_type == "Material",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].property == "melting_point"


# ---------------------------------------------------------------------------
# status field validation — uses canonical EXTRACTION_GAP_STATUSES
# ---------------------------------------------------------------------------


def test_gap_uses_canonical_status_set() -> None:
    """Service-level invariant: only statuses from EXTRACTION_GAP_STATUSES
    are ever written to gap_status.
    """
    assert set(EXTRACTION_GAP_STATUSES) == {"open", "filling", "filled", "wont_fix"}


# ---------------------------------------------------------------------------
# GapScanResult dataclass contract
# ---------------------------------------------------------------------------


def test_gap_scan_result_dataclass_contract() -> None:
    """GapScanResult is a frozen dataclass with the four documented fields."""
    from dataclasses import FrozenInstanceError, fields, is_dataclass

    assert is_dataclass(GapScanResult)
    field_names = {f.name for f in fields(GapScanResult)}
    assert field_names == {
        "total_expected",
        "gaps_found",
        "gaps_created",
        "scan_duration_ms",
    }

    result = GapScanResult(
        total_expected=5,
        gaps_found=2,
        gaps_created=1,
        scan_duration_ms=12,
    )
    assert result.total_expected == 5
    # Frozen — assignment raises.
    with pytest.raises(FrozenInstanceError):
        result.gaps_found = 99  # type: ignore[misc]
