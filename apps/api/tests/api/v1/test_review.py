"""Integration tests for /api/v1/review endpoints (Phase 3).

5 review endpoints across extraction_results, kg_nodes, kg_edges,
and property_measurements, with state machine transition validation.

Tests use ExtractionResult (no FKs to external tables in SQLite),
which provides sufficient code-path coverage for the cross-table union logic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nfm_db.api.v1.review import _row_to_review_item
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
        value=3.5,
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


@pytest.mark.asyncio
async def test_update_review_status_invalid_transition(async_client, db_session) -> None:
    """Cannot transition approved → rejected (409 Conflict)."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.APPROVED.value,
    )

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "rejected"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_review_status_corrected_from_needs_revision(
    async_client, db_session,
) -> None:
    """Needs_revision -> corrected is a valid transition."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.NEEDS_REVISION.value,
    )

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "corrected", "note": "Fixed value"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["review_status"] == "corrected"


@pytest.mark.asyncio
async def test_reset_approved_to_pending(async_client, db_session) -> None:
    """Approved item can be reset back to pending."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.APPROVED.value,
    )
    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "pending", "note": "Reset for re-review"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["review_status"] == "pending"


@pytest.mark.asyncio
async def test_reset_rejected_to_pending(async_client, db_session) -> None:
    """Rejected item can be reset back to pending."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.REJECTED.value,
    )
    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "pending"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_pending_to_pending_rejected(async_client, db_session) -> None:
    """Cannot transition pending → pending (no-op)."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.PENDING.value,
    )
    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "pending"},
    )
    assert response.status_code == 409


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
    """Stats aggregate counts across all statuses."""
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.PENDING.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.APPROVED.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.REJECTED.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.NEEDS_REVISION.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.CORRECTED.value,
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
    assert data["corrected"] == 1


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
    assert data["corrected"] == 0


@pytest.mark.asyncio
async def test_review_stats_reflects_changes(async_client, db_session) -> None:
    """Stats update after PATCH status change."""
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.PENDING.value,
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


