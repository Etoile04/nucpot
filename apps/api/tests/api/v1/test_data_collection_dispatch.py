"""API endpoint tests for the 3 dispatch endpoints added by NFM-2651.

Endpoints under test:
1. POST /api/v1/data-collection/dispatch                 — batch dispatch
2. GET  /api/v1/data-collection/dispatch/status         — paginated dispatch status
3. POST /api/v1/data-collection/dispatch/{request_id}/retry — retry a failed dispatch

The ``GapDispatchService`` is patched in these tests to avoid hitting
Celery / network dependencies.  We feed it canned ``DispatchResult``
values so we can exercise the endpoint contracts without depending on
the dispatch internals (which have their own test suite).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from nfm_db.models import DataCollectionRequest, OntologyVersion
from nfm_db.services.gap_dispatch_service import DispatchResult

BASE = "/api/v1/data-collection"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_USER_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")


def _make_ontology_data() -> dict:
    """Minimal ontology payload — empty entity_types is fine."""
    return {"entity_types": [], "relation_types": []}


async def _seed_version(
    session, *, version: str = "1.0.0",
) -> OntologyVersion:
    """Create and flush a minimal OntologyVersion."""
    ov = OntologyVersion(
        version=version,
        status="published",
        created_by=_SEED_USER_ID,
        ontology_data=_make_ontology_data(),
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
    urgency: int = 0,
    metadata_: dict[str, Any] | None = None,
) -> DataCollectionRequest:
    """Create and flush a DataCollectionRequest."""
    req = DataCollectionRequest(
        ontology_version_id=ov.id,
        entity_type=entity_type,
        property=property_name,
        material_system=material_system,
        status=status,
        urgency=urgency,
        metadata_=metadata_,
    )
    session.add(req)
    await session.flush()
    await session.refresh(req)
    return req


def _fake_dispatch_result(
    request_id: uuid.UUID,
    *,
    path: str = "literature",
    status: str = "dispatched",
    detail: str = "queued via celery",
) -> DispatchResult:
    """Build a canned DispatchResult for patching."""
    return DispatchResult(
        request_id=request_id,
        path_taken=path,
        status=status,
        detail=detail,
        metadata={"task_id": "fake-task-id"},
    )


# ---------------------------------------------------------------------------
# 1. POST /data-collection/dispatch — batch dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_dispatch_no_open_requests(
    async_client, db_session,
) -> None:
    """Returns empty list when no open requests exist."""
    await _seed_version(db_session)

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        resp = await async_client.post(f"{BASE}/dispatch")

    assert resp.status_code == 200
    assert resp.json() == []
    mock_svc_cls.assert_called_once()
    mock_svc_cls.return_value.dispatch_request.assert_not_called()


@pytest.mark.asyncio
async def test_batch_dispatch_with_open_requests(
    async_client, db_session,
) -> None:
    """Dispatches each open request and returns a result for each."""
    ov = await _seed_version(db_session)
    r1 = await _seed_request(
        db_session, ov=ov, property_name="density", urgency=5,
    )
    r2 = await _seed_request(
        db_session, ov=ov, property_name="melting_point", urgency=3,
    )

    async def _dispatch(req_id: uuid.UUID) -> DispatchResult:
        for cand in (r1, r2):
            if cand.id == req_id:
                return _fake_dispatch_result(
                    cand.id, path="dft",
                    detail=f"dft calc for {cand.property}",
                )
        raise AssertionError(f"unexpected request_id {req_id}")

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        mock_svc_cls.return_value.dispatch_request.side_effect = _dispatch

        resp = await async_client.post(f"{BASE}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {item["request_id"] for item in data} == {str(r1.id), str(r2.id)}
    for item in data:
        assert item["status"] == "dispatched"
        assert item["path_taken"] == "dft"
        assert item["detail"]


@pytest.mark.asyncio
async def test_batch_dispatch_respects_limit(
    async_client, db_session,
) -> None:
    """Honours the ``limit`` query parameter."""
    ov = await _seed_version(db_session)
    seeded = [
        await _seed_request(
            db_session, ov=ov, property_name=f"prop_{i}", urgency=i,
        )
        for i in range(5)
    ]

    dispatched: list[uuid.UUID] = []

    async def _dispatch(req_id: uuid.UUID) -> DispatchResult:
        dispatched.append(req_id)
        return _fake_dispatch_result(req_id)

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        mock_svc_cls.return_value.dispatch_request.side_effect = _dispatch

        resp = await async_client.post(f"{BASE}/dispatch", params={"limit": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert len(dispatched) == 2
    # urgency desc, created_at asc → first 2 selected are urgency=4, urgency=3.
    assert {item["request_id"] for item in data} == {
        str(seeded[4].id), str(seeded[3].id),
    }


@pytest.mark.asyncio
async def test_batch_dispatch_filter_by_ontology_version(
    async_client, db_session,
) -> None:
    """Filters candidates by ``ontology_version_id``."""
    ov1 = await _seed_version(db_session, version="1.0.0")
    ov2 = await _seed_version(db_session, version="2.0.0")

    # Seed two requests per ontology version.
    r1_ov1 = await _seed_request(
        db_session, ov=ov1, property_name="p1_ov1", urgency=5,
    )
    r2_ov1 = await _seed_request(
        db_session, ov=ov1, property_name="p2_ov1", urgency=1,
    )
    await _seed_request(db_session, ov=ov2, property_name="p1_ov2", urgency=5)
    await _seed_request(db_session, ov=ov2, property_name="p2_ov2", urgency=1)

    dispatched: list[uuid.UUID] = []

    async def _dispatch(req_id: uuid.UUID) -> DispatchResult:
        dispatched.append(req_id)
        return _fake_dispatch_result(req_id)

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        mock_svc_cls.return_value.dispatch_request.side_effect = _dispatch

        resp = await async_client.post(
            f"{BASE}/dispatch",
            params={"ontology_version_id": str(ov1.id), "limit": 10},
        )

    assert resp.status_code == 200
    data = resp.json()
    # Only ov1's open requests were dispatched: r1_ov1 (urgency=5) and
    # r2_ov1 (urgency=1).  Both ov2 requests must have been filtered
    # out by the ontology_version_id query parameter.
    assert {item["request_id"] for item in data} == {
        str(r1_ov1.id), str(r2_ov1.id),
    }
    assert r1_ov1.id in dispatched
    assert r2_ov1.id in dispatched


@pytest.mark.asyncio
async def test_batch_dispatch_captures_per_request_failure(
    async_client, db_session,
) -> None:
    """A failing request is recorded as 'failed' but does not abort the batch."""
    ov = await _seed_version(db_session)
    r1 = await _seed_request(db_session, ov=ov, property_name="density")
    r2 = await _seed_request(
        db_session, ov=ov, property_name="melting_point",
    )

    async def _dispatch(req_id: uuid.UUID) -> DispatchResult:
        if req_id == r1.id:
            raise ValueError("Simulated dispatch failure")
        return _fake_dispatch_result(req_id)

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        mock_svc_cls.return_value.dispatch_request.side_effect = _dispatch

        resp = await async_client.post(f"{BASE}/dispatch")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    by_id = {item["request_id"]: item for item in data}

    assert by_id[str(r1.id)]["status"] == "failed"
    assert "Simulated dispatch failure" in by_id[str(r1.id)]["detail"]

    assert by_id[str(r2.id)]["status"] == "dispatched"


@pytest.mark.asyncio
async def test_batch_dispatch_invalid_limit_returns_422(
    async_client, db_session,
) -> None:
    """Limit out of range returns 422."""
    resp = await async_client.post(
        f"{BASE}/dispatch", params={"limit": 0},
    )
    assert resp.status_code == 422

    resp = await async_client.post(
        f"{BASE}/dispatch", params={"limit": 9999},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. GET /data-collection/dispatch/status — paginated dispatch status
# ---------------------------------------------------------------------------


def _dispatch_metadata(
    *,
    dispatched_at: datetime,
    path: str = "literature",
    dispatch_status: str = "dispatched",
    task_id: str | None = None,
    dft_calculation_id: str | None = None,
) -> dict[str, Any]:
    """Build a ``metadata_`` dict carrying a populated 'dispatch' sub-key."""
    dispatch: dict[str, Any] = {
        "path_taken": path,
        "dispatch_status": dispatch_status,
        "dispatched_at": dispatched_at.isoformat(),
        "detail": "seeded",
    }
    if task_id is not None:
        dispatch["task_id"] = task_id
    if dft_calculation_id is not None:
        dispatch["dft_calculation_id"] = dft_calculation_id
    return {"dispatch": dispatch}


@pytest.mark.asyncio
async def test_list_dispatch_status_empty(
    async_client, db_session,
) -> None:
    """Returns empty list when no dispatched requests exist."""
    ov = await _seed_version(db_session)
    await _seed_request(db_session, ov=ov, property_name="density")

    resp = await async_client.get(f"{BASE}/dispatch/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_dispatch_status_returns_dispatched_only(
    async_client, db_session,
) -> None:
    """Filters out requests that were never dispatched."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    dispatched_a = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(dispatched_at=ts, path="literature"),
    )
    dispatched_b = await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        metadata_=_dispatch_metadata(dispatched_at=ts, path="dft"),
    )
    undispatched = await _seed_request(
        db_session,
        ov=ov,
        property_name="thermal_conductivity",
        metadata_={"note": "no dispatch sub-key"},
    )

    resp = await async_client.get(f"{BASE}/dispatch/status")

    assert resp.status_code == 200
    data = resp.json()
    ids = {item["id"] for item in data["items"]}
    assert ids == {str(dispatched_a.id), str(dispatched_b.id)}
    assert str(undispatched.id) not in ids
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_list_dispatch_status_filter_by_dispatch_status(
    async_client, db_session,
) -> None:
    """Filters by ``dispatch_status`` query param."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    failed = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="dft", dispatch_status="failed",
        ),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="literature", dispatch_status="dispatched",
        ),
    )

    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"dispatch_status": "failed"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(failed.id)
    assert data["items"][0]["dispatch_status"] == "failed"


@pytest.mark.asyncio
async def test_list_dispatch_status_filter_by_dispatched_path(
    async_client, db_session,
) -> None:
    """Filters by ``dispatched_path`` query param."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    dft = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(dispatched_at=ts, path="dft"),
    )
    await _seed_request(
        db_session,
        ov=ov,
        property_name="melting_point",
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="literature",
        ),
    )

    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"dispatched_path": "dft"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(dft.id)
    assert data["items"][0]["dispatched_path"] == "dft"


