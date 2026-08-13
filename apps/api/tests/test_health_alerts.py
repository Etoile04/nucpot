"""Tests for GET /api/v1/health/alerts and /api/v1/health/alerts/summary (NFM-2222).

Covers: empty result set, filtered queries, pagination, invalid params,
and the summary aggregation sub-endpoint.
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


async def _seed_events(db: AsyncSession, count: int = 5, **overrides) -> list[HealthEvent]:
    """Insert *count* events and return them."""
    events: list[HealthEvent] = []
    for i in range(count):
        evt = _make_event(**overrides)
        if i > 0:
            # Stagger created_at so ordering is deterministic.
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
# GET /api/v1/health/alerts — baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_empty_result(client: AsyncClient, db_session: AsyncSession):
    """Endpoint returns 200 with empty list when no events exist."""
    resp = await client.get("/api/v1/health/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["alerts"] == []
    assert data["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts — seeded data, default params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_returns_seeded_events(
    client: AsyncClient, db_session: AsyncSession
):
    """Endpoint returns all seeded events ordered by created_at desc."""
    await _seed_events(db_session, count=3)

    resp = await client.get("/api/v1/health/alerts")
    assert resp.status_code == 200
    body = resp.json()
    data = body["data"]
    assert len(data["alerts"]) == 3
    assert data["meta"]["total"] == 3


@pytest.mark.asyncio
async def test_alerts_ordering_newest_first(
    client: AsyncClient, db_session: AsyncSession
):
    """Events are returned newest-first (created_at desc)."""
    now = datetime.now(UTC)
    await _seed_events(db_session, count=1, created_at=now - timedelta(hours=2))
    await _seed_events(db_session, count=1, created_at=now - timedelta(hours=1))
    await _seed_events(db_session, count=1, created_at=now)
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts")
    data = resp.json()["data"]
    timestamps = [a["created_at"] for a in data["alerts"]]
    # Newest first: timestamps should be descending.
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts — filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_filter_event_type(
    client: AsyncClient, db_session: AsyncSession
):
    """Filtering by event_type returns only matching events."""
    await _seed_events(db_session, count=3, event_type="fallback_triggered")
    await _seed_events(db_session, count=2, event_type="validation_drop")
    await db_session.flush()

    resp = await client.get(
        "/api/v1/health/alerts", params={"event_type": "validation_drop"}
    )
    data = resp.json()["data"]
    assert len(data["alerts"]) == 2
    for alert in data["alerts"]:
        assert alert["event_type"] == "validation_drop"


@pytest.mark.asyncio
async def test_alerts_filter_severity(
    client: AsyncClient, db_session: AsyncSession
):
    """Filtering by severity returns only matching events."""
    await _seed_events(db_session, count=3, severity="warning")
    await _seed_events(db_session, count=1, severity="error")
    await db_session.flush()

    resp = await client.get(
        "/api/v1/health/alerts", params={"severity": "error"}
    )
    data = resp.json()["data"]
    assert len(data["alerts"]) == 1
    assert data["alerts"][0]["severity"] == "error"


@pytest.mark.asyncio
async def test_alerts_filter_source_service(
    client: AsyncClient, db_session: AsyncSession
):
    """Filtering by source_service returns only matching events."""
    await _seed_events(db_session, count=2, source_service="mineru_extraction")
    await _seed_events(db_session, count=1, source_service="pdf_parsing")
    await db_session.flush()

    resp = await client.get(
        "/api/v1/health/alerts", params={"source_service": "pdf_parsing"}
    )
    data = resp.json()["data"]
    assert len(data["alerts"]) == 1
    assert data["alerts"][0]["source_service"] == "pdf_parsing"


@pytest.mark.asyncio
async def test_alerts_filter_since(
    client: AsyncClient, db_session: AsyncSession
):
    """Filtering by 'since' returns only events after that timestamp."""
    now = datetime.now(UTC)
    await _seed_events(db_session, count=2, created_at=now - timedelta(hours=48))
    await _seed_events(db_session, count=3, created_at=now - timedelta(hours=1))
    await db_session.flush()

    since = (now - timedelta(hours=24)).isoformat()
    resp = await client.get("/api/v1/health/alerts", params={"since": since})
    data = resp.json()["data"]
    assert len(data["alerts"]) == 3
    assert data["meta"]["total"] == 3


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts — pagination (limit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_limit_respected(
    client: AsyncClient, db_session: AsyncSession
):
    """The limit param caps the number of returned events."""
    await _seed_events(db_session, count=10)
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts", params={"limit": 3})
    data = resp.json()["data"]
    assert len(data["alerts"]) == 3
    assert data["meta"]["total"] == 10


@pytest.mark.asyncio
async def test_alerts_limit_default_50(
    client: AsyncClient, db_session: AsyncSession
):
    """Default limit is 50 when not specified."""
    for _ in range(60):
        db_session.add(_make_event())
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts")
    data = resp.json()["data"]
    assert len(data["alerts"]) == 50
    assert data["meta"]["total"] == 60


@pytest.mark.asyncio
async def test_alerts_limit_max_500(
    client: AsyncClient, db_session: AsyncSession
):
    """Limit value 500 is accepted and returned correctly."""
    for _ in range(10):
        db_session.add(_make_event())
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts", params={"limit": 500})
    data = resp.json()["data"]
    # 10 events exist, so all returned, but meta.total is the unfiltered count.
    assert len(data["alerts"]) == 10
    assert data["meta"]["total"] == 10


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts — invalid params -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_invalid_limit_negative(
    client: AsyncClient, db_session: AsyncSession
):
    """A negative limit returns 422."""
    resp = await client.get("/api/v1/health/alerts", params={"limit": -1})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_alerts_invalid_since_format(
    client: AsyncClient, db_session: AsyncSession
):
    """An unparseable 'since' param returns 422."""
    resp = await client.get("/api/v1/health/alerts", params={"since": "not-a-date"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts — response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_response_shape(
    client: AsyncClient, db_session: AsyncSession
):
    """Each alert has the expected fields and the meta envelope is correct."""
    now = datetime.now(UTC)
    evt = _make_event(
        event_type="fallback_triggered",
        severity="warning",
        source_service="mineru_extraction",
        context={"error": "something failed"},
        created_at=now,
    )
    db_session.add(evt)
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts")
    data = resp.json()["data"]
    assert len(data["alerts"]) == 1
    alert = data["alerts"][0]

    assert "id" in alert
    assert alert["event_type"] == "fallback_triggered"
    assert alert["severity"] == "warning"
    assert alert["source_service"] == "mineru_extraction"
    assert alert["context"] == {"error": "something failed"}
    assert "created_at" in alert

    meta = data["meta"]
    assert meta["total"] == 1
    assert "since" in meta
    assert "filters" in meta


# ---------------------------------------------------------------------------
# GET /api/v1/health/alerts/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_empty(client: AsyncClient, db_session: AsyncSession):
    """Summary returns zeros when no events exist."""
    resp = await client.get("/api/v1/health/alerts/summary")
    assert resp.status_code == 200
    body = resp.json()
    summary = body["data"]["summary"]
    assert summary["total_events"] == 0
    assert summary["by_type"] == {}
    assert summary["by_severity"] == {}


@pytest.mark.asyncio
async def test_summary_aggregation(
    client: AsyncClient, db_session: AsyncSession
):
    """Summary correctly aggregates counts by type and severity."""
    await _seed_events(db_session, count=3, event_type="fallback_triggered", severity="warning")
    await _seed_events(db_session, count=2, event_type="fallback_triggered", severity="error")
    await _seed_events(db_session, count=1, event_type="validation_drop", severity="warning")
    await db_session.flush()

    resp = await client.get("/api/v1/health/alerts/summary")
    assert resp.status_code == 200
    summary = resp.json()["data"]["summary"]

    assert summary["total_events"] == 6
    assert summary["by_type"]["fallback_triggered"] == 5
    assert summary["by_type"]["validation_drop"] == 1
    assert summary["by_severity"]["warning"] == 4
    assert summary["by_severity"]["error"] == 2


@pytest.mark.asyncio
async def test_summary_period_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """Summary includes period.since and period.until timestamps."""
    resp = await client.get("/api/v1/health/alerts/summary")
    summary = resp.json()["data"]["summary"]
    assert "period" in summary
    assert "since" in summary["period"]
    assert "until" in summary["period"]
