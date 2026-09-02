"""Tests for CoverageScanService (NFM-2620).

Compares ontology schema (entity_types + declared properties) against
actual DB records and creates DataCollectionRequest rows for uncovered
properties.

Covers:
1. Coverage rate computation from ontology vs DB records.
2. DataCollectionRequest creation for uncovered properties.
3. Deduplication: no duplicate requests for same triple.
4. ValueError on missing ontology version.
5. Empty ontology / no entity_types returns 1.0 coverage rate.
6. Coverage rate distinct from recall rate conceptually.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from nfm_db.models import DataCollectionRequest, OntologyVersion
from nfm_db.services.coverage_scan_service import (
    CoverageMetrics,
    CoverageScanResult,
    CoverageScanService,
    UncoveredProperty,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _make_ontology_data(
    entity_types: list[dict] | None,
) -> dict:
    """Wrap entity_types in a minimal ontology payload."""
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


# ---------------------------------------------------------------------------
# Tests: CoverageMetrics computation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_compute_metrics_empty_ontology(db_session):
    """Empty ontology (no entity_types) returns coverage_rate=1.0."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    assert isinstance(metrics, CoverageMetrics)
    assert metrics.total_expected == 0
    assert metrics.covered == 0
    assert metrics.uncovered == 0
    assert metrics.coverage_rate == 1.0


@pytest.mark.unit
async def test_compute_metrics_no_db_records(db_session):
    """Ontology with properties but no DB records returns 0.0 coverage."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "thermal_conductivity"],
        },
        {
            "name": "Isotope",
            "properties": ["half_life"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    assert metrics.total_expected == 3
    assert metrics.covered == 0
    assert metrics.uncovered == 3
    assert metrics.coverage_rate == 0.0


@pytest.mark.unit
async def test_compute_metrics_missing_ontology_version(db_session):
    """compute_metrics raises ValueError for non-existent ontology version."""
    svc = CoverageScanService(db_session)
    fake_id = uuid.uuid4()

    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await svc.compute_metrics(fake_id)


# ---------------------------------------------------------------------------
# Tests: run_scan + DataCollectionRequest creation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_run_scan_creates_requests_for_uncovered(db_session):
    """run_scan creates DataCollectionRequest for uncovered properties."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "thermal_conductivity"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)
    result = await svc.run_scan(ov.id, material_system="UO2")

    assert isinstance(result, CoverageScanResult)
    assert result.metrics.total_expected == 2
    assert result.requests_created == 2
    assert len(result.uncovered_properties) == 2

    # Verify DB rows were created
    stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.ontology_version_id == ov.id,
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2

    entity_names = {r.entity_type for r in rows}
    prop_names = {r.property for r in rows}
    assert "NuclearMaterial" in entity_names
    assert "density" in prop_names
    assert "thermal_conductivity" in prop_names


@pytest.mark.unit
async def test_run_scan_no_duplicate_requests(db_session):
    """run_scan skips creating requests for existing triples."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )

    # Pre-seed an existing request
    existing = DataCollectionRequest(
        ontology_version_id=ov.id,
        entity_type="NuclearMaterial",
        property="density",
        material_system="UO2",
        status="open",
    )
    db_session.add(existing)
    await db_session.flush()

    svc = CoverageScanService(db_session)
    result = await svc.run_scan(ov.id, material_system="UO2")

    # Should NOT create a duplicate
    assert result.requests_created == 0
    assert len(result.uncovered_properties) == 1  # Still uncovered


@pytest.mark.unit
async def test_run_scan_empty_ontology(db_session):
    """Empty ontology creates no requests, coverage_rate=1.0."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    svc = CoverageScanService(db_session)
    result = await svc.run_scan(ov.id)

    assert result.metrics.coverage_rate == 1.0
    assert result.requests_created == 0
    assert result.uncovered_properties == []


