"""Tests for GET /api/admin/health/alerts (NFM-2416).

Covers: auth guard (401 unauthenticated, 403 non-admin), response format
(status healthy/degraded, alerts with type/count/last_seen), empty state,
and seeded-data aggregation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.health_event import HealthEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    *,
    event_type: str = "fallback_triggered",
    severity: str = "warning",
    source_service: str = "mineru_extraction",
    context: dict | None = None,
    created_at: datetime | None = None,
) -> HealthEvent:
    """Create a HealthEvent instance without persisting."""
    return HealthEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        severity=severity,
        source_service=source_service,
        context=context or {"error": "test"},
        created_at=created_at or datetime.now(UTC),
    )


async def _seed_events(
    db: AsyncSession,
    count: int = 5,
    **overrides,
) -> list[HealthEvent]:
    """Insert *count* events and return them."""
    events: list[HealthEvent] = []
    for i in range(count):
        evt = _make_event(**overrides)
        if i > 0:
            evt.created_at = evt.created_at - timedelta(hours=i)
        db.add(evt)
        events.append(evt)
    await db.flush()
    return events


@pytest.fixture
async def client(db_session: AsyncSession):
    """Async test client with DB override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_admin_health_alerts_unauthenticated_returns_401(
    client: AsyncClient,
):
    """Unauthenticated requests to /api/admin/health/alerts return 401."""
    resp = await client.get("/api/admin/health/alerts")
    assert resp.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_admin_health_alerts_editor_returns_403(
    client: AsyncClient,
    editor_headers: dict,
):
    """Non-admin users (editor) receive 403 Forbidden."""
    resp = await client.get(
        "/api/admin/health/alerts",
        headers=editor_headers,
    )
    assert resp.status_code == 403


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_admin_health_alerts_reviewer_returns_403(
    client: AsyncClient,
    reviewer_headers: dict,
):
    """Non-admin users (reviewer) receive 403 Forbidden."""
    resp = await client.get(
        "/api/admin/health/alerts",
        headers=reviewer_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_health_alerts_admin_returns_200(
    client: AsyncClient,
    admin_headers: dict,
):
    """Admin users receive 200 OK."""
    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Response format tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_health_alerts_response_shape(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Response contains status and alerts fields with correct structure."""
    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert "status" in body
    assert body["status"] in ("healthy", "degraded")
    assert "alerts" in body
    assert isinstance(body["alerts"], list)


@pytest.mark.asyncio
async def test_admin_health_alerts_empty_returns_healthy(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Empty state returns status 'healthy' with empty alerts."""
    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["alerts"] == []


@pytest.mark.asyncio
async def test_admin_health_alerts_alert_has_required_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Each alert contains type, count, and last_seen fields."""
    now = datetime.now(UTC)
    await _seed_events(
        db_session,
        count=3,
        event_type="fallback_triggered",
        severity="warning",
        created_at=now,
    )
    await db_session.flush()

    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    assert len(body["alerts"]) >= 1

    for alert in body["alerts"]:
        assert "type" in alert
        assert "count" in alert
        assert "last_seen" in alert
        assert isinstance(alert["count"], int)
        assert isinstance(alert["type"], str)


# ---------------------------------------------------------------------------
# Aggregation / data tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_health_alerts_degraded_when_events_exist(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Status is 'degraded' when health events exist."""
    await _seed_events(db_session, count=2, severity="error")
    await db_session.flush()

    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["status"] == "degraded"


@pytest.mark.asyncio
async def test_admin_health_alerts_groups_by_type(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Events are grouped by event_type with correct counts."""
    now = datetime.now(UTC)
    await _seed_events(
        db_session,
        count=3,
        event_type="fallback_triggered",
        severity="warning",
        created_at=now,
    )
    await _seed_events(
        db_session,
        count=2,
        event_type="validation_drop",
        severity="error",
        created_at=now - timedelta(minutes=5),
    )
    await db_session.flush()

    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    alerts_by_type = {a["type"]: a for a in body["alerts"]}

    assert "fallback_triggered" in alerts_by_type
    assert alerts_by_type["fallback_triggered"]["count"] == 3

    assert "validation_drop" in alerts_by_type
    assert alerts_by_type["validation_drop"]["count"] == 2


@pytest.mark.asyncio
async def test_admin_health_alerts_last_seen_is_latest_timestamp(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """last_seen for each type reflects the newest event timestamp."""
    now = datetime.now(UTC)
    await _seed_events(
        db_session,
        count=1,
        event_type="fallback_triggered",
        created_at=now - timedelta(hours=2),
    )
    await _seed_events(
        db_session,
        count=1,
        event_type="fallback_triggered",
        created_at=now,
    )
    await db_session.flush()

    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    alert = next(a for a in body["alerts"] if a["type"] == "fallback_triggered")
    last_seen = datetime.fromisoformat(alert["last_seen"]).replace(tzinfo=UTC)
    assert (now - last_seen) < timedelta(minutes=1)


@pytest.mark.asyncio
async def test_admin_health_alerts_ignores_old_events(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict,
):
    """Events older than 24 hours are excluded from the summary."""
    now = datetime.now(UTC)
    await _seed_events(
        db_session,
        count=1,
        event_type="old_event",
        created_at=now - timedelta(hours=48),
    )
    await db_session.flush()

    resp = await client.get(
        "/api/admin/health/alerts",
        headers=admin_headers,
    )
    body = resp.json()
    assert body["status"] == "healthy"
    types = [a["type"] for a in body["alerts"]]
    assert "old_event" not in types
