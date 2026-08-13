"""Tests for ExtractionGapScanner (NFM-2596).

ExtractionGapScanner is a thin subclass of GapScanService that overrides
``_entity_types`` to consume the *mapping* ontology shape::

    {"entity_types": {"Fuel": {"properties": ["density"]}}}

rather than the parent's list shape::

    {"entity_types": [{"name": "Fuel", "properties": ["density"]}]}

The full scan pipeline (load ontology → load chunks → load existing gaps
→ compute pairs → dedup → create rows → write ExtractionStep) lives on
the parent; these tests focus on the override behaviour plus the
end-to-end count contract for the mapping shape.

The parent's list-shape behaviour is covered comprehensively in
``tests/services/test_gap_scanner_service.py``; we deliberately do not
duplicate those 15 tests here.  Instead, these tests stub the parent's
``_load_*`` helpers (the only ones the override touches via the
inherited ``scan_for_gaps``), then inspect the ``ExtractionGap`` and
``ExtractionStep`` rows added to the mocked session.

Spec-mandated tests (NFM-2596 acceptance criteria):

1. test_scan_finds_gaps_when_chunks_missing_properties
2. test_scan_no_gaps_when_all_properties_covered
3. test_scan_deduplication_skips_existing_open_gaps
4. test_scan_deduplication_skips_filled_gaps
5. test_scan_empty_ontology_returns_zero
6. test_scan_creates_extraction_step
7. test_scan_records_duration

Plus the pre-existing single-shape test retained for the override proof.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from nfm_db.models.extraction_gap import ExtractionGap
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.services.extraction_gap_scanner import ExtractionGapScanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> Mock:
    """Build a mock AsyncSession sufficient for ``scan_for_gaps``.

    ``add`` is a synchronous Mock (the real ``Session.add`` is sync),
    ``flush`` is an AsyncMock (the real ``AsyncSession.flush`` is async).
    """
    return Mock(add=Mock(), flush=AsyncMock())


def _make_ontology(
    ontology_data: dict[str, Any] | None,
) -> Mock:
    """Build a mock OntologyVersion exposing ``id``, ``version``, and ``ontology_data``."""
    return Mock(id=uuid4(), version="1.0.0", ontology_data=ontology_data)


def _added_gaps(session: Mock) -> list[ExtractionGap]:
    """Extract every ExtractionGap passed to ``session.add``."""
    return [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ExtractionGap)
    ]


def _added_steps(session: Mock) -> list[ExtractionStep]:
    """Extract every ExtractionStep passed to ``session.add``."""
    return [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ExtractionStep)
    ]


def _stub_loaders(
    scanner: ExtractionGapScanner,
    *,
    ontology: Mock,
    chunks: list[Mock],
    existing_gaps: list[Mock],
) -> None:
    """Bypass real DB I/O on the parent's three loader helpers."""
    scanner._load_ontology = AsyncMock(return_value=ontology)
    scanner._load_chunks = AsyncMock(return_value=chunks)
    scanner._load_existing_gaps = AsyncMock(return_value=existing_gaps)


def _mapping_ontology(
    entities: dict[str, dict[str, Any]],
) -> Mock:
    """Wrap a mapping-shape entity_types dict in an ontology mock."""
    return _make_ontology({"entity_types": entities})


def _chunk(content: str) -> Mock:
    """Build a mock ExtractionChunk exposing ``content``."""
    return Mock(content=content)


# ---------------------------------------------------------------------------
# Pre-existing test: proof the override accepts the mapping shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scanner_accepts_mapping_ontology_shape() -> None:
    """The mapping ontology shape — ``entity_types`` is a dict keyed by
    entity name — is accepted by ExtractionGapScanner and produces the
    expected single gap.

    Retained from NFM-2596 first-pass implementation; the full mapping
    contract is covered by the spec-mandated tests below.
    """
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology_id = uuid4()
    ontology = Mock(
        id=ontology_id,
        ontology_data={"entity_types": {"Fuel": {"properties": ["density"]}}},
    )
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=[],
        existing_gaps=[],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=ontology_id,
    )

    assert result.total_expected == 1
    assert result.gaps_found == 1
    assert result.gaps_created == 1
    assert result.scan_duration_ms >= 0
    assert session.add.call_count == 2  # gap + step


# ---------------------------------------------------------------------------
# AC1: scan finds gaps when chunks are missing properties
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_finds_gaps_when_chunks_missing_properties() -> None:
    """3 entity_types x 2 properties = 6 expected; chunks cover only 2 ->
    4 gaps created with the correct (entity_type, property) pairs.
    """
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {
            "NuclearMaterial": {"properties": ["density", "melting_point"]},
            "Isotope": {"properties": ["half_life", "decay_mode"]},
            "Reactor": {"properties": ["power", "fuel_type"]},
        },
    )
    # Chunk mentions 'density' and 'half_life' only.
    chunks = [
        _chunk("density is 10.5 g/cm3 and half_life is 700 Myr"),
    ]
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=uuid4(),
    )

    # 6 expected; 2 covered by the chunk; 4 missing.
    assert result.total_expected == 6
    assert result.gaps_found == 4
    assert result.gaps_created == 4
    assert result.scan_duration_ms >= 0

    added = _added_gaps(session)
    assert len(added) == 4
    pairs = {(g.entity_type, g.property) for g in added}
    expected_missing = {
        ("NuclearMaterial", "melting_point"),
        ("Isotope", "decay_mode"),
        ("Reactor", "power"),
        ("Reactor", "fuel_type"),
    }
    assert pairs == expected_missing
    for gap in added:
        assert gap.gap_status == "open"
        assert gap.ontology_version == ontology.version
        assert gap.chunk_id is None