@pytest.mark.asyncio
async def test_list_dispatch_status_populates_derived_fields(
    async_client, db_session,
) -> None:
    """The new dispatched_at/dispatched_path/dispatch_status/result_reference
    fields are populated from the metadata dispatch sub-key."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(
            dispatched_at=ts,
            path="dft",
            dispatch_status="dispatched",
            dft_calculation_id="calc-abc-123",
        ),
    )

    resp = await async_client.get(f"{BASE}/dispatch/status")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == str(req.id)
    assert item["dispatched_path"] == "dft"
    assert item["dispatch_status"] == "dispatched"
    assert item["dispatched_at"].startswith("2026-06-15T12:00:00")
    assert item["result_reference"] == "calc-abc-123"


@pytest.mark.asyncio
async def test_list_dispatch_status_pagination(
    async_client, db_session,
) -> None:
    """Pagination metadata reflects the FULL filtered set across all pages.

    Regression test for the filter-after-pagination bug: total/pages must
    describe the complete filtered result set (not the per-page slice).
    With 5 dispatched requests and per_page=2 we expect total=5, pages=3.
    """
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await _seed_request(
            db_session,
            ov=ov,
            property_name=f"prop_{i}",
            metadata_=_dispatch_metadata(dispatched_at=ts),
        )

    # Page 1 of size 2 — only 2 rows come back, but total/pages must
    # describe the full 5-row filtered set.
    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"page": 1, "per_page": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["limit"] == 2
    assert data["total"] == 5
    assert data["pages"] == 3  # ceil(5 / 2)
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_dispatch_status_pagination_walks_all_pages(
    async_client, db_session,
) -> None:
    """Walking page 1 → page 2 → page 3 yields every dispatched row exactly once.

    Regression test for the filter-after-pagination bug: each page must
    be derived from a filter applied BEFORE offset/limit, so consecutive
    pages do not overlap and no row is dropped.
    """
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    seeded_ids: list[uuid.UUID] = []
    for i in range(5):
        req = await _seed_request(
            db_session,
            ov=ov,
            property_name=f"prop_{i}",
            metadata_=_dispatch_metadata(dispatched_at=ts),
        )
        seeded_ids.append(req.id)

    seen: list[str] = []
    for page in (1, 2, 3):
        resp = await async_client.get(
            f"{BASE}/dispatch/status",
            params={"page": page, "per_page": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == page
        assert data["total"] == 5
        assert data["pages"] == 3
        seen.extend(item["id"] for item in data["items"])

    assert sorted(seen) == sorted(str(i) for i in seeded_ids)
    assert len(seen) == len(set(seen))  # no duplicates across pages


@pytest.mark.asyncio
async def test_list_dispatch_status_filter_combined_with_pagination(
    async_client, db_session,
) -> None:
    """Filters AND pagination compose: total counts the filtered set."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    # 3 dispatched requests with path='dft'.
    dft_ids: list[uuid.UUID] = []
    for i in range(3):
        req = await _seed_request(
            db_session,
            ov=ov,
            property_name=f"dft_{i}",
            metadata_=_dispatch_metadata(dispatched_at=ts, path="dft"),
        )
        dft_ids.append(req.id)

    # 4 dispatched requests with path='literature'.
    lit_ids: list[uuid.UUID] = []
    for i in range(4):
        req = await _seed_request(
            db_session,
            ov=ov,
            property_name=f"lit_{i}",
            metadata_=_dispatch_metadata(
                dispatched_at=ts, path="literature",
            ),
        )
        lit_ids.append(req.id)

    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"dispatched_path": "dft", "page": 1, "per_page": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    # total counts the 3 dft rows, not 7 (global) or 2 (per-page slice).
    assert data["total"] == 3
    assert data["pages"] == 2  # ceil(3 / 2)
    assert len(data["items"]) == 2
    # Page 1 must contain only dft rows (no leakage from the literature set).
    dft_id_strs = {str(i) for i in dft_ids}
    page1_ids = {item["id"] for item in data["items"]}
    assert page1_ids <= dft_id_strs
    assert page1_ids.isdisjoint({str(i) for i in lit_ids})

    # Page 2 returns the remaining dft row (not a literature row).
    resp2 = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"dispatched_path": "dft", "page": 2, "per_page": 2},
    )
    data2 = resp2.json()
    assert data2["total"] == 3
    assert len(data2["items"]) == 1
    # Walking all pages must cover the entire dft set with no duplicates.
    page2_ids = {item["id"] for item in data2["items"]}
    assert page2_ids <= dft_id_strs
    assert page2_ids.isdisjoint({str(i) for i in lit_ids})
    union = page1_ids | page2_ids
    assert union == dft_id_strs  # every dft id appears exactly once


