"""API endpoint tests for DataCollectionRequest routes (NFM-2621).

Tests all 5 data collection endpoints:
1. GET  /api/v1/data-collection/requests -- paginated list
2. GET  /api/v1/data-collection/requests/{id} -- detail
3. PATCH /api/v1/data-collection/requests/{id}/status -- status transition
4. GET  /api/v1/data-collection/coverage/{ontology_version_id} -- coverage metrics
5. POST /api/v1/data-collection/scan -- trigger scan
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models import DataCollectionRequest, OntologyVersion

BASE = "/api/v1/data-collection"

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


async def _seed_version(session, *, ontology_data: dict) -> OntologyVersion:
    """Create and flush a minimal OntologyVersion."""
    ov = OntologyVersion(
        version="1.0.0",
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=ontology_data,
    )
    session.add(ov)
    await session.flush()
    await session.refresh(ov)
    return ov


async def _seed_request(
    session,
    *,
    ov: OntologyVersion,
    entity_type: str = "NuclearMaterial",
    property_name: str = "density",
    material_system: str = "UO2",
    status: str = "open",
) -> DataCollectionRequest:
    """Create and flush a DataCollectionRequest."""
    req = DataCollectionRequest(
        ontology_version_id=ov.id,
        entity_type=entity_type,
        property=property_name,
        material_system=material_system,
        status=status,
    )
    session.add(req)
    await session.flush()
    await session.refresh(req)
    return req


# ---------------------------------------------------------------------------
# 1. GET /data-collection/requests -- paginated list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_requests_empty(async_client, db_session) -> None:
    """Returns empty paginated list when no requests exist."""
    resp = await async_client.get(f"{BASE}/requests")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1
    assert data["pages"] == 0


@pytest.mark.asyncio
async def test_list_requests_with_data(async_client, db_session) -> None:
    """Returns paginated list of existing requests."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(db_session, ov=ov, property_name="density")
    await _seed_request(db_session, ov=ov, property_name="melting_point")

    resp = await async_client.get(f"{BASE}/requests")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["pages"] == 1

    # Items should have expected fields
    item = data["items"][0]
    assert "id" in item
    assert "entity_type" in item
    assert "property" in item
    assert "status" in item