# ---------------------------------------------------------------------------
# AC2: scan finds no gaps when all properties are covered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_no_gaps_when_all_properties_covered() -> None:
    """All declared properties appear in the chunk content → 0 gaps."""
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {
            "NuclearMaterial": {"properties": ["density", "melting_point"]},
            "Isotope": {"properties": ["half_life"]},
        },
    )
    chunks = [
        _chunk(
            "density 10.5, melting_point 3120 K, half_life 700 Myr — "
            "all measured at 298 K",
        ),
    ]
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=uuid4(),
    )

    assert result.total_expected == 3
    assert result.gaps_found == 0
    assert result.gaps_created == 0
    assert _added_gaps(session) == []
    # The audit step still records.
    assert len(_added_steps(session)) == 1


# ---------------------------------------------------------------------------
# AC3: deduplication skips existing open gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_deduplication_skips_existing_open_gaps() -> None:
    """A pre-existing 'open' gap is not re-created."""
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {"Material": {"properties": ["density"]}},
    )
    chunks = [_chunk("no relevant value here")]

    # Pre-existing 'open' gap for the only missing (entity, property).
    existing_open = Mock(
        entity_type="Material",
        property="density",
        gap_status="open",
    )
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[existing_open],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=uuid4(),
    )

    assert result.total_expected == 1
    assert result.gaps_found == 1  # still missing in chunks
    assert result.gaps_created == 0  # already tracked
    assert _added_gaps(session) == []


# ---------------------------------------------------------------------------
# AC3 cont.: deduplication skips existing filled gaps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_deduplication_skips_filled_gaps() -> None:
    """A pre-existing 'filled' gap is not reopened."""
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {"Material": {"properties": ["density"]}},
    )
    chunks = [_chunk("no relevant value here")]

    existing_filled = Mock(
        entity_type="Material",
        property="density",
        gap_status="filled",
    )
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[existing_filled],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=uuid4(),
    )

    assert result.gaps_created == 0
    assert _added_gaps(session) == []


# ---------------------------------------------------------------------------
# AC5: empty ontology handled gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_empty_ontology_returns_zero() -> None:
    """``ontology_data`` is ``None`` *or* lacks ``entity_types`` → 0
    expected, 0 found, 0 created, no exceptions.  The audit step is
    still recorded.
    """
    cases: list[tuple[str, dict[str, Any] | None]] = [
        ("none_ontology_data", None),
        ("empty_dict", {}),
        ("empty_entity_types_dict", {"entity_types": {}}),
        ("missing_entity_types_key", {"relation_types": []}),
    ]

    for label, ontology_data in cases:
        session = _make_session()
        scanner = ExtractionGapScanner(session)
        ontology = _make_ontology(ontology_data)
        _stub_loaders(
            scanner,
            ontology=ontology,
            chunks=[_chunk("density = 10.5")],
            existing_gaps=[],
        )

        result = await scanner.scan_for_gaps(
            job_id=uuid4(),
            ontology_version_id=uuid4(),
        )

        assert result.total_expected == 0, f"{label}: total_expected"
        assert result.gaps_found == 0, f"{label}: gaps_found"
        assert result.gaps_created == 0, f"{label}: gaps_created"
        assert _added_gaps(session) == [], f"{label}: added_gaps"
        # The audit step is still written even when nothing was scanned.
        assert len(_added_steps(session)) == 1, f"{label}: audit step"


# ---------------------------------------------------------------------------
# AC4: ExtractionStep with step_type="gap_scan" is created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_creates_extraction_step() -> None:
    """A single ``ExtractionStep`` with ``step_type='gap_scan'`` and
    ``status='completed'`` is recorded after every scan, regardless of
    how many gaps were created.
    """
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {"Material": {"properties": ["density"]}},
    )
    chunks = [_chunk("density 10.5 g/cm3")]

    job_id = uuid4()
    ontology_id = uuid4()
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[],
    )

    await scanner.scan_for_gaps(
        job_id=job_id,
        ontology_version_id=ontology_id,
    )

    steps = _added_steps(session)
    assert len(steps) == 1
    step = steps[0]
    assert step.step_type == "gap_scan"
    assert step.status == "completed"
    assert step.job_id == job_id
    assert step.completed_at is not None
    # Metadata reflects the scan outcome.
    meta = step.metadata_
    assert isinstance(meta, dict)
    assert meta["ontology_version_id"] == str(ontology_id)
    assert meta["total_expected"] == 1
    assert meta["gaps_found"] == 0
    assert meta["gaps_created"] == 0
    assert meta["scan_duration_ms"] >= 0


# ---------------------------------------------------------------------------
# AC6: scan_duration_ms is recorded and non-negative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_records_duration() -> None:
    """The returned ``GapScanResult.scan_duration_ms`` is a non-negative
    integer measured in milliseconds.
    """
    session = _make_session()
    scanner = ExtractionGapScanner(session)
    ontology = _mapping_ontology(
        {"Material": {"properties": ["density", "melting_point"]}},
    )
    chunks = [_chunk("density is 10.5; melting_point is 3120 K")]
    _stub_loaders(
        scanner,
        ontology=ontology,
        chunks=chunks,
        existing_gaps=[],
    )

    result = await scanner.scan_for_gaps(
        job_id=uuid4(),
        ontology_version_id=uuid4(),
    )

    assert isinstance(result.scan_duration_ms, int)
    assert result.scan_duration_ms >= 0

    # The same value is mirrored on the audit step's metadata_.
    steps = _added_steps(session)
    assert len(steps) == 1
    assert steps[0].metadata_["scan_duration_ms"] == result.scan_duration_ms