# ---------------------------------------------------------------------------
# Feedback loop: corrected transitions write audit records + metrics endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrected_writes_audit_record(async_client, db_session) -> None:
    """needs_revision → corrected writes a Review audit record with loop_time_seconds."""
    from sqlalchemy import select

    from nfm_db.models.review import Review

    # Seed item in needs_revision with a prior reviewed_at so loop_time is non-null.
    er = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.NEEDS_REVISION.value,
        reviewed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    response = await async_client.patch(
        f"/api/v1/review/{er.id}",
        json={"status": "corrected", "note": "Fixed value"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["review_status"] == "corrected"

    # Verify exactly one audit record exists for this item.
    stmt = select(Review).where(Review.result_id == er.id)
    result = await db_session.execute(stmt)
    audits = result.scalars().all()
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == ReviewStatus.CORRECTED.value
    assert audit.comment == "Fixed value"
    assert audit.data["previous_status"] == ReviewStatus.NEEDS_REVISION.value
    assert audit.data["table"] == "extraction_results"
    assert audit.data["loop_time_seconds"] is not None
    assert audit.data["loop_time_seconds"] > 0


@pytest.mark.asyncio
async def test_feedback_metrics_endpoint(async_client, db_session) -> None:
    """GET /review/feedback-metrics aggregates loop_time from audit records."""
    from nfm_db.models.review import Review

    # Seed two corrected audit records directly.
    db_session.add(
        Review(
            result_id=uuid.uuid4(),
            reviewer_id="test-reviewer",
            action=ReviewStatus.CORRECTED.value,
            comment="",
            data={"loop_time_seconds": 3600.0},  # 1 hour
        )
    )
    db_session.add(
        Review(
            result_id=uuid.uuid4(),
            reviewer_id="test-reviewer",
            action=ReviewStatus.CORRECTED.value,
            comment="",
            data={"loop_time_seconds": 7200.0},  # 2 hours
        )
    )
    await db_session.commit()

    response = await async_client.get("/api/v1/review/feedback-metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total_corrections"] == 2
    # avg of 1h and 2h = 1.5h
    assert data["avg_loop_time_hours"] == pytest.approx(1.5, rel=1e-6)
    assert data["max_loop_time_hours"] == pytest.approx(2.0, rel=1e-6)


@pytest.mark.asyncio
async def test_batch_corrected_writes_audit(async_client, db_session) -> None:
    """Batch review with corrected status writes an audit record per item."""
    from sqlalchemy import select

    from nfm_db.models.review import Review

    er1 = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.NEEDS_REVISION.value,
        reviewed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    er2 = await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.NEEDS_REVISION.value,
        reviewed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    response = await async_client.post(
        "/api/v1/review/batch",
        json={
            "items": [
                {"id": str(er1.id), "status": "corrected", "note": "fix1"},
                {"id": str(er2.id), "status": "corrected", "note": "fix2"},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["succeeded"] == 2
    assert data["failed"] == 0

    # Two audit records should now exist.
    stmt = select(Review).where(Review.action == ReviewStatus.CORRECTED.value)
    result = await db_session.execute(stmt)
    audits = result.scalars().all()
    assert len(audits) == 2
    result_ids = {a.result_id for a in audits}
    assert er1.id in result_ids
    assert er2.id in result_ids
    for a in audits:
        assert a.data["loop_time_seconds"] is not None
        assert a.data["loop_time_seconds"] > 0


@pytest.mark.asyncio
async def test_stats_includes_adoption_rate(async_client, db_session) -> None:
    """Adoption rate = corrected / (corrected + rejected)."""
    # 3 corrected, 1 rejected → adoption_rate = 0.75
    for _ in range(3):
        await _seed_extraction_result(
            db_session,
            review_status=ReviewStatus.CORRECTED.value,
        )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.REJECTED.value,
    )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.APPROVED.value,
    )

    response = await async_client.get("/api/v1/review/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["corrected"] == 3
    assert data["rejected"] == 1
    assert data["approved"] == 1
    # total_reviewed = approved + rejected + needs_revision + corrected = 5
    assert data["total_reviewed"] == 5
    # adoption_rate = 3 / (3 + 1) = 0.75
    assert data["adoption_rate"] is not None
    assert abs(data["adoption_rate"] - 0.75) < 1e-9


@pytest.mark.asyncio
async def test_stats_adoption_rate_zero_division(async_client) -> None:
    """No reviewed items → adoption_rate should be None (not crash)."""
    response = await async_client.get("/api/v1/review/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["corrected"] == 0
    assert data["rejected"] == 0
    assert data["adoption_rate"] is None
    assert data["total_reviewed"] == 0


@pytest.mark.asyncio
async def test_stats_by_type_breakdown(async_client, db_session) -> None:
    """Per-type breakdown includes adoption rate for extraction items."""
    # Seed only ExtractionResult rows (other tables have FK constraints in SQLite).
    # 2 corrected + 1 rejected in extraction → adoption_rate = 2/3
    for _ in range(2):
        await _seed_extraction_result(
            db_session,
            review_status=ReviewStatus.CORRECTED.value,
        )
    await _seed_extraction_result(
        db_session,
        review_status=ReviewStatus.REJECTED.value,
    )

    response = await async_client.get("/api/v1/review/stats")
    assert response.status_code == 200
    data = response.json()["data"]
    by_type = data["by_type"]
    assert "extraction" in by_type
    ext = by_type["extraction"]
    assert ext["corrected"] == 2
    assert ext["rejected"] == 1
    assert ext["approved"] == 0
    assert ext["total"] == 3
    assert ext["adoption_rate"] is not None
    assert abs(ext["adoption_rate"] - (2 / 3)) < 1e-9
    # All four type labels should be present
    for label in ("extraction", "node", "edge", "measurement"):
        assert label in by_type


# ---------------------------------------------------------------------------
# NFM-4560 — _row_to_review_item unit_symbols branch
# ---------------------------------------------------------------------------
#
# PropertyMeasurement has FK references to tables that don't exist in
# SQLite (property_types.id, units.id, datasets.id, sources.id), so the
# integration tests above use ExtractionResult. The unit_symbol JOIN
# lives in the property_measurements branch of ``_row_to_review_item``
# only, so we unit-test it with a duck-typed row instead of seeding a
# real DB. This keeps the test fast and SQLite-friendly while still
# exercising the mapping contract the API endpoint depends on.


def _measurement_row(
    *,
    row_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None,
    unit_id: uuid.UUID | None,
    value_scalar: float | None = 0.34,
    dedupe_key: str | None = None,
    notes: str | None = None,
) -> SimpleNamespace:
    """Build a duck-typed row that mimics PropertyMeasurement's surface
    that ``_row_to_review_item`` reads (property_type_id, unit_id,
    value_scalar, dedupe_key, notes, id, created_at, review_status)."""
    return SimpleNamespace(
        id=row_id or uuid.uuid4(),
        property_type_id=property_type_id,
        unit_id=unit_id,
        value_scalar=value_scalar,
        dedupe_key=dedupe_key,
        notes=notes,
        # _row_to_review_item uses getattr for these — set them so the
        # response envelope is fully populated.
        confidence=0.5,
        review_status="pending",
        created_at=datetime(2026, 9, 10, tzinfo=UTC),
    )


def test_row_to_review_item_resolves_unit_symbol_when_present() -> None:
    """NFM-4560 — backend mirror of the round-2 property_type_name fix.

    When ``unit_symbols`` is supplied and the row has a unit_id, the
    mapper writes ``unit_symbol`` into item_data so the UI can render
    "W/(m·K)" instead of a row UUID prefix.
    """
    unit_id = uuid.uuid4()
    pt_id = uuid.uuid4()
    row = _measurement_row(property_type_id=pt_id, unit_id=unit_id)
    item = _row_to_review_item(
        row,
        "property_measurements",
        property_type_names={pt_id: "thermal conductivity"},
        unit_symbols={unit_id: "W/(m·K)"},
    )
    assert item.item_data["unit_symbol"] == "W/(m·K)"
    assert item.item_data["unit_id"] == str(unit_id)
    assert item.item_data["property_type_name"] == "thermal conductivity"


def test_row_to_review_item_unit_symbol_null_when_dict_empty() -> None:
    """No symbol resolved → item_data["unit_symbol"] is None (legacy
    fallback handled client-side, mirrors property_type_name)."""
    unit_id = uuid.uuid4()
    row = _measurement_row(property_type_id=uuid.uuid4(), unit_id=unit_id)
    item = _row_to_review_item(
        row,
        "property_measurements",
        unit_symbols={},  # unit was deleted / not resolvable
    )
    assert item.item_data["unit_symbol"] is None
    # unit_id is still surfaced so the client can fall back to a short
    # id prefix.
    assert item.item_data["unit_id"] == str(unit_id)


def test_row_to_review_item_unit_symbol_null_when_row_has_no_unit() -> None:
    """Row with unit_id=None → no symbol, no unit_id, client shows '—'."""
    row = _measurement_row(property_type_id=uuid.uuid4(), unit_id=None)
    item = _row_to_review_item(
        row,
        "property_measurements",
        unit_symbols={},
    )
    assert item.item_data["unit_symbol"] is None
    assert item.item_data["unit_id"] is None


def test_row_to_review_item_unit_symbols_param_is_optional() -> None:
    """unit_symbols=None must not crash (mirrors property_type_names)."""
    row = _measurement_row(property_type_id=uuid.uuid4(), unit_id=uuid.uuid4())
    item = _row_to_review_item(row, "property_measurements")
    assert item.item_data["unit_symbol"] is None


def test_row_to_review_item_resolves_only_matching_unit_id() -> None:
    """The mapper looks up by the row's specific unit_id — a different
    row with a different unit_id must not bleed its symbol into this
    row's item_data."""
    this_unit = uuid.uuid4()
    other_unit = uuid.uuid4()
    row = _measurement_row(property_type_id=uuid.uuid4(), unit_id=this_unit)
    item = _row_to_review_item(
        row,
        "property_measurements",
        unit_symbols={this_unit: "K", other_unit: "eV"},
    )
    assert item.item_data["unit_symbol"] == "K"
