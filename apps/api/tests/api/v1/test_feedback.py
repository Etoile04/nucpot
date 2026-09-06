"""Integration tests for /api/v1/feedback endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from nfm_db.models.feedback import Feedback, FeedbackStatus, FeedbackType, Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_feedback(db_session, **overrides) -> Feedback:
    defaults = dict(
        feedback_type=FeedbackType.BUG_REPORT,
        title="Test feedback title",
        description="Test feedback description",
        priority=Priority.MEDIUM,
        status=FeedbackStatus.OPEN,
    )
    defaults.update(overrides)
    feedback = Feedback(**defaults)
    db_session.add(feedback)
    await db_session.commit()
    await db_session.refresh(feedback)
    return feedback


# ---------------------------------------------------------------------------
# POST /api/v1/feedback — submit feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_feedback_success(async_client) -> None:
    """Submitting valid feedback returns 201 with created data."""
    payload = {
        "feedback_type": "bug_report",
        "title": "Broken search",
        "description": "The search bar returns no results on mobile",
        "page_url": "https://example.com/search",
        "contact_email": "user@example.com",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["feedback_type"] == "bug_report"
    assert data["priority"] == "medium"
    assert data["status"] == "open"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_submit_feedback_feature_request_defaults_to_low(async_client) -> None:
    """Feature requests should auto-classify as low priority."""
    payload = {
        "feedback_type": "feature_request",
        "title": "Add export CSV",
        "description": "Users want to export search results as CSV",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["priority"] == "low"


@pytest.mark.asyncio
async def test_submit_feedback_data_correction_defaults_to_high(async_client) -> None:
    """Data corrections should auto-classify as high priority."""
    payload = {
        "feedback_type": "data_correction",
        "title": "Wrong density value",
        "description": "The UO2 density is listed as 2.0 but should be 10.97",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["priority"] == "high"


@pytest.mark.asyncio
async def test_submit_feedback_bug_with_crash_keyword_escallates(async_client) -> None:
    """Bug reports with crash keyword should escalate to high priority."""
    payload = {
        "feedback_type": "bug_report",
        "title": "App crashes on load",
        "description": "The application crashes immediately when opened",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["priority"] == "high"


@pytest.mark.asyncio
async def test_submit_feedback_missing_title_rejects(async_client) -> None:
    """Missing required title field should return 422."""
    payload = {
        "feedback_type": "bug_report",
        "description": "Missing title here",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_empty_description_rejects(async_client) -> None:
    """Empty description should return 422 (min_length=1)."""
    payload = {
        "feedback_type": "feature_request",
        "title": "A feature",
        "description": "",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_invalid_type_rejects(async_client) -> None:
    """Invalid feedback_type should return 422."""
    payload = {
        "feedback_type": "not_a_real_type",
        "title": "Invalid type",
        "description": "Testing validation",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_optional_fields_can_be_null(async_client) -> None:
    """page_url and contact_email can be omitted."""
    payload = {
        "feedback_type": "usage_inquiry",
        "title": "How to search?",
        "description": "I cannot find the search feature",
    }
    response = await async_client.post("/api/v1/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_submit_feedback_anonymous_creates_row(async_client, db_session) -> None:
    """Public contract (NFM-4373): anonymous submission persists a row.

    The route documents itself as 公开端点/无需认证, the feedback table has no
    user linkage, and the FE feedback button renders for anonymous visitors.
    A stray ``get_current_active_user`` dependency 401'd anonymous submissions
    in prod while conftest auto-auth masked the behaviour in CI — this test
    drops the auto-auth override so the real dependency chain runs, and goes
    further than a 201 assertion: it verifies the row actually landed
    (NFM-4380 enum-persistence class of regressions is invisible to
    status-code-only checks on SQLite).

    Posts via the nginx ``/api`` alias mount (#1211) — the path the FE client
    uses in prod — so persistence is pinned end-to-end on the FE-facing route.
    """
    payload = {
        "feedback_type": "bug_report",
        "title": "Anonymous visitor report",
        "description": "Submitted without any session cookie",
        "contact_email": "visitor@example.com",
    }
    response = await async_client.post("/api/feedback", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["feedback_type"] == "bug_report"

    count = await db_session.scalar(select(func.count()).select_from(Feedback))
    assert count == 1
    row = (await db_session.execute(select(Feedback))).scalar_one()
    assert row.title == "Anonymous visitor report"
    assert row.contact_email == "visitor@example.com"
    assert row.status == FeedbackStatus.OPEN


# ---------------------------------------------------------------------------
# GET /api/v1/feedback — list with filters and pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_feedback_empty(async_client) -> None:
    """Empty database returns success with no items."""
    response = await async_client.get("/api/v1/feedback")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1
    assert data["pages"] == 0


@pytest.mark.asyncio
async def test_list_feedback_returns_all(async_client, db_session) -> None:
    """Seeded feedback entries appear in list response."""
    await _seed_feedback(db_session, title="First")
    await _seed_feedback(db_session, title="Second")

    response = await async_client.get("/api/v1/feedback")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_feedback_filter_by_status(async_client, db_session) -> None:
    """Filtering by status returns only matching entries."""
    await _seed_feedback(db_session, title="Open item", status=FeedbackStatus.OPEN)
    await _seed_feedback(db_session, title="Resolved item", status=FeedbackStatus.RESOLVED)

    response = await async_client.get("/api/v1/feedback", params={"status": "resolved"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Resolved item"


@pytest.mark.asyncio
async def test_list_feedback_filter_by_priority(async_client, db_session) -> None:
    """Filtering by priority returns only matching entries."""
    await _seed_feedback(db_session, title="Urgent", priority=Priority.URGENT)
    await _seed_feedback(db_session, title="Low", priority=Priority.LOW)

    response = await async_client.get("/api/v1/feedback", params={"priority": "urgent"})
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 1
    assert data["items"][0]["priority"] == "urgent"


@pytest.mark.asyncio
async def test_list_feedback_filter_by_type(async_client, db_session) -> None:
    """Filtering by feedback_type returns only matching entries."""
    await _seed_feedback(db_session, title="Bug", feedback_type=FeedbackType.BUG_REPORT)
    await _seed_feedback(
        db_session,
        title="Feature",
        feedback_type=FeedbackType.FEATURE_REQUEST,
    )

    response = await async_client.get(
        "/api/v1/feedback", params={"feedback_type": "feature_request"}
    )
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Feature"


@pytest.mark.asyncio
async def test_list_feedback_combined_filters(async_client, db_session) -> None:
    """Multiple filters applied together narrow results."""
    await _seed_feedback(
        db_session,
        title="High bug",
        priority=Priority.HIGH,
        status=FeedbackStatus.OPEN,
        feedback_type=FeedbackType.BUG_REPORT,
    )
    await _seed_feedback(
        db_session,
        title="Low bug",
        priority=Priority.LOW,
        status=FeedbackStatus.OPEN,
        feedback_type=FeedbackType.BUG_REPORT,
    )
    await _seed_feedback(
        db_session,
        title="High feature",
        priority=Priority.HIGH,
        status=FeedbackStatus.OPEN,
        feedback_type=FeedbackType.FEATURE_REQUEST,
    )

    response = await async_client.get(
        "/api/v1/feedback",
        params={
            "priority": "high",
            "status": "open",
            "feedback_type": "bug_report",
        },
    )
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "High bug"


@pytest.mark.asyncio
async def test_list_feedback_pagination(async_client, db_session) -> None:
    """Pagination respects page and limit parameters."""
    for i in range(5):
        await _seed_feedback(db_session, title=f"Item {i}")

    response = await async_client.get("/api/v1/feedback", params={"page": 1, "limit": 2})
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["limit"] == 2
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_list_feedback_second_page(async_client, db_session) -> None:
    """Second page returns correct subset of results."""
    for i in range(5):
        await _seed_feedback(db_session, title=f"Item {i}")

    response = await async_client.get("/api/v1/feedback", params={"page": 2, "limit": 2})
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert len(data["items"]) == 2
    assert data["page"] == 2


@pytest.mark.asyncio
async def test_list_feedback_filter_no_matches(async_client, db_session) -> None:
    """Filter returning no matches returns empty list with total=0."""
    await _seed_feedback(db_session, title="Open item", status=FeedbackStatus.OPEN)

    response = await async_client.get("/api/v1/feedback", params={"status": "resolved"})
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_feedback_page_defaults(async_client, db_session) -> None:
    """Default pagination is page=1, limit=20."""
    response = await async_client.get("/api/v1/feedback")
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert data["page"] == 1
    assert data["limit"] == 20


@pytest.mark.asyncio
async def test_list_feedback_invalid_status_rejects(async_client) -> None:
    """Invalid status query param returns 422."""
    response = await async_client.get("/api/v1/feedback", params={"status": "not_a_status"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_feedback_page_zero_rejects(async_client) -> None:
    """Page < 1 should return 422 validation error."""
    response = await async_client.get("/api/v1/feedback", params={"page": 0})
    assert response.status_code == 422
