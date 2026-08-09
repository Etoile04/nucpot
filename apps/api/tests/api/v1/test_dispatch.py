"""Integration tests for the dispatch endpoint (NFM-2678).

Exercises ``POST /api/v1/data-collection/requests/{id}/dispatch`` across
all three collection paths (literature | dft | external_db) plus the
``any`` cascade and error paths.

The dispatch service lazily imports Celery and the external-data-source
HTTP client, so we monkeypatch the source modules (never the function
local name) for hermetic tests.

Acceptance criteria (NFM-2678):
- Alembic migration 048 exists and is valid  -- covered by migration file
- All dispatch integration tests pass     -- this module
- Tests cover at least 2 of the 3 dispatch paths -- covers all 3 plus 'any'
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from nfm_db.models import DataCollectionRequest, DFTCalculation, OntologyVersion

BASE = "/api/v1/data-collection"

_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed helpers (mirrored from test_data_collection.py to keep dispatch tests
# self-contained and avoid crossing private helpers across files).
# ---------------------------------------------------------------------------


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
) -> DataCollectionRequest:
    """Create and flush a DataCollectionRequest with an explicit source_preference."""
    req = DataCollectionRequest(
        ontology_version_id=ov.id,
        entity_type=entity_type,
        property=property_name,
        material_system=material_system,
        status=status,
        source_preference=source_preference,
    )
    session.add(req)
    await session.flush()
    await session.refresh(req)
    return req


# ---------------------------------------------------------------------------
# External-source fake client (replaces httpx-calling ExternalDataSourceClient)
# ---------------------------------------------------------------------------


class _FakeAsyncResult:
    """Stand-in for ``celery.AsyncResult`` -- only the ``.id`` attribute is read."""

    def __init__(self, task_id: str) -> None:
        self.id = task_id


class _FakeExternalClient:
    """Minimal async stand-in for ``ExternalDataSourceClient``.

    Each ``query_*`` returns the payload the test fixed in advance; ``close``
    is a no-op so the service's ``finally`` block is exercised harmlessly.
    """

    def __init__(
        self,
        *,
        nist: dict[str, Any] | None = None,
        openkim: dict[str, Any] | None = None,
        mp: dict[str, Any] | None = None,
    ) -> None:
        self._nist = nist
        self._openkim = openkim
        self._mp = mp
        self.closed = False

    async def query_nist_ipr(
        self,
        *,
        formula: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        return self._nist

    async def query_openkim(
        self,
        *,
        species: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        return self._openkim

    async def query_materials_project(
        self,
        *,
        formula: str,
        property_name: str | None = None,
    ) -> dict[str, Any] | None:
        return self._mp

    async def close(self) -> None:
        self.closed = True


def _patch_external_db(
    monkeypatch: pytest.MonkeyPatch,
    *,
    nist: dict[str, Any] | None = None,
    openkim: dict[str, Any] | None = None,
    mp: dict[str, Any] | None = None,
) -> None:
    """Replace ``ExternalDataSourceClient`` in its source module.

    The dispatch service does ``from nfm_db.services.external_data_sources
    import ExternalDataSourceClient`` inside the function, so the patch
    must target the source module's binding, not the function-local name.
    """
    payloads = {"nist": nist, "openkim": openkim, "mp": mp}

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeExternalClient:
        return _FakeExternalClient(**payloads)

    monkeypatch.setattr(
        "nfm_db.services.external_data_sources.ExternalDataSourceClient",
        _factory,
    )


def _patch_celery_send_task(
    monkeypatch: pytest.MonkeyPatch,
    *,
    task_id: str = "fake-gap-task-id",
    side_effect: type[BaseException] | None = None,
) -> None:
    """Replace ``celery_app.send_task`` so no broker is required.

    With ``side_effect`` set the patched ``send_task`` raises that exception,
    which is how the cascade test simulates literature path failure.
    """

    def _send(*_args: Any, **_kwargs: Any) -> _FakeAsyncResult:
        if side_effect is not None:
            raise side_effect
        return _FakeAsyncResult(task_id)

    monkeypatch.setattr(
        "nfm_db.services.celery_app.celery_app.send_task",
        _send,
    )


# ---------------------------------------------------------------------------
# 6. POST /data-collection/requests/{id}/dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_dft_preference_creates_pending_calculation(
    async_client,
    db_session,
) -> None:
    """dft preference creates a pending DFTCalculation row and transitions the request."""
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, source_preference="dft")

    resp = await async_client.post(f"{BASE}/requests/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    dispatch_meta = data["metadata_"]["dispatch"]
    assert dispatch_meta["path_taken"] == "dft"
    assert dispatch_meta["dispatch_status"] == "dispatched"
    assert "DFTCalculation" in dispatch_meta["detail"]

    # The dispatched event must have created a DFTCalculation row linked
    # back to this request via calculation_id == "gap-{req.id}".
    result = await db_session.execute(
        select(DFTCalculation).where(
            DFTCalculation.calculation_id == f"gap-{req.id}",
        ),
    )
    calc = result.scalar_one()
    assert calc.status == "pending"
    assert calc.source == "gap_dispatch"
    assert calc.functional == "PBE"
    meta = calc.computation_metadata or {}
    assert str(meta.get("data_collection_request_id")) == str(req.id)


@pytest.mark.asyncio
async def test_dispatch_external_db_preference_records_source_results(
    async_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """external_db preference records which sources returned data."""
    _patch_external_db(
        monkeypatch,
        nist={"source": "nist_ipr", "values": [10.5]},
        openkim={"source": "openkim", "potentials": []},
    )
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, source_preference="external_db")

    resp = await async_client.post(f"{BASE}/requests/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    dispatch_meta = data["metadata_"]["dispatch"]
    assert dispatch_meta["path_taken"] == "external_db"
    assert dispatch_meta["dispatch_status"] == "dispatched"
    # Two mock sources returned data => "Queried 3 external sources, 2 returned data"
    assert "2 returned" in dispatch_meta["detail"]
    # Results are echoed on the dispatched event for later processing.
    external_results = dispatch_meta["external_results"]
    assert external_results["nist_ipr"]["source"] == "nist_ipr"
    assert external_results["openkim"]["source"] == "openkim"


@pytest.mark.asyncio
async def test_dispatch_literature_preference_mocks_celery_send_task(
    async_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """literature preference schedules a Celery task and records the task id."""
    _patch_celery_send_task(monkeypatch, task_id="literature-task-abc")
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, source_preference="literature")

    resp = await async_client.post(f"{BASE}/requests/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    dispatch_meta = data["metadata_"]["dispatch"]
    assert dispatch_meta["path_taken"] == "literature"
    assert dispatch_meta["dispatch_status"] == "dispatched"
    assert dispatch_meta["task_id"] == "literature-task-abc"


@pytest.mark.asyncio
async def test_dispatch_any_preference_falls_through_to_external_db(
    async_client,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """any preference tries paths in priority order and uses the first success."""
    # Make literature fail (simulated broker outage) so the cascade moves to
    # external_db, demonstrating the priority ordering (literature -> external_db).
    _patch_celery_send_task(
        monkeypatch,
        side_effect=RuntimeError("simulated broker outage"),
    )
    _patch_external_db(
        monkeypatch,
        nist={"source": "nist_ipr", "values": [42.0]},
    )
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, source_preference="any")

    resp = await async_client.post(f"{BASE}/requests/{req.id}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    # Literature raised and was skipped; external_db succeeded.
    # The returned path_taken identifies which path landed.
    dispatch_meta = data["metadata_"]["dispatch"]
    assert dispatch_meta["path_taken"] == "external_db"
    assert dispatch_meta["dispatch_status"] == "dispatched"


@pytest.mark.asyncio
async def test_dispatch_not_found_returns_404(
    async_client,
    db_session,
) -> None:
    """Dispatching a non-existent request returns 404."""
    missing_id = uuid.uuid4()
    resp = await async_client.post(f"{BASE}/requests/{missing_id}/dispatch")

    assert resp.status_code == 404
    assert str(missing_id) in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dispatch_already_in_progress_returns_404(
    async_client,
    db_session,
) -> None:
    """Dispatching a request whose status is not 'open' surfaces a 404.

    The endpoint maps every ``ValueError`` from the service to a 404 (NFM-2621
    decision: misuse is reported as 'not dispatchable from this state' rather
    than a separate 409). The detail string identifies the actual status so
    operators can act on it.
    """
    ov = await _seed_version(db_session, ontology_data=_make_ontology_data([]))
    req = await _seed_request(db_session, ov=ov, status="in_progress")

    resp = await async_client.post(f"{BASE}/requests/{req.id}/dispatch")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "'in_progress'" in detail
    assert "expected 'open'" in detail