@pytest.mark.asyncio
async def test_list_requests_filter_by_status(async_client, db_session) -> None:
    """Status query param filters results."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(db_session, ov=ov, property_name="density", status="open")
    await _seed_request(
        db_session, ov=ov, property_name="melting_point", status="completed",
    )

    resp = await async_client.get(
        f"{BASE}/requests",
        params={"status": "open"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["property"] == "density"
    assert data["items"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_list_requests_filter_by_entity_type(async_client, db_session) -> None:
    """entity_type query param filters results."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(
        db_session, ov=ov, entity_type="NuclearMaterial", property_name="density",
    )
    await _seed_request(
        db_session, ov=ov, entity_type="Isotope", property_name="half_life",
    )

    resp = await async_client.get(
        f"{BASE}/requests",
        params={"entity_type": "Isotope"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["entity_type"] == "Isotope"


@pytest.mark.asyncio
async def test_list_requests_invalid_status_returns_422(async_client, db_session) -> None:
    """Invalid status query param returns 422."""
    resp = await async_client.get(
        f"{BASE}/requests",
        params={"status": "bogus"},
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /data-collection/requests/{id} -- detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_detail(async_client, db_session) -> None:
    """Returns a single request by ID."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, property_name="density")

    resp = await async_client.get(f"{BASE}/requests/{req.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(req.id)
    assert data["entity_type"] == "NuclearMaterial"
    assert data["property"] == "density"
    assert data["material_system"] == "UO2"
    assert data["status"] == "open"
    assert data["urgency"] == 0
    assert data["completed_at"] is None


@pytest.mark.asyncio
async def test_get_request_not_found(async_client, db_session) -> None:
    """Returns 404 for non-existent request ID."""
    resp = await async_client.get(f"{BASE}/requests/{uuid.uuid4()}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. PATCH /data-collection/requests/{id}/status -- status transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_transition_open_to_in_progress(async_client, db_session) -> None:
    """Valid transition: open -> in_progress."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, status="open")

    resp = await async_client.patch(
        f"{BASE}/requests/{req.id}/status",
        json={"status": "in_progress"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    assert data["completed_at"] is None


@pytest.mark.asyncio
async def test_status_transition_in_progress_to_completed(async_client, db_session) -> None:
    """Valid transition: in_progress -> completed sets completed_at."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, status="in_progress")

    resp = await async_client.patch(
        f"{BASE}/requests/{req.id}/status",
        json={"status": "completed"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["completed_at"] is not None


@pytest.mark.asyncio
async def test_status_transition_open_to_declined(async_client, db_session) -> None:
    """Valid transition: open -> declined sets completed_at."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, status="open")

    resp = await async_client.patch(
        f"{BASE}/requests/{req.id}/status",
        json={"status": "declined"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "declined"
    assert data["completed_at"] is not None


@pytest.mark.asyncio
async def test_status_transition_invalid_target(async_client, db_session) -> None:
    """Invalid target status returns 422."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, status="open")

    resp = await async_client.patch(
        f"{BASE}/requests/{req.id}/status",
        json={"status": "bogus"},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_status_transition_invalid_from_completed(async_client, db_session) -> None:
    """Cannot transition from completed (terminal state)."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, status="completed")

    resp = await async_client.patch(
        f"{BASE}/requests/{req.id}/status",
        json={"status": "open"},
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Cannot transition" in detail


@pytest.mark.asyncio
async def test_status_transition_not_found(async_client, db_session) -> None:
    """Returns 404 for non-existent request."""
    resp = await async_client.patch(
        f"{BASE}/requests/{uuid.uuid4()}/status",
        json={"status": "in_progress"},
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. GET /data-collection/coverage/{ontology_version_id} -- coverage metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_coverage_metrics(async_client, db_session) -> None:
    """Returns coverage metrics for an ontology version."""
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
    await _seed_request(db_session, ov=ov, property_name="density", status="open")
    await _seed_request(
        db_session, ov=ov, property_name="thermal_conductivity", status="completed",
    )

    resp = await async_client.get(f"{BASE}/coverage/{ov.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ontology_version_id"] == str(ov.id)
    assert data["coverage_rate"] == 0.0  # No DB property records
    assert data["total_requests"] == 2
    assert data["open_requests"] == 1
    assert data["completed_requests"] == 1
    assert data["in_progress_requests"] == 0
    assert data["declined_requests"] == 0
    assert "computed_at" in data


@pytest.mark.asyncio
async def test_get_coverage_not_found(async_client, db_session) -> None:
    """Returns 404 for non-existent ontology version."""
    resp = await async_client.get(f"{BASE}/coverage/{uuid.uuid4()}")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. POST /data-collection/scan -- trigger scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_scan(async_client, db_session) -> None:
    """Triggers a coverage scan and returns result summary."""
    entity_types = [
        {
            "name": "NuclearMaterial",
            "properties": ["density", "melting_point"],
        },
    ]
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data(entity_types),
    )

    resp = await async_client.post(
        f"{BASE}/scan",
        json={
            "ontology_version_id": str(ov.id),
            "material_system": "UO2",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ontology_version_id"] == str(ov.id)
    assert data["metrics"]["total_expected"] == 2
    assert data["metrics"]["covered"] == 0
    assert data["metrics"]["uncovered"] == 2
    assert data["metrics"]["coverage_rate"] == 0.0
    assert data["requests_created"] == 2
    assert data["scan_duration_ms"] >= 0
    assert len(data["uncovered_properties"]) == 2


@pytest.mark.asyncio
async def test_trigger_scan_not_found(async_client, db_session) -> None:
    """Returns 404 for non-existent ontology version."""
    resp = await async_client.post(
        f"{BASE}/scan",
        json={
            "ontology_version_id": str(uuid.uuid4()),
            "material_system": "UO2",
        },
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_trigger_scan_idempotent(async_client, db_session) -> None:
    """Second scan does not create duplicate requests."""
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

    # First scan
    resp1 = await async_client.post(
        f"{BASE}/scan",
        json={
            "ontology_version_id": str(ov.id),
            "material_system": "UO2",
        },
    )
    assert resp1.status_code == 200
    assert resp1.json()["requests_created"] == 1

    # Second scan (same material_system)
    resp2 = await async_client.post(
        f"{BASE}/scan",
        json={
            "ontology_version_id": str(ov.id),
            "material_system": "UO2",
        },
    )
    assert resp2.status_code == 200
    assert resp2.json()["requests_created"] == 0
