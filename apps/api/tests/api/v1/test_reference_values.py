"""Integration tests for /api/v1/reference-values endpoints.

Tests all 6 routes:
- POST /api/v1/reference-values/bulk              — bulk staging (201)
- GET  /api/v1/reference-values/pending-review   — review queue
- POST /api/v1/reference-values/{id}/approve      — approve staging entry
- POST /api/v1/reference-values/{id}/reject       — reject staging entry
- POST /api/v1/reference-values/export            — export for verification
- POST /api/v1/reference-values/verify-callback   — verification callback
"""

from __future__ import annotations

import uuid

import pytest

from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_staging(
    db_session,
    *,
    element_system="U",
    phase="BCC",
    property_name="lattice_constant",
    value=3.47,
    unit="angstrom",
    source="test-source",
    confidence=Confidence.MEDIUM,
    status=StagingStatus.PENDING,
    dedup_hash_override: str | None = None,
) -> RefGapFillStaging:
    """Insert a staging record for testing."""
    dedup = dedup_hash_override or uuid.uuid4().hex
    record = RefGapFillStaging(
        element_system=element_system,
        phase=phase,
        property_name=property_name,
        value=value,
        unit=unit,
        source=source,
        confidence=confidence,
        dedup_hash=dedup,
        range_validated=True,
        status=status,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


def _bulk_item(**overrides) -> dict:
    """Build a single bulk staging payload item."""
    defaults = {
        "element_system": "U",
        "phase": "BCC",
        "property_name": "lattice_constant",
        "value": 3.47,
        "unit": "angstrom",
        "method": "DFT",
        "source": "TestSource2024",
        "confidence": "high",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/bulk — bulk staging (201)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_stage_creates_records(async_client, db_session) -> None:
    payload = {
        "values": [
            _bulk_item(),
            _bulk_item(property_name="bulk_modulus", value=112.0, unit="GPa"),
        ],
    }
    response = await async_client.post("/api/v1/reference-values/bulk", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["accepted"] == 2
    assert data["rejected"] == 0
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_bulk_stage_response_shape(async_client) -> None:
    payload = {"values": [_bulk_item()]}
    response = await async_client.post("/api/v1/reference-values/bulk", json=payload)
    assert response.status_code == 201
    data = response.json()["data"]
    assert "accepted" in data
    assert "rejected" in data
    assert "results" in data
    result = data["results"][0]
    assert "staging_id" in result
    assert "status" in result
    assert "confidence" in result


@pytest.mark.asyncio
async def test_bulk_stage_missing_required_field(async_client) -> None:
    payload = {
        "values": [
            {"element_system": "U", "value": 3.47},
        ],
    }
    response = await async_client.post("/api/v1/reference-values/bulk", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_stage_empty_list_rejected(async_client) -> None:
    payload = {"values": []}
    response = await async_client.post("/api/v1/reference-values/bulk", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_stage_duplicate_detection(async_client, db_session) -> None:
    # First insert succeeds
    item = _bulk_item(source="DupSource1")
    resp1 = await async_client.post(
        "/api/v1/reference-values/bulk", json={"values": [item]},
    )
    assert resp1.status_code == 201
    assert resp1.json()["data"]["accepted"] == 1

    # Second insert with same dedup key should be flagged duplicate
    resp2 = await async_client.post(
        "/api/v1/reference-values/bulk", json={"values": [item]},
    )
    assert resp2.status_code == 201
    data2 = resp2.json()["data"]
    assert data2["accepted"] == 0
    assert data2["rejected"] == 1


@pytest.mark.asyncio
async def test_bulk_stage_high_confidence_auto_approved(async_client) -> None:
    item = _bulk_item(confidence="high", source="UniqueHigh1")
    response = await async_client.post(
        "/api/v1/reference-values/bulk", json={"values": [item]},
    )
    assert response.status_code == 201
    result = response.json()["data"]["results"][0]
    assert result["status"] == "auto_approved"


@pytest.mark.asyncio
async def test_bulk_stage_medium_confidence_pending_review(async_client) -> None:
    item = _bulk_item(confidence="medium", source="UniqueMed1")
    response = await async_client.post(
        "/api/v1/reference-values/bulk", json={"values": [item]},
    )
    assert response.status_code == 201
    result = response.json()["data"]["results"][0]
    assert result["status"] == "pending_review"


# ---------------------------------------------------------------------------
# GET /api/v1/reference-values/pending-review — review queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_review_empty(async_client) -> None:
    response = await async_client.get("/api/v1/reference-values/pending-review")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["records"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_pending_review_returns_pending_only(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.PENDING)
    await _seed_staging(
        db_session, property_name="bulk_modulus", status=StagingStatus.APPROVED,
        dedup_hash_override="b" * 64,
    )
    await _seed_staging(
        db_session, property_name="thermal_conductivity", status=StagingStatus.REJECTED,
        dedup_hash_override="c" * 64,
    )

    response = await async_client.get("/api/v1/reference-values/pending-review")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_pending_review_pagination(async_client, db_session) -> None:
    for i in range(5):
        await _seed_staging(
            db_session,
            property_name=f"prop_{i}",
            dedup_hash_override=f"d{i}" * 16,
        )

    response = await async_client.get("/api/v1/reference-values/pending-review?per_page=2&page=2")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 5
    assert data["page"] == 2
    assert len(data["records"]) <= 2


@pytest.mark.asyncio
async def test_pending_review_filter_element_system(async_client, db_session) -> None:
    await _seed_staging(db_session, element_system="U")
    await _seed_staging(
        db_session, element_system="Zr", dedup_hash_override="e" * 64,
    )

    response = await async_client.get(
        "/api/v1/reference-values/pending-review?element_system=Zr",
    )
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["element_system"] == "Zr"


@pytest.mark.asyncio
async def test_pending_review_filter_status_all(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.PENDING)
    await _seed_staging(
        db_session, status=StagingStatus.APPROVED, dedup_hash_override="f" * 64,
    )

    response = await async_client.get("/api/v1/reference-values/pending-review?status=all")
    data = response.json()["data"]
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_pending_review_filter_status_approved(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.PENDING)
    await _seed_staging(
        db_session, status=StagingStatus.APPROVED, dedup_hash_override="g" * 64,
    )

    response = await async_client.get(
        "/api/v1/reference-values/pending-review?status=approved",
    )
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["status"] == "approved"


@pytest.mark.asyncio
async def test_pending_review_filter_confidence(async_client, db_session) -> None:
    await _seed_staging(db_session, confidence=Confidence.HIGH)
    await _seed_staging(
        db_session, confidence=Confidence.LOW, dedup_hash_override="h" * 64,
    )

    response = await async_client.get(
        "/api/v1/reference-values/pending-review?confidence=high",
    )
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["confidence"] == "high"


@pytest.mark.asyncio
async def test_pending_review_invalid_status(async_client) -> None:
    response = await async_client.get(
        "/api/v1/reference-values/pending-review?status=invalid_status",
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pending_review_record_shape(async_client, db_session) -> None:
    await _seed_staging(db_session)
    response = await async_client.get("/api/v1/reference-values/pending-review")
    data = response.json()["data"]["records"][0]
    assert "id" in data
    assert "element_system" in data
    assert "property_name" in data
    assert "value" in data
    assert "status" in data
    assert "confidence" in data
    assert "created_at" in data


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/approve — approve staging entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_pending_record(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PENDING)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/approve",
        json={"review_note": "Looks good"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "promoted"
    assert data["review_note"] == "Looks good"
    assert data["staging_id"] == str(record.id)


@pytest.mark.asyncio
async def test_approve_without_body(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PENDING)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/approve",
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "promoted"


@pytest.mark.asyncio
async def test_approve_nonexistent_record(async_client) -> None:
    response = await async_client.post(
        f"/api/v1/reference-values/{uuid.uuid4()}/approve",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_already_rejected_conflict(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.REJECTED)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/approve",
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_approve_already_promoted_conflict(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PROMOTED)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/approve",
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_approve_invalid_uuid(async_client) -> None:
    response = await async_client.post(
        "/api/v1/reference-values/not-a-uuid/approve",
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/reject — reject staging entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_pending_record(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PENDING)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/reject",
        json={"review_note": "Bad data"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "rejected"
    assert data["review_note"] == "Bad data"
    assert data["staging_id"] == str(record.id)


@pytest.mark.asyncio
async def test_reject_without_body(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PENDING)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/reject",
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_reject_nonexistent_record(async_client) -> None:
    response = await async_client.post(
        f"/api/v1/reference-values/{uuid.uuid4()}/reject",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reject_already_promoted_conflict(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PROMOTED)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/reject",
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_reject_already_rejected_conflict(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.REJECTED)
    response = await async_client.post(
        f"/api/v1/reference-values/{record.id}/reject",
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/export — export for verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_empty(async_client) -> None:
    payload = {"filters": {}, "limit": 10, "offset": 0}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["records"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_export_response_shape(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.APPROVED)

    payload = {"filters": {}, "limit": 10, "offset": 0}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    data = response.json()["data"]
    assert "records" in data
    assert "total" in data
    assert "offset" in data
    assert "limit" in data
    assert data["offset"] == 0
    assert data["limit"] == 10


@pytest.mark.asyncio
async def test_export_returns_approved_and_promoted(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.APPROVED)
    await _seed_staging(
        db_session, status=StagingStatus.PROMOTED, dedup_hash_override="i" * 64,
    )
    await _seed_staging(
        db_session, status=StagingStatus.PENDING, dedup_hash_override="j" * 64,
    )

    payload = {"filters": {}, "limit": 100}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    data = response.json()["data"]
    assert data["total"] == 2
    statuses = {r["status"] for r in data["records"]}
    assert "approved" in statuses
    assert "promoted" in statuses


@pytest.mark.asyncio
async def test_export_filter_by_element_system(async_client, db_session) -> None:
    await _seed_staging(
        db_session, status=StagingStatus.APPROVED, element_system="U",
    )
    await _seed_staging(
        db_session, status=StagingStatus.APPROVED, element_system="Zr",
        dedup_hash_override="k" * 64,
    )

    payload = {"filters": {"element_system": "U"}, "limit": 100}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["element_system"] == "U"


@pytest.mark.asyncio
async def test_export_filter_by_status(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.APPROVED)
    await _seed_staging(
        db_session, status=StagingStatus.PROMOTED, dedup_hash_override="l" * 64,
    )

    payload = {"filters": {"status": "approved"}, "limit": 100}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["records"][0]["status"] == "approved"


@pytest.mark.asyncio
async def test_export_pagination(async_client, db_session) -> None:
    for i in range(5):
        await _seed_staging(
            db_session,
            status=StagingStatus.APPROVED,
            property_name=f"export_prop_{i}",
            dedup_hash_override=f"m{i}" * 16,
        )

    payload = {"filters": {}, "limit": 2, "offset": 0}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    data = response.json()["data"]
    assert data["total"] == 5
    assert len(data["records"]) == 2


@pytest.mark.asyncio
async def test_export_record_fields(async_client, db_session) -> None:
    await _seed_staging(db_session, status=StagingStatus.APPROVED)

    payload = {"filters": {}, "limit": 10}
    response = await async_client.post("/api/v1/reference-values/export", json=payload)
    record = response.json()["data"]["records"][0]
    assert "id" in record
    assert "element_system" in record
    assert "property_name" in record
    assert "value" in record
    assert "confidence" in record
    assert "created_at" in record


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/verify-callback — verification callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_callback_updates_records(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.APPROVED)

    payload = {
        "results": [
            {
                "staging_id": str(record.id),
                "verdict": "A",
                "verification_note": "Confirmed by cross-reference",
            },
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["processed"] == 1
    assert data["updated"] == 1
    assert data["not_found"] == 0
    assert data["results"][0]["status"] == "updated"


@pytest.mark.asyncio
async def test_verify_callback_not_found(async_client) -> None:
    payload = {
        "results": [
            {
                "staging_id": str(uuid.uuid4()),
                "verdict": "B",
            },
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["not_found"] == 1
    assert data["updated"] == 0


@pytest.mark.asyncio
async def test_verify_callback_f_grade_auto_rejects(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.PENDING)

    payload = {
        "results": [
            {
                "staging_id": str(record.id),
                "verdict": "F",
                "verification_note": "Confirmed wrong value",
            },
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["updated"] == 1

    # Verify record was auto-rejected
    await db_session.refresh(record)
    assert record.status == StagingStatus.REJECTED
    assert "VERIFY:F" in (record.review_note or "")


@pytest.mark.asyncio
async def test_verify_callback_multiple_results(async_client, db_session) -> None:
    rec1 = await _seed_staging(db_session, status=StagingStatus.APPROVED)
    rec2 = await _seed_staging(
        db_session, status=StagingStatus.APPROVED, dedup_hash_override="n" * 64,
    )

    payload = {
        "results": [
            {"staging_id": str(rec1.id), "verdict": "A"},
            {"staging_id": str(rec2.id), "verdict": "C"},
            {"staging_id": str(uuid.uuid4()), "verdict": "B"},
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    data = response.json()["data"]
    assert data["processed"] == 3
    assert data["updated"] == 2
    assert data["not_found"] == 1


@pytest.mark.asyncio
async def test_verify_callback_response_shape(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.APPROVED)

    payload = {
        "batch_id": str(uuid.uuid4()),
        "results": [
            {"staging_id": str(record.id), "verdict": "B"},
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    data = response.json()["data"]
    assert "processed" in data
    assert "updated" in data
    assert "not_found" in data
    assert "results" in data
    assert data["results"][0]["staging_id"] == str(record.id)


@pytest.mark.asyncio
async def test_verify_callback_empty_results(async_client) -> None:
    payload = {"results": []}
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_verify_callback_invalid_verdict(async_client) -> None:
    payload = {
        "results": [
            {"staging_id": str(uuid.uuid4()), "verdict": "Z"},
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_verify_callback_with_verified_value(async_client, db_session) -> None:
    record = await _seed_staging(db_session, status=StagingStatus.APPROVED)

    payload = {
        "results": [
            {
                "staging_id": str(record.id),
                "verdict": "D",
                "verified_value": 3.50,
                "verified_uncertainty": 0.01,
                "verified_source": "Sallee1985",
                "verification_note": "Corrected from primary source",
            },
        ],
    }
    response = await async_client.post(
        "/api/v1/reference-values/verify-callback",
        json=payload,
    )
    assert response.status_code == 200
    assert response.json()["data"]["updated"] == 1

    await db_session.refresh(record)
    assert "VERIFY:D" in (record.review_note or "")
    assert "value=3.5" in (record.review_note or "")