@pytest.mark.asyncio
async def test_list_dispatch_status_filter_excludes_undispatched_from_count(
    async_client, db_session,
) -> None:
    """Total excludes undispatched rows; they are not 'filtered out later'."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)

    # 2 dispatched + 5 undispatched = 7 total rows in the table.
    for i in range(2):
        await _seed_request(
            db_session,
            ov=ov,
            property_name=f"dispatched_{i}",
            metadata_=_dispatch_metadata(dispatched_at=ts),
        )
    for i in range(5):
        await _seed_request(
            db_session,
            ov=ov,
            property_name=f"undispatched_{i}",
            metadata_={"note": "no dispatch sub-key"},
        )

    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        params={"page": 1, "per_page": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    # Only the 2 dispatched rows are part of the filtered universe.
    assert data["total"] == 2
    assert data["pages"] == 1
    assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# 3. POST /data-collection/dispatch/{request_id}/retry — retry failed dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_dispatch_success(
    async_client, db_session,
) -> None:
    """Retries a previously failed dispatch and returns the new state."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        status="open",  # Retry resets to open before re-dispatching.
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="dft", dispatch_status="failed",
        ),
    )

    async def _dispatch(req_id: uuid.UUID) -> DispatchResult:
        # Simulate the metadata mutation that the real service performs.
        # Without this, the response would have dispatch_status=None.
        existing_meta = dict(req.metadata_ or {})
        existing_meta["dispatch"] = {
            "path_taken": "literature",
            "dispatch_status": "dispatched",
            "detail": "retry succeeded",
            "dispatched_at": datetime.now(UTC).isoformat(),
            "task_id": "retry-task-id",
        }
        req.metadata_ = existing_meta
        req.status = "in_progress"
        await db_session.flush()
        assert req_id == req.id
        return _fake_dispatch_result(
            req_id, path="literature", status="dispatched",
            detail="retry succeeded",
        )

    with patch(
        "nfm_db.api.v1.data_collection.GapDispatchService",
    ) as mock_svc_cls:
        mock_svc_cls.return_value.dispatch_request.side_effect = _dispatch

        resp = await async_client.post(f"{BASE}/dispatch/{req.id}/retry")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(req.id)
    assert data["dispatch_status"] == "dispatched"
    assert data["dispatched_path"] == "literature"


