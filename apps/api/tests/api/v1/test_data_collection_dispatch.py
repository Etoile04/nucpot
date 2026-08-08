"""API endpoint tests for dispatch routes (NFM-2651, NFM-2662).

Tests all 4 dispatch endpoints:
1. POST /api/v1/data-collection/dispatch -- trigger batch dispatch (NFM-2651)
2. GET  /api/v1/data-collection/dispatch/status -- paginated dispatch status (NFM-2651)
3. POST /api/v1/data-collection/dispatch/{id}/retry -- retry failed dispatch (NFM-2651)
4. POST /api/v1/data-collection/{id}/dispatch -- per-request dispatch (NFM-2662)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from nfm_db.models import DataCollectionRequest, OntologyVersion
from nfm_db.services.gap_dispatch_service import DispatchResult

BASE = "/api/v1/data-collection/dispatch"
DC_BASE = "/api/v1/data-collection"

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
    source_preference: str = "any",
    dispatch_status: str | None = None,
    dispatched_path: str | None = None,
    dispatched_at: datetime | None = None,
) -> DataCollectionRequest:
    """Create and flush a DataCollectionRequest."""
    req = DataCollectionRequest(
        ontology_version_id=ov.id,
        entity_type=entity_type,
        property=property_name,
        material_system=material_system,
        status=status,
        source_preference=source_preference,
        dispatch_status=dispatch_status,
        dispatched_path=dispatched_path,
        dispatched_at=dispatched_at,
    )
    session.add(req)
    await session.flush()
    await session.refresh(req)
    return req


def _mock_dispatch_result(
    *,
    success: bool = True,
    path: str = "external_db",
    reference: str | None = None,
    error: str | None = None,
    data_found: bool = True,
) -> DispatchResult:
    """Create a mock DispatchResult."""
    return DispatchResult(
        success=success,
        path=path,
        reference=reference,
        error=error,
        data_found=data_found,
    )


# ---------------------------------------------------------------------------
# 1. POST /data-collection/dispatch -- trigger batch dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_scoped_empty(async_client, db_session) -> None:
    """Returns empty results when no open DCRs exist for the ontology version."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )

    resp = await async_client.post(
        f"{BASE}",
        params={"ontology_version_id": str(ov.id), "limit": 5},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatched_count"] == 0
    assert data["results"] == []


@pytest.mark.asyncio
async def test_dispatch_scoped_with_results(
    async_client, db_session, monkeypatch,
) -> None:
    """Dispatches open DCRs and returns results."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(db_session, ov=ov, property_name="density")

    mock_result = _mock_dispatch_result(success=True, path="external_db")
    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(return_value=mock_result),
    )

    resp = await async_client.post(
        f"{BASE}",
        params={"ontology_version_id": str(ov.id), "limit": 10},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatched_count"] == 1
    assert data["results"][0]["success"] is True
    assert data["results"][0]["path"] == "external_db"


@pytest.mark.asyncio
async def test_dispatch_default_limit(async_client, db_session, monkeypatch) -> None:
    """Uses default limit when not specified."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )

    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(return_value=_mock_dispatch_result()),
    )

    resp = await async_client.post(
        f"{BASE}",
        params={"ontology_version_id": str(ov.id)},
    )

    assert resp.status_code == 200




