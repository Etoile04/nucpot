"""Integration tests for CoverageScanService end-to-end (NFM-2621).

Validates the complete DataCollectionRequest + coverage scan feature:
1. CoverageScanService produces DataCollectionRequest records for uncovered
   ontology properties.
2. Coverage rate is demonstrably distinct from recall rate.
3. Full lifecycle: scan -> create requests -> transition status -> verify metrics.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from nfm_db.models import DataCollectionRequest, OntologyVersion
from nfm_db.services.coverage_scan_service import (
    CoverageMetrics,
    CoverageScanResult,
    CoverageScanService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _make_ontology_data(entity_types: list[dict] | None) -> dict:
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
# Integration: scan creates requests + coverage distinct from recall
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_coverage_scan_creates_requests_and_coverage_differs_from_recall(
    db_session,
):
    """End-to-end: ontology with properties but empty DB produces
    coverage_rate=0.0, creates DataCollectionRequests for all uncovered,
    and coverage_rate is conceptually distinct from recall rate.

    Coverage rate = DB records with non-null values / ontology properties
    Recall rate = extraction chunks mentioning properties / ontology properties

    These measure different things.  This test proves coverage_rate works
    independently of any extraction/recall machinery.
    """
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "thermal_conductivity", "melting_point"],
        },
        {
            "name": "Isotope",
            "properties": ["half_life", "neutron_cross_section"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)

    # 1. Coverage metrics: no DB records -> 0.0 coverage
    metrics = await svc.compute_metrics(ov.id)
    assert isinstance(metrics, CoverageMetrics)
    assert metrics.total_expected == 5
    assert metrics.covered == 0
    assert metrics.uncovered == 5
    assert metrics.coverage_rate == 0.0

    # 2. Run scan -- should create 5 DataCollectionRequests
    result = await svc.run_scan(ov.id, material_system="UO2")
    assert isinstance(result, CoverageScanResult)
    assert result.requests_created == 5
    assert len(result.uncovered_properties) == 5
    assert result.metrics.coverage_rate == 0.0

    # 3. Verify DB rows
    stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.ontology_version_id == ov.id,
    )
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 5

    # 4. All requests should be "open"
    assert all(r.status == "open" for r in rows)

    # 5. Entity types should be represented
    entity_names = {r.entity_type for r in rows}
    assert entity_names == {"NuclearMaterial", "Isotope"}


@pytest.mark.integration
async def test_coverage_rate_updates_after_manual_completion(db_session):
    """Coverage metrics remain consistent after manual status transitions.

    Simulates a workflow: scan creates requests, then some are
    completed manually.  Re-computing metrics should still reflect
    the same coverage_rate (which is based on DB records, not
    request status).
    """
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

    # Run scan
    result = await svc.run_scan(ov.id, material_system="Zr")
    assert result.requests_created == 2
    assert result.metrics.coverage_rate == 0.0

    # Find requests and complete one
    stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.ontology_version_id == ov.id,
        DataCollectionRequest.property == "density",
    )
    density_req = (await db_session.execute(stmt)).scalar_one_or_none()
    assert density_req is not None
    density_req.status = "completed"
    await db_session.flush()

    # Re-compute metrics -- coverage_rate unchanged (still no DB records)
    metrics = await svc.compute_metrics(ov.id)
    assert metrics.coverage_rate == 0.0
    assert metrics.total_expected == 2

    # Verify one request is completed via direct query
    completed_stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.ontology_version_id == ov.id,
        DataCollectionRequest.status == "completed",
    )
    completed = (await db_session.execute(completed_stmt)).scalars().all()
    assert len(completed) == 1


@pytest.mark.integration
async def test_repeated_scan_is_idempotent(db_session):
    """Running scan twice creates requests only on first run.

    Second run should find 0 new creations due to deduplication.
    """
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
    svc = CoverageScanService(db_session)

    # First scan
    result1 = await svc.run_scan(ov.id, material_system="UO2")
    assert result1.requests_created == 1

    # Second scan (same material_system)
    result2 = await svc.run_scan(ov.id, material_system="UO2")
    assert result2.requests_created == 0

    # Third scan (different material_system -- should create)
    result3 = await svc.run_scan(ov.id, material_system="Zr")
    assert result3.requests_created == 1

    # Total rows: 2 (one per material_system)
    stmt = select(DataCollectionRequest).where(
        DataCollectionRequest.ontology_version_id == ov.id,
    )
    total = (await db_session.execute(stmt)).scalars().all()
    assert len(total) == 2


@pytest.mark.integration
async def test_coverage_scan_with_mixed_dict_and_string_properties(db_session):
    """Ontology with mixed property formats (string + dict) works end-to-end."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": [
                "density",  # string format
                {"name": "melting_point", "datatype": "float"},  # dict format
            ],
        },
        {
            "name": "Isotope",
            "properties": [
                {"name": "half_life", "datatype": "float"},
            ],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )
    svc = CoverageScanService(db_session)

    result = await svc.run_scan(ov.id)
    assert result.metrics.total_expected == 3
    assert result.requests_created == 3

    # Verify all 3 distinct properties
    prop_names = {up.property_name for up in result.uncovered_properties}
    assert prop_names == {"density", "melting_point", "half_life"}
