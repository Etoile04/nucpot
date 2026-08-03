"""Tests for GET /api/admin/health/alerts (NFM-2440 / NFM-2422).

The public ``/api/v1/health/alerts`` endpoints (NFM-2222) are deliberately
unauthenticated. This admin-gated endpoint exposes the same underlying
``health_events`` table as a compact *active error* summary and requires
``BlogRole.ADMIN``.

Response contract (errors live in the body, never the HTTP status code)::

    {"status": "ok",
     "active_errors": {"count": N,
                       "categories": {"event_type": {...},
                                      "severity": {...}}}}
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

ENDPOINT = "/api/admin/health/alerts"


def _make_event(
    *,
    event_type: str = "fallback_triggered",
    severity: str = "error",
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


@pytest.fixture
async def client(db_session: AsyncSession):
    """Async test client with DB override.

    The session-scoped conftest fixture auto-authenticates every request as
    ``BlogRole.ADMIN`` (unless ``no_auto_auth``), so this client passes the
    admin gate by default.
    """

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Response shape / AC: 200 + structured JSON summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_ok_with_zero_count_when_no_events(client: AsyncClient) -> None:
    """Empty table -> 200 with count 0 and empty category maps."""
    resp = await client.get(ENDPOINT)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "ok"
    assert data["active_errors"]["count"] == 0
    assert data["active_errors"]["categories"] == {
        "event_type": {},
        "severity": {},
    }


@pytest.mark.asyncio
async def test_returns_200_when_errors_exist(client: AsyncClient, db_session: AsyncSession) -> None:
    """AC: errors live in the body, never in the HTTP status code."""
    db_session.add(_make_event(severity="error"))
    db_session.add(_make_event(severity="critical"))
    await db_session.flush()

    resp = await client.get(ENDPOINT)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "ok"
    assert data["active_errors"]["count"] == 2


@pytest.mark.asyncio
async def test_counts_only_error_and_critical_severities(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """info/warning are not 'active errors'; error/critical are."""
    for severity in ("info", "warning", "error", "critical"):
        db_session.add(_make_event(severity=severity))
    await db_session.flush()

    resp = await client.get(ENDPOINT)

    assert resp.status_code == 200
    assert resp.json()["data"]["active_errors"]["count"] == 2


@pytest.mark.asyncio
async def test_categories_break_down_by_event_type_and_severity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """AC: categories include event_type and severity breakdowns."""
    db_session.add(_make_event(event_type="validation_drop", severity="error"))
    db_session.add(_make_event(event_type="validation_drop", severity="critical"))
    db_session.add(_make_event(event_type="asyncio_crash", severity="error"))
    # Non-error events must not create a category.
    db_session.add(_make_event(event_type="fallback_triggered", severity="warning"))
    await db_session.flush()

    resp = await client.get(ENDPOINT)

    categories = resp.json()["data"]["active_errors"]["categories"]
    assert categories["event_type"] == {"validation_drop": 2, "asyncio_crash": 1}
    assert categories["severity"] == {"error": 2, "critical": 1}
    assert "fallback_triggered" not in categories["event_type"]
    assert "warning" not in categories["severity"]


@pytest.mark.asyncio
async def test_excludes_events_outside_default_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Events older than the 24 h default window are not counted."""
    db_session.add(_make_event(severity="error"))
    db_session.add(
        _make_event(
            severity="error",
            created_at=datetime.now(UTC) - timedelta(hours=48),
        )
    )
    await db_session.flush()

    resp = await client.get(ENDPOINT)

    assert resp.json()["data"]["active_errors"]["count"] == 1


@pytest.mark.asyncio
async def test_since_query_param_widens_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An explicit ``since`` overrides the 24 h default."""
    db_session.add(
        _make_event(
            severity="error",
            created_at=datetime.now(UTC) - timedelta(hours=48),
        )
    )
    await db_session.flush()

    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    resp = await client.get(ENDPOINT, params={"since": since})

    assert resp.status_code == 200
    assert resp.json()["data"]["active_errors"]["count"] == 1


@pytest.mark.asyncio
async def test_invalid_since_returns_422(client: AsyncClient) -> None:
    """A non-ISO ``since`` is rejected by FastAPI validation."""
    resp = await client.get(ENDPOINT, params={"since": "not-a-date"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_response_reports_window_metadata(client: AsyncClient) -> None:
    """The payload states the window it summarised."""
    resp = await client.get(ENDPOINT)

    data = resp.json()["data"]
    assert "since" in data["active_errors"]
    assert "until" in data["active_errors"]
    # Both are ISO 8601 strings and parse cleanly.
    datetime.fromisoformat(data["active_errors"]["since"])
    datetime.fromisoformat(data["active_errors"]["until"])


# ---------------------------------------------------------------------------
# Admin-only access enforcement
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_requires_authentication(client: AsyncClient) -> None:
    """No credentials -> 401."""
    resp = await client.get(ENDPOINT)

    assert resp.status_code == 401


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_non_admin_role_is_forbidden(client: AsyncClient, editor_headers: dict) -> None:
    """An authenticated EDITOR is not an admin -> 403."""
    resp = await client.get(ENDPOINT, headers=editor_headers)

    assert resp.status_code == 403


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_admin_role_is_allowed(client: AsyncClient, admin_headers: dict) -> None:
    """An authenticated ADMIN reaches the endpoint -> 200."""
    resp = await client.get(ENDPOINT, headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_returns_zero_for_empty_table(db_session: AsyncSession) -> None:
    """get_active_error_summary handles an empty window."""
    from nfm_db.services.health_alert_service import get_active_error_summary

    result = await get_active_error_summary(db_session)

    assert result.status == "ok"
    assert result.active_errors.count == 0
    assert result.active_errors.categories == {"event_type": {}, "severity": {}}


@pytest.mark.asyncio
async def test_service_aggregates_by_event_type_and_severity(
    db_session: AsyncSession,
) -> None:
    """get_active_error_summary groups error/critical rows by both axes."""
    from nfm_db.services.health_alert_service import get_active_error_summary

    db_session.add(_make_event(event_type="asyncio_crash", severity="critical"))
    db_session.add(_make_event(event_type="asyncio_crash", severity="error"))
    db_session.add(_make_event(event_type="validation_drop", severity="info"))
    await db_session.flush()

    result = await get_active_error_summary(db_session)

    assert result.active_errors.count == 2
    assert result.active_errors.categories == {
        "event_type": {"asyncio_crash": 2},
        "severity": {"critical": 1, "error": 1},
    }