# ---------------------------------------------------------------------------
# 2. GET /data-collection/dispatch/status -- paginated dispatch status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_status_empty(async_client, db_session) -> None:
    """Returns empty list when no dispatched DCRs exist."""
    resp = await async_client.get(f"{BASE}/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["pages"] == 0


@pytest.mark.asyncio
async def test_dispatch_status_with_data(async_client, db_session) -> None:
    """Returns dispatched DCRs with dispatch fields."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        dispatch_status="success",
        dispatched_path="external_db",
        dispatched_at=datetime.now(UTC),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        dispatch_status="failed",
        dispatched_path="literature",
        dispatched_at=datetime.now(UTC),
    )

    resp = await async_client.get(f"{BASE}/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    item = data["items"][0]
    assert "dispatch_status" in item
    assert "dispatched_path" in item
    assert "dispatched_at" in item
    assert "result_reference" in item


@pytest.mark.asyncio
async def test_dispatch_status_filter_by_status(async_client, db_session) -> None:
    """Filter by dispatch_status query param."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        dispatch_status="success",
        dispatched_path="external_db",
        dispatched_at=datetime.now(UTC),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        dispatch_status="failed",
        dispatched_path="literature",
        dispatched_at=datetime.now(UTC),
    )

    resp = await async_client.get(
        f"{BASE}/status",
        params={"dispatch_status": "failed"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["dispatch_status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_status_filter_by_path(async_client, db_session) -> None:
    """Filter by dispatched_path query param."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        dispatch_status="success",
        dispatched_path="external_db",
        dispatched_at=datetime.now(UTC),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        dispatch_status="success",
        dispatched_path="literature",
        dispatched_at=datetime.now(UTC),
    )

    resp = await async_client.get(
        f"{BASE}/status",
        params={"dispatched_path": "external_db"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["dispatched_path"] == "external_db"


@pytest.mark.asyncio
async def test_dispatch_status_pagination(async_client, db_session) -> None:
    """Pagination works correctly for dispatch status endpoint."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )

    for i in range(5):
        await _seed_request(
            db_session,
            ov=ov,
            property_name=f"prop_{i}",
            dispatch_status="success",
            dispatched_path="external_db",
            dispatched_at=datetime.now(UTC),
        )

    resp = await async_client.get(
        f"{BASE}/status",
        params={"page": 1, "per_page": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["pages"] == 3


# ---------------------------------------------------------------------------
# 3. POST /data-collection/dispatch/{id}/retry -- retry failed dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_failed_dispatch(async_client, db_session, monkeypatch) -> None:
    """Retries a failed dispatch and returns updated DCR."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        status="in_progress",
        dispatch_status="failed",
        dispatched_path="literature",
        dispatched_at=datetime.now(UTC),
    )

    mock_result = _mock_dispatch_result(success=True, path="external_db")
    mock_dispatch = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        mock_dispatch,
    )

    resp = await async_client.post(f"{BASE}/{req.id}/retry")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(req.id)


@pytest.mark.asyncio
async def test_retry_not_found(async_client, db_session) -> None:
    """Returns 404 for non-existent request ID."""
    resp = await async_client.post(f"{BASE}/{uuid.uuid4()}/retry")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_non_failed_rejected(async_client, db_session) -> None:
    """Returns 422 when dispatch_status is not 'failed'."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        dispatch_status="success",
        dispatched_path="external_db",
        dispatched_at=datetime.now(UTC),
    )

    resp = await async_client.post(f"{BASE}/{req.id}/retry")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "expected 'failed'" in detail



# ---------------------------------------------------------------------------
# Dispatch failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_captures_per_request_error(
    async_client, db_session, monkeypatch,
) -> None:
    """A dispatch exception is captured per-request instead of failing the batch."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(db_session, ov=ov, property_name="density")

    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(side_effect=RuntimeError("provider exploded")),
    )

    resp = await async_client.post(
        f"{BASE}",
        params={"ontology_version_id": str(ov.id), "limit": 10},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["dispatched_count"] == 1
    item = data["results"][0]
    assert item["request_id"] == str(req.id)
    assert item["success"] is False
    assert item["path"] == "error"
    assert item["data_found"] is False
    assert "provider exploded" in item["error"]


@pytest.mark.asyncio
async def test_retry_dispatch_failure_returns_500(
    async_client, db_session, monkeypatch,
) -> None:
    """A dispatch exception during retry surfaces as a 500."""
    ov = await _seed_version(
        db_session,
        ontology_data=_make_ontology_data([]),
    )
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        status="in_progress",
        dispatch_status="failed",
        dispatched_path="literature",
        dispatched_at=datetime.now(UTC),
    )

    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(side_effect=RuntimeError("retry exploded")),
    )

    resp = await async_client.post(f"{BASE}/{req.id}/retry")

    assert resp.status_code == 500
    assert "retry exploded" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. POST /data-collection/{id}/dispatch -- per-request dispatch (NFM-2662)
# ---------------------------------------------------------------------------

_RESPONSE_KEYS = {
    "dispatched_at",
    "dispatched_path",
    "dispatch_status",
    "result_reference",
}


@pytest.mark.asyncio
async def test_dispatch_one_wires_to_service_and_persists(
    async_client, db_session,
) -> None:
    """Real service run: endpoint persists dispatch columns and returns them.

    No fill paths are registered, so the router resolves no handler for
    ``source_preference='literature'`` and records a failed dispatch. That is
    still a complete round-trip through GapDispatchService, so it proves the
    endpoint is wired to the service rather than fabricating a response.
    """
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        source_preference="literature",
    )

    resp = await async_client.post(f"{DC_BASE}/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == _RESPONSE_KEYS
    assert data["dispatched_at"] is not None
    assert data["dispatched_path"] == "literature"
    assert data["dispatch_status"] == "failed"

    await db_session.refresh(req)
    assert req.dispatched_at is not None
    assert req.dispatched_path == "literature"
    assert req.dispatch_status == "failed"


@pytest.mark.asyncio
async def test_dispatch_one_success_returns_persisted_columns(
    async_client, db_session, monkeypatch,
) -> None:
    """A successful dispatch returns the DCR's persisted dispatch columns."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, property_name="density")

    async def _fake_dispatch(dcr: DataCollectionRequest) -> DispatchResult:
        dcr.dispatched_at = datetime.now(UTC)
        dcr.dispatched_path = "external_db"
        dcr.dispatch_status = "success"
        dcr.result_reference = "matproj:mp-1234"
        return _mock_dispatch_result(
            success=True,
            path="external_db",
            reference="matproj:mp-1234",
        )

    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(side_effect=_fake_dispatch),
    )

    resp = await async_client.post(f"{DC_BASE}/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == _RESPONSE_KEYS
    assert data["dispatch_status"] == "success"
    assert data["dispatched_path"] == "external_db"
    assert data["result_reference"] == "matproj:mp-1234"


@pytest.mark.asyncio
async def test_dispatch_one_not_found(async_client, db_session) -> None:
    """Returns 404 for a non-existent request ID."""
    resp = await async_client.post(f"{DC_BASE}/{uuid.uuid4()}/dispatch")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_one_already_dispatched_conflicts(
    async_client, db_session,
) -> None:
    """Returns 409 when dispatched_at is already set (idempotency rule)."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        dispatch_status="success",
        dispatched_path="external_db",
        dispatched_at=datetime.now(UTC),
    )

    resp = await async_client.post(f"{DC_BASE}/{req.id}/dispatch")

    assert resp.status_code == 409
    assert "already dispatched" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dispatch_one_applies_source_preference_override(
    async_client, db_session,
) -> None:
    """An override replaces source_preference before routing, and persists."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        source_preference="literature",
    )

    resp = await async_client.post(
        f"{DC_BASE}/{req.id}/dispatch",
        json={"source_preference_override": "dft"},
    )

    assert resp.status_code == 200
    assert resp.json()["dispatched_path"] == "dft"

    await db_session.refresh(req)
    assert req.source_preference == "dft"


@pytest.mark.asyncio
async def test_dispatch_one_rejects_invalid_override(
    async_client, db_session,
) -> None:
    """An unknown source_preference_override is rejected without dispatching."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, property_name="density")

    resp = await async_client.post(
        f"{DC_BASE}/{req.id}/dispatch",
        json={"source_preference_override": "carrier_pigeon"},
    )

    assert resp.status_code == 422

    await db_session.refresh(req)
    assert req.dispatched_at is None


@pytest.mark.asyncio
async def test_dispatch_one_service_failure_returns_500(
    async_client, db_session, monkeypatch,
) -> None:
    """A dispatch exception surfaces as a 500 rather than a 200."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, property_name="density")

    monkeypatch.setattr(
        "nfm_db.api.v1.data_collection.GapDispatchService.dispatch",
        AsyncMock(side_effect=RuntimeError("dispatch exploded")),
    )

    resp = await async_client.post(f"{DC_BASE}/{req.id}/dispatch")

    assert resp.status_code == 500
    assert "dispatch exploded" in resp.json()["detail"]

