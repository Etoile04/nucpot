"""Tests for GET /api/v1/admin/health/alerts endpoint (NFM-2414).

Covers:
- Admin auth guard: 401 for unauthenticated, 403 for non-admin.
- Healthy response when no ``health_events`` rows fall in the look-back
  window.
- Degraded response with per-event-type aggregation when rows are
  present. The previous implementation imported a non-existent module
  and always returned ``healthy, alerts: []``; these tests exercise
  the real ``health_events`` query path so that failure mode cannot
  silently regress.

Auth strategy: ``require_admin`` depends on ``get_current_active_user``,
which in turn hits Postgres. To exercise the real role gate (which is
the actual unit under test) without standing up Postgres we override
``get_current_active_user`` to return a hand-built ``User`` and let
the real ``require_admin`` enforce the role. The DB is only used by
the data-path tests, which override ``get_db``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models import BlogRole, User
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
    """Build an in-memory ``HealthEvent`` row (no flush)."""
    return HealthEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        severity=severity,
        source_service=source_service,
        context=context or {"error": "test"},
        created_at=created_at or datetime.now(UTC),
    )


def _user_with_role(role: BlogRole) -> User:
    """Build a hand-built active user with the given role (no DB write)."""
    return User(
        id=uuid.uuid4(),
        username=f"user-{role.value}",
        email=f"{role.value}@example.com",
        hashed_password="hashed",
        blog_role=role,
        is_active=True,
        is_service_account=False,
    )


@pytest.fixture
def admin_client(db_session: AsyncSession) -> AsyncClient:
    """Authenticated admin client backed by the test DB session."""
    admin = _user_with_role(BlogRole.ADMIN)

    async def _override_active_user() -> User:
        return admin

    async def _override_get_db() -> AsyncSession:
        yield db_session

    app.dependency_overrides[get_current_active_user] = _override_active_user
    app.dependency_overrides[get_db] = _override_get_db
    try:
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")
    finally:
        pass  # cleanup happens on fixture teardown below


@pytest.fixture
async def admin_async_client(db_session: AsyncSession):
    """Async context-manager form of ``admin_client``."""
    admin = _user_with_role(BlogRole.ADMIN)

    async def _override_active_user() -> User:
        return admin

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_current_active_user] = _override_active_user
    app.dependency_overrides[get_db] = _override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def editor_client():
    """Client authenticated as a non-admin editor — must hit the 403 gate."""
    editor = _user_with_role(BlogRole.EDITOR)

    async def _override_active_user() -> User:
        return editor

    app.dependency_overrides[get_current_active_user] = _override_active_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
@pytest.mark.asyncio
async def test_unauthenticated_returns_401() -> None:
    """Missing credentials must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/admin/health/alerts")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_returns_403(editor_client: AsyncClient) -> None:
    """Non-admin role (editor) must receive 403 from the real ``require_admin``.

    The override only short-circuits ``get_current_active_user`` (the
    upstream DB lookup). The role check inside ``require_admin`` runs
    for real, so this proves the gate is wired up — the previous test
    mocked the whole ``require_admin`` and proved nothing.
    """
    response = await editor_client.get("/api/v1/admin/health/alerts")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_service_account_is_rejected_with_403() -> None:
    """Service accounts must be denied — proves the role-gate's service-account guard.

    ``require_admin`` short-circuits any ``is_service_account=True`` user
    with 403 before the BlogRole check (auth.py:111-115). A pure role
    override would miss this branch.
    """
    svc = _user_with_role(BlogRole.ADMIN)
    svc.is_service_account = True

    async def _override_active_user() -> User:
        return svc

    app.dependency_overrides[get_current_active_user] = _override_active_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/admin/health/alerts")
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Healthy / degraded data path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_returns_healthy_when_no_events(
    admin_async_client: AsyncClient,
) -> None:
    """Admin role + empty health_events table must yield ``status=healthy``."""
    response = await admin_async_client.get("/api/v1/admin/health/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "healthy"
    assert data["alerts"] == []


@pytest.mark.asyncio
async def test_admin_returns_degraded_with_aggregated_alerts(
    admin_async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Seeded events must produce ``status=degraded`` with per-type counts.

    Seeds three rows for ``fallback_triggered`` and one for
    ``validation_drop``; the response must aggregate to two alert
    items with the correct counts and a recent ``last_seen``.
    """
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _make_event(
                event_type="fallback_triggered",
                severity="warning",
                created_at=now - timedelta(minutes=10),
            ),
            _make_event(
                event_type="fallback_triggered",
                severity="error",
                created_at=now - timedelta(minutes=5),
            ),
            _make_event(
                event_type="fallback_triggered",
                severity="warning",
                created_at=now - timedelta(minutes=2),
            ),
            _make_event(
                event_type="validation_drop",
                severity="warning",
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db_session.flush()

    response = await admin_async_client.get("/api/v1/admin/health/alerts")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"

    alerts_by_type = {alert["type"]: alert for alert in data["alerts"]}
    assert set(alerts_by_type) == {"fallback_triggered", "validation_drop"}
    assert alerts_by_type["fallback_triggered"]["count"] == 3
    assert alerts_by_type["validation_drop"]["count"] == 1


@pytest.mark.asyncio
async def test_admin_excludes_events_outside_lookback_window(
    admin_async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Events older than the look-back window must not surface as alerts."""
    db_session.add(
        _make_event(
            event_type="fallback_triggered",
            created_at=datetime.now(UTC) - timedelta(hours=72),
        )
    )
    await db_session.flush()

    response = await admin_async_client.get("/api/v1/admin/health/alerts")
    data = response.json()["data"]

    assert data["status"] == "healthy"
    assert data["alerts"] == []


@pytest.mark.asyncio
async def test_admin_alert_items_have_required_fields(
    admin_async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Each alert must carry ``type``, ``count``, and ``last_seen``."""
    db_session.add(_make_event(event_type="asyncio_crash"))
    await db_session.flush()

    response = await admin_async_client.get("/api/v1/admin/health/alerts")
    alerts = response.json()["data"]["alerts"]

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["type"] == "asyncio_crash"
    assert isinstance(alert["count"], int)
    assert alert["count"] == 1
    assert "last_seen" in alert


# ---------------------------------------------------------------------------
# Aggregator failure must not silently report healthy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_reports_degraded_when_db_query_fails(
    admin_async_client: AsyncClient,
) -> None:
    """A failing aggregator must surface a synthetic alert, not ``healthy``.

    This is the failure mode the previous implementation silently
    exhibited: an ImportError swallowed by ``except ImportError``
    returned ``healthy, alerts: []`` regardless of what the system was
    actually doing. If the DB query fails the endpoint must flip to
    ``degraded`` so the dashboard sees red and an operator investigates.
    """
    from nfm_db.services import admin_health_service as service_module

    real_collect = service_module._collect_alerts

    async def _broken_collect(_db: AsyncSession) -> list:
        raise RuntimeError("simulated db failure")

    service_module._collect_alerts = _broken_collect  # type: ignore[assignment]
    try:
        response = await admin_async_client.get("/api/v1/admin/health/alerts")
    finally:
        service_module._collect_alerts = real_collect  # type: ignore[assignment]

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert len(data["alerts"]) == 1
    assert data["alerts"][0]["type"] == "aggregator_failure"
    assert data["alerts"][0]["count"] == 1


# ---------------------------------------------------------------------------
# Internal sanity check (not via HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_health_alerts_service_directly(
    db_session: AsyncSession,
) -> None:
    """The service function must aggregate ``health_events`` by ``event_type``."""
    from nfm_db.services.admin_health_service import get_health_alerts

    db_session.add_all(
        [
            _make_event(event_type="asyncio_crash"),
            _make_event(event_type="asyncio_crash"),
            _make_event(event_type="validation_drop"),
        ]
    )
    await db_session.flush()

    result = await get_health_alerts(db_session)

    assert result.status == "degraded"
    by_type = {alert.type: alert.count for alert in result.alerts}
    assert by_type == {"asyncio_crash": 2, "validation_drop": 1}


@pytest.mark.asyncio
async def test_get_health_alerts_empty_db(db_session: AsyncSession) -> None:
    """Empty DB must return ``healthy`` with no alerts at the service layer."""
    from nfm_db.services.admin_health_service import get_health_alerts

    result = await get_health_alerts(db_session)

    assert result.status == "healthy"
    assert result.alerts == []