@pytest.mark.unit
async def test_run_scan_dict_properties_format(db_session):
    """Ontology with dict-style properties (name/datatype) works."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": [
                {"name": "density", "datatype": "float"},
                {"name": "melting_point", "datatype": "float"},
            ],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)
    result = await svc.run_scan(ov.id)

    assert result.metrics.total_expected == 2
    assert result.requests_created == 2


@pytest.mark.unit
async def test_run_scan_missing_ontology_version(db_session):
    """run_scan raises ValueError for non-existent ontology version."""
    svc = CoverageScanService(db_session)
    fake_id = uuid.uuid4()

    with pytest.raises(ValueError, match="OntologyVersion not found"):
        await svc.run_scan(fake_id)


# ---------------------------------------------------------------------------
# Tests: coverage rate distinct from recall rate
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_coverage_rate_is_zero_when_no_db_records(db_session):
    """Coverage rate should be 0 when ontology has properties but DB empty.

    This is conceptually distinct from recall rate: recall checks extraction
    chunks, coverage checks actual DB records.
    """
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "thermal_conductivity", "elastic_modulus"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    # No DB records exist → coverage is 0
    assert metrics.coverage_rate == 0.0
    assert metrics.total_expected == 3
    assert metrics.covered == 0
    assert metrics.uncovered == 3


# ---------------------------------------------------------------------------
# Tests: Result dataclass immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_coverage_metrics_frozen():
    """CoverageMetrics is frozen (immutable)."""
    metrics = CoverageMetrics(
        ontology_version_id=uuid.uuid4(),
        total_expected=10,
        covered=5,
        uncovered=5,
        coverage_rate=0.5,
        computed_at=datetime.now(UTC),
    )
    with pytest.raises(AttributeError):
        metrics.coverage_rate = 0.9  # type: ignore[misc]


@pytest.mark.unit
def test_uncovered_property_frozen():
    """UncoveredProperty is frozen (immutable)."""
    up = UncoveredProperty(
        entity_type="NuclearMaterial",
        property_name="density",
    )
    with pytest.raises(AttributeError):
        up.entity_type = "Other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: BUG-22 fix — Path A (NFM-3874 / C-S3)
#
# Coverage scan historically returned coverage_rate=1.0 for any ontology
# whose ``entity_types[].properties`` was empty, because the scanner only
# read the declared ``properties`` field.  In v0.4.0 (and later) the
# authoritative property list lives under ``datatype_properties`` (279
# entries keyed by property name), and ``entity_types`` carries only
# ``name`` + ``description``.  Path A is the runtime stop-gap: when
# declared properties are absent, fall back to ``datatype_properties``.
# ---------------------------------------------------------------------------


def _make_ontology_data_v040(entity_types: list[dict]) -> dict:
    """Wrap entity_types + datatype_properties in the v0.4.0 ontology shape."""
    return {
        "entity_types": entity_types,
        "relation_types": [],
        "datatype_properties": {
            "hasDensity": {
                "uri": "http://example.org/materials/composition#hasDensity",
                "comment": "Density",
                "domain": None,
            },
            "hasMeltingPoint": {
                "uri": "http://example.org/materials/composition#hasMeltingPoint",
                "comment": "Melting point",
                "domain": None,
            },
            "hasThermalConductivity": {
                "uri": "http://example.org/materials/composition#hasThermalConductivity",
                "comment": "Thermal conductivity",
                "domain": None,
            },
        },
    }


@pytest.mark.unit
async def test_path_a_falls_back_to_datatype_properties(db_session):
    """Path A: empty entity_types[].properties falls back to datatype_properties.

    Reproduces BUG-22: without the fallback, total_expected=0 and
    coverage_rate=1.0 (a vacuous truth). With the fallback, total_expected
    reflects the 3 datatype_property keys.
    """
    # entity_types carry NO properties — same as v0.4.0 production data
    entity_types = [
        {"name": "Material", "description": "A nuclear fuel or structural material"},
        {"name": "Property", "description": "A physical/material property"},
        {"name": "Condition", "description": "Experimental conditions"},
        {"name": "Experiment", "description": "An experimental measurement"},
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data_v040(entity_types),
    )
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    # The 3 datatype_properties keys are now the expected set
    assert metrics.total_expected == 3
    assert metrics.covered == 0
    assert metrics.uncovered == 3
    # 0/3 != 1.0 any more — BUG-22 fixed
    assert metrics.coverage_rate == 0.0


@pytest.mark.unit
async def test_path_a_run_scan_creates_requests_from_datatype_properties(db_session):
    """Path A: run_scan emits DataCollectionRequest rows for datatype_properties."""
    entity_types = [
        {"name": "Material", "description": "A nuclear fuel or structural material"},
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data_v040(entity_types),
    )
    svc = CoverageScanService(db_session)
    result = await svc.run_scan(ov.id, material_system="UO2")

    assert result.metrics.total_expected == 3
    assert result.requests_created == 3
    prop_names = {r.property_name for r in result.uncovered_properties}
    assert prop_names == {"hasDensity", "hasMeltingPoint", "hasThermalConductivity"}


@pytest.mark.unit
async def test_path_a_declared_properties_take_precedence(db_session):
    """Path A: when entity_types DO declare properties, the fallback is skipped.

    Ensures the fix doesn't double-count when both sources are present.
    """
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density"],
        },
    ]
    # Provide datatype_properties too — Path A must NOT add them
    ontology_data = {
        "entity_types": entity_types,
        "relation_types": [],
        "datatype_properties": {
            "hasDensity": {"uri": "x", "comment": "y", "domain": None},
            "hasMeltingPoint": {"uri": "x", "comment": "y", "domain": None},
        },
    }
    ov = await _seed_version(db_session, ontology_data=ontology_data)
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    # Only the declared "density" counts; datatype_properties is ignored
    assert metrics.total_expected == 1
    assert metrics.coverage_rate == 0.0


@pytest.mark.unit
async def test_path_a_no_datatype_properties_returns_zero(db_session):
    """Path A edge case: empty entity_types AND empty datatype_properties
    → no false coverage (regression guard for the original bug)."""
    ov = await _seed_version(
        db_session,
        ontology_data={
            "entity_types": [{"name": "Material", "description": "x"}],
            "relation_types": [],
        },
    )
    svc = CoverageScanService(db_session)
    metrics = await svc.compute_metrics(ov.id)

    assert metrics.total_expected == 0
    assert metrics.coverage_rate == 1.0  # Empty corpus semantics preserved


@pytest.mark.unit
def test_iter_datatype_property_names_unit():
    """Path A helper: yields property names from ontology_data['datatype_properties']."""
    from nfm_db.services.gap_scanner import iter_datatype_property_names

    class _StubOV:
        ontology_data = {
            "entity_types": [{"name": "Material"}],
            "datatype_properties": {
                "hasDensity": {"uri": "x"},
                "hasMeltingPoint": {"uri": "x"},
                "": {"uri": "x"},  # empty key — must be filtered
            },
        }

    names = list(iter_datatype_property_names(_StubOV()))
    assert names == ["hasDensity", "hasMeltingPoint"]


@pytest.mark.unit
def test_extract_expected_pairs_unit():
    """Path A: extract_expected_pairs prefers declared, falls back to dtp."""
    from nfm_db.services.gap_scanner import extract_expected_pairs

    class _StubOV:
        # declared properties present → fallback not used
        ontology_data = {
            "entity_types": [
                {"name": "M", "properties": ["a", "b"]},
            ],
        }

    pairs = extract_expected_pairs(_StubOV())
    assert pairs == [("M", "a"), ("M", "b")]

    class _StubOVFallback:
        # declared properties empty → fall back to datatype_properties
        ontology_data = {
            "entity_types": [{"name": "Material", "description": "x"}],
            "datatype_properties": {
                "hasDensity": {"uri": "x"},
                "hasMass": {"uri": "x"},
            },
        }

    pairs = extract_expected_pairs(_StubOVFallback())
    assert ("Material", "hasDensity") in pairs
    assert ("Material", "hasMass") in pairs
    assert len(pairs) == 2
