"""Integration tests for /api/v1/review endpoints (Phase 2).

ADR-NFM-796 §4: 5 review endpoints across extraction_results,
kg_nodes, kg_edges, and property_measurements.
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models.extraction_result import ExtractionResult
from nfm_db.models.review import ReviewStatus

# NOTE: KGNode/KGEdge/PropertyMeasurement have FK references to tables that
# don't exist in SQLite (e.g. kg_nodes.source_id → "sources.id"). Since the
# review router uses identical logic across all 4 tables, ExtractionResult
# provides sufficient code-path coverage for the SQLite test layer.


# ---------------------------------------------------------------------------
# Factory helpers — each test creates its own data, no cross-test dependencies
# ---------------------------------------------------------------------------


async def _seed_extraction_result(db_session, **overrides):
    """Create an ExtractionResult row with sensible defaults."""
    defaults = dict(
        item_type="property",
        item_data={"property": "thermal_conductivity", "value": 3.5},
        source_paragraph="The thermal conductivity of UO2 at 1000K.",
        source_page=42,
        source_doi="10.1234/test.doi",
        confidence=0.95,
        review_status=ReviewStatus.PENDING.value,
    )
    defaults.update(overrides)
    obj = ExtractionResult(**defaults)
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj



# ---------------------------------------------------------------------------
# R1: GET /api/v1/review/pending — paginated pending items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_reviews_empty(async_client) -> None:
    """No data in DB → 200 with empty items list."""
    response = await async_client.get("/api/v1/review/pending")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_pending_reviews_returns_items(async_client, db_session) -> None:
    """Multiple pending extraction results are returned."""
    er1 = await _seed_extraction_result(db_session, item_type="property")
    er2 = await _seed_extraction_result(db_session, item_type="entity")

    response = await async_client.get("/api/v1/review/pending")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2
    item_ids = {item["id"] for item in data["items"]}
    assert str(er1.id) in item_ids
    assert str(er2.id) in item_ids


@pytest.mark.asyncio
async def test_pending_reviews_item_type_filter(async_client, db_session) -> None:
    """Filtering by item_type=extraction returns only extraction_results."""
    await _seed_extraction_result(db_session, item_type="property")

    response = await async_client.get("/api/v1/review/pending?item_type=extraction")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["item_type"] == "extraction"


@pytest.mark.asyncio
async def test_pending_reviews_invalid_item_type(async_client) -> None:
    """Invalid item_type filter → 400."""
    response = await async_client.get("/api/v1/review/pending?item_type=invalid_type")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_pending_reviews_pagination(async_client, db_session) -> None:
    """Pagination parameters work correctly."""
    for i in range(5):
        await _seed_extraction_result(
            db_session,
            item_data={"index": i},
        )

    response = await async_client.get("/api/v1/review/pending?page=1&limit=2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_pending_reviews_excludes_non_pending(async_client, db_session) -> None:
    """Items with non-pending review_status are excluded."""
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.APPROVED.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.PENDING.value,
    )

    response = await async_client.get("/api/v1/review/pending")
    data = response.json()["data"]
    assert data["total"] == 1


# ---------------------------------------------------------------------------
# R2: GET /api/v1/review/{id}/source — source provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_source_found(async_client, db_session) -> None:
    """ExtractionResult with source fields returns provenance data."""
    er = await _seed_extraction_result(
        db_session,
        source_paragraph="Test paragraph text",
        source_page=10,
        source_doi="10.1234/test",
    )

    response = await async_client.get(f"/api/v1/review/{er.id}/source")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["paragraph"] == "Test paragraph text"
    assert data["page"] == 10
    assert data["doi"] == "10.1234/test"


@pytest.mark.asyncio
async def test_review_source_not_found(async_client) -> None:
    """Non-existent ID → 404."""
    response = await async_client.get(f"/api/v1/review/{uuid.uuid4()}/source")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_review_source_no_source_fields(async_client, db_session) -> None:
    """Item without source fields returns null provenance fields."""
    er = await _seed_extraction_result(
        db_session,
        source_paragraph=None,
        source_page=None,
        source_doi=None,
    )

    response = await async_client.get(f"/api/v1/review/{er.id}/source")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["paragraph"] is None
    assert data["page"] is None
    assert data["doi"] is None


# ---------------------------------------------------------------------------
# R3: PATCH /api/v1/review/{id} — status update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_review_status_approved(async_client, db_session) -> None:
    """Happy path: approve a pending item."""
    er = await _seed_extraction_result(db_session)

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "approved", "note": "Looks correct"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["review_status"] == "approved"


@pytest.mark.asyncio
async def test_update_review_status_not_found(async_client) -> None:
    """Non-existent ID → 404."""
    response = await async_client.patch(
        f"/api/v1/review/{uuid.uuid4()}",
        json={"status": "approved"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_review_status_invalid(async_client, db_session) -> None:
    """Invalid status string → 400."""
    er = await _seed_extraction_result(db_session)

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "totally_invalid"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_review_status_needs_revision(async_client, db_session) -> None:
    """Set status to needs_revision with a note."""
    er = await _seed_extraction_result(db_session)

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "needs_revision", "note": "Re-check the value"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["review_status"] == "needs_revision"


# ---------------------------------------------------------------------------
# R4: POST /api/v1/review/batch — batch operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_review_all_succeed(async_client, db_session) -> None:
    """Batch approve multiple items successfully."""
    er1 = await _seed_extraction_result(db_session)
    er2 = await _seed_extraction_result(db_session)

    response = await async_client.post(
        "/api/v1/review/batch",
        json={
            "items": [
                {"id": str(er1.id), "status": "approved"},
                {"id": str(er2.id), "status": "approved"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["succeeded"] == 2
    assert data["failed"] == 0
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_batch_review_mixed_results(async_client, db_session) -> None:
    """Batch with one valid item and one not-found item."""
    er = await _seed_extraction_result(db_session)
    fake_id = str(uuid.uuid4())

    response = await async_client.post(
        "/api/v1/review/batch",
        json={
            "items": [
                {"id": str(er.id), "status": "approved"},
                {"id": fake_id, "status": "rejected"},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["succeeded"] == 1
    assert data["failed"] == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["id"] == fake_id


@pytest.mark.asyncio
async def test_batch_review_empty_items(async_client) -> None:
    """Empty items list → 422 validation error."""
    response = await async_client.post(
        "/api/v1/review/batch",
        json={"items": []},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_batch_review_invalid_status(async_client, db_session) -> None:
    """Batch with invalid status counts as failed."""
    er = await _seed_extraction_result(db_session)

    response = await async_client.post(
        "/api/v1/review/batch",
        json={
            "items": [
                {"id": str(er.id), "status": "bad_status"},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["succeeded"] == 0
    assert data["failed"] == 1


# ---------------------------------------------------------------------------
# R5: GET /api/v1/review/stats — review statistics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_stats_with_data(async_client, db_session) -> None:
    """Stats aggregate counts across statuses."""
    await _seed_extraction_result(
        db_session, review_status=ReviewStatus.PENDING.value,
    )
    await _seed_extraction_result(
        db_session, review_status=ReviewStatus.APPROVED.value,
    )
    await _seed_extraction_result(
        db_session, review_status=ReviewStatus.REJECTED.value,
    )
    await _seed_extraction_result(
        db_session, review_status=ReviewStatus.NEEDS_REVISION.value,
    )

    response = await async_client.get("/api/v1/review/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["pending"] == 1
    assert data["approved"] == 1
    assert data["rejected"] == 1
    assert data["needs_revision"] == 1


@pytest.mark.asyncio
async def test_review_stats_empty(async_client) -> None:
    """Empty DB → all counts are 0."""
    response = await async_client.get("/api/v1/review/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pending"] == 0
    assert data["approved"] == 0
    assert data["rejected"] == 0
    assert data["needs_revision"] == 0


@pytest.mark.asyncio
async def test_review_stats_reflects_changes(async_client, db_session) -> None:
    """Stats update after PATCH status change."""
    er = await _seed_extraction_result(
        db_session, review_status=ReviewStatus.PENDING.value,
    )

    # Verify initial state
    response = await async_client.get("/api/v1/review/stats")
    data = response.json()["data"]
    assert data["pending"] == 1
    assert data["approved"] == 0

    # Approve the item
    await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "approved"},
    )

    # Verify updated state
    response = await async_client.get("/api/v1/review/stats")
    data = response.json()["data"]
    assert data["pending"] == 0
    assert data["approved"] == 1