@pytest.mark.asyncio
async def test_retry_dispatch_not_found(
    async_client, db_session,
) -> None:
    """Returns 404 for non-existent request."""
    resp = await async_client.post(
        f"{BASE}/dispatch/{uuid.uuid4()}/retry",
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_dispatch_no_prior_dispatch(
    async_client, db_session,
) -> None:
    """Returns 422 when the request has no prior dispatch record."""
    ov = await _seed_version(db_session)
    req = await _seed_request(
        db_session, ov=ov, property_name="density",
    )

    resp = await async_client.post(f"{BASE}/dispatch/{req.id}/retry")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "no prior dispatch" in detail.lower()


@pytest.mark.asyncio
async def test_retry_dispatch_not_failed(
    async_client, db_session,
) -> None:
    """Returns 422 when prior dispatch_status is not 'failed'."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="literature", dispatch_status="dispatched",
        ),
    )

    resp = await async_client.post(f"{BASE}/dispatch/{req.id}/retry")

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "failed" in detail.lower()


@pytest.mark.asyncio
async def test_retry_dispatch_pending_is_not_eligible(
    async_client, db_session,
) -> None:
    """Prior status 'pending' is not eligible for retry either."""
    ov = await _seed_version(db_session)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    req = await _seed_request(
        db_session,
        ov=ov,
        property_name="density",
        metadata_=_dispatch_metadata(
            dispatched_at=ts, path="literature", dispatch_status="pending",
        ),
    )

    resp = await async_client.post(f"{BASE}/dispatch/{req.id}/retry")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AuthZ — all 3 endpoints require domain_expert role
# ---------------------------------------------------------------------------


def _bearer_headers(user_id: uuid.UUID) -> dict[str, str]:
    """Build a Bearer token header for the given user."""
    from nfm_db.services.auth_service import create_access_token
    return {
        "Authorization": f"Bearer {create_access_token({'sub': str(user_id)})}",
    }


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_batch_dispatch_requires_domain_expert(
    async_client, db_session, editor_user,
) -> None:
    """Non-domain_expert users are rejected with 403."""
    resp = await async_client.post(
        f"{BASE}/dispatch",
        headers=_bearer_headers(editor_user.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_list_dispatch_status_requires_domain_expert(
    async_client, db_session, editor_user,
) -> None:
    """Non-domain_expert users are rejected with 403."""
    resp = await async_client.get(
        f"{BASE}/dispatch/status",
        headers=_bearer_headers(editor_user.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.no_auto_auth
async def test_retry_dispatch_requires_domain_expert(
    async_client, db_session, editor_user,
) -> None:
    """Non-domain_expert users are rejected with 403."""
    resp = await async_client.post(
        f"{BASE}/dispatch/{uuid.uuid4()}/retry",
        headers=_bearer_headers(editor_user.id),
    )
    assert resp.status_code == 403
