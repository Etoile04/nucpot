"""Integration tests for reference values API endpoints.

Tests for POST /bulk, GET /pending-review, POST /approve, POST /reject
per NFM-54 design Sections 2.2-2.3.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.ref_gap_fill import (
    Confidence,
    RefGapFillStaging,
    StagingStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


def _bulk_payload(values: list[dict] | None = None) -> dict:
    """Build a bulk staging request payload."""
    if values is None:
        values = [
            {
                "element_system": "U",
                "phase": "BCC",
                "property_name": "lattice_constant",
                "value": 2.85,
                "unit": "angstrom",
                "source": "TestSource",
                "confidence": "medium",
            },
        ]
    return {"values": values}


async def _persist_record(session: AsyncSession, **overrides) -> RefGapFillStaging:
    """Create and persist a staging record."""
    defaults = {
        "element_system": "U",
        "phase": "BCC",
        "property_name": "lattice_constant",
        "value": 2.85,
        "unit": "angstrom",
        "source": "TestSource",
        "confidence": Confidence.MEDIUM,
        "dedup_hash": "abc123",
        "range_validated": True,
        "status": StagingStatus.PENDING,
    }
    defaults.update(overrides)
    record = RefGapFillStaging(**defaults)
    session.add(record)
    await session.flush()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/bulk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_stage_single_value(db_session: AsyncSession) -> None:
    """Bulk endpoint accepts a single valid reference value and stages it."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)

    payload = _bulk_payload()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reference-values/bulk",
            json=payload,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["accepted"] >= 1
    assert isinstance(data["results"], list)

    staged = [r for r in data["results"] if r["staging_id"] is not None]
    assert len(staged) >= 1


@pytest.mark.asyncio
async def test_bulk_stage_multiple_values(db_session: AsyncSession) -> None:
    """Bulk endpoint processes multiple values correctly."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)

    payload = _bulk_payload([
        {
            "element_system": "U",
            "property_name": "lattice_constant",
            "value": 2.85,
            "unit": "angstrom",
            "source": "SourceA",
            "confidence": "medium",
        },
        {
            "element_system": "UO2",
            "property_name": "bulk_modulus",
            "value": 200.0,
            "unit": "GPa",
            "source": "SourceB",
            "confidence": "high",
        },
    ])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reference-values/bulk",
            json=payload,
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["accepted"] >= 1


@pytest.mark.asyncio
async def test_bulk_stage_empty_array_rejected(db_session: AsyncSession) -> None:
    """Bulk endpoint rejects an empty values array."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reference-values/bulk",
            json={"values": []},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_stage_duplicate_detection(db_session: AsyncSession) -> None:
    """Bulk endpoint detects duplicates across sequential calls."""
    payload = _bulk_payload()

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First call should stage
        response1 = await client.post(
            "/api/v1/reference-values/bulk",
            json=payload,
        )
        assert response1.status_code == 201

        # Second call with same value should detect duplicate
        response2 = await client.post(
            "/api/v1/reference-values/bulk",
            json=payload,
        )
        assert response2.status_code == 201

    app.dependency_overrides.clear()

    data2 = response2.json()["data"]
    dup_results = [r for r in data2["results"] if r["status"] == "duplicate"]
    assert len(dup_results) >= 1


# ---------------------------------------------------------------------------
# GET /api/v1/reference-values/pending-review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_review_empty(db_session: AsyncSession) -> None:
    """Pending review returns empty list when no records exist."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/reference-values/pending-review",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total"] == 0
    assert body["data"]["records"] == []


@pytest.mark.asyncio
async def test_pending_review_with_records(db_session: AsyncSession) -> None:
    """Pending review returns staged records with correct pagination."""
    await _persist_record(db_session)
    await _persist_record(
        db_session,
        element_system="UO2",
        property_name="bulk_modulus",
        dedup_hash="def456",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/reference-values/pending-review",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 2
    assert len(data["records"]) == 2
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_pending_review_filter_by_element_system(db_session: AsyncSession) -> None:
    """Pending review filters by element_system."""
    await _persist_record(db_session, element_system="U")
    await _persist_record(
        db_session,
        element_system="UO2",
        property_name="bulk_modulus",
        dedup_hash="def456",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/reference-values/pending-review",
            params={"element_system": "UO2"},
        )

    app.dependency_overrides.clear()

    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["records"][0]["element_system"] == "UO2"


@pytest.mark.asyncio
async def test_pending_review_excludes_non_pending(db_session: AsyncSession) -> None:
    """Pending review excludes already-promoted and rejected records."""
    await _persist_record(db_session, status=StagingStatus.PROMOTED)
    await _persist_record(
        db_session,
        status=StagingStatus.REJECTED,
        property_name="bulk_modulus",
        dedup_hash="def456",
    )

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/v1/reference-values/pending-review",
        )

    app.dependency_overrides.clear()

    body = response.json()
    assert body["data"]["total"] == 0


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_pending_record(db_session: AsyncSession) -> None:
    """Approve endpoint transitions a pending record to promoted."""
    record = await _persist_record(db_session)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/reference-values/{record.id}/approve",
            json={"review_note": "Looks correct"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "promoted"
    assert data["review_note"] == "Looks correct"
    assert data["staging_id"] == str(record.id)


@pytest.mark.asyncio
async def test_approve_without_note(db_session: AsyncSession) -> None:
    """Approve works without a review note."""
    record = await _persist_record(db_session)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/reference-values/{record.id}/approve",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "promoted"


@pytest.mark.asyncio
async def test_approve_not_found(db_session: AsyncSession) -> None:
    """Approve returns 404 for non-existent record."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reference-values/00000000-0000-0000-0000-000000000000/approve",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_approve_already_promoted_conflict(db_session: AsyncSession) -> None:
    """Approve returns 409 for a record already promoted."""
    record = await _persist_record(db_session, status=StagingStatus.PROMOTED)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/reference-values/{record.id}/approve",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/v1/reference-values/{id}/reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_pending_record(db_session: AsyncSession) -> None:
    """Reject endpoint transitions a pending record to rejected."""
    record = await _persist_record(db_session)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/reference-values/{record.id}/reject",
            json={"review_note": "Uncertain source"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "rejected"
    assert data["review_note"] == "Uncertain source"


@pytest.mark.asyncio
async def test_reject_not_found(db_session: AsyncSession) -> None:
    """Reject returns 404 for non-existent record."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/reference-values/00000000-0000-0000-0000-000000000000/reject",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reject_already_rejected_conflict(db_session: AsyncSession) -> None:
    """Reject returns 409 for a record already rejected."""
    record = await _persist_record(db_session, status=StagingStatus.REJECTED)

    app.dependency_overrides[get_db] = _override_get_db(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/reference-values/{record.id}/reject",
        )

    app.dependency_overrides.clear()

    assert response.status_code == 409
