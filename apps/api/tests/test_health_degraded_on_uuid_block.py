"""NFM-4097 — AC-4: GET /api/v1/health returns ``degraded`` when a
``uuid_titled_source_blocked`` health_events row exists in the last
24 hours, ``ok`` otherwise.

This file complements :mod:`tests.test_migration_071_f4_uuid_titled_source_guard`
(AC-3 — DB trigger) by exercising the application-side flip the
trigger's INSERT ultimately drives.

Acceptance criteria covered
---------------------------

* [AC-4.1] ``GET /api/v1/health`` returns ``status='ok'`` when no
  ``uuid_titled_source_blocked`` event has been written in the last
  24 hours.
* [AC-4.2] ``GET /api/v1/health`` returns ``status='degraded'`` when
  **one or more** ``uuid_titled_source_blocked`` events exist within
  the last 24 hours.  The flip is **inclusive of 1** (the threshold
  is 24h count > 0, NOT >= 2 — see issue spec).
* [AC-4.3] Events **older than 24h** must NOT flip the status.  The
  ops contract is "recent"; a stale historical event is not actionable.
* [AC-4.4] Only ``event_type='uuid_titled_source_blocked'`` flips
  status.  Other ``health_events`` rows (e.g.
  ``fallback_triggered``, ``validation_drop``) must not trigger
  the degraded state — those surface through the existing
  ``/health/alerts`` endpoints and the admin summary.
* [AC-4.5] The service helper that drives the flip is exposed and
  unit-testable so a future caller (e.g. an on-call dashboard)
  can query the count without going through the HTTP layer.

Test strategy
-------------

The existing :func:`tests.conftest.db_session` fixture provides an
in-memory SQLite async session.  We insert HealthEvent rows
**directly** via ``session.execute(...)`` — bypassing the ORM
``HealthEvent.__init__`` / SQLAlchemy insert, mirroring how the
AC-3 trigger inserts (raw SQL ``INSERT INTO health_events ...``)
in production.  This is the "直接 SQL 绕过 ORM gate" the issue
spec calls out.

Note: SQLite does NOT enforce the
``ck_health_events_event_type`` CHECK constraint that the AC-3
migration adds to PostgreSQL, so the test can insert
``'uuid_titled_source_blocked'`` without the migration being
applied.  The migration is the production gate; the test only
verifies the application-side flip.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.health_event import HealthEvent
from nfm_db.services.health_alert_service import (
    UUID_TITLE_BLOCKED_EVENT_TYPE,
    count_recent_uuid_titled_source_blocks,
)

HEALTH_ENDPOINT = "/api/v1/health"
EVENT_TYPE = "uuid_titled_source_blocked"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async test client wired to the SQLite ``db_session`` fixture.

    The /health endpoint must remain unauthenticated (load-balancer
    probes must not require credentials) so no auth override is needed.
    """

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


async def _insert_event(
    db_session: AsyncSession,
    *,
    event_type: str = EVENT_TYPE,
    severity: str = "critical",
    source_service: str = "ingest",
    created_at: datetime | None = None,
) -> str:
    """Insert one ``health_events`` row directly via SQL.

    Bypasses the ORM ``HealthEvent.__init__`` path to mirror the
    trigger's INSERT in production (NFM-4097 AC-3 trigger writes via
    ``INSERT INTO health_events ... VALUES (...)`` plpgsql).

    Returns the event id (as a string) so the test can refer to it.
    """
    event_id = uuid.uuid4()
    created_at = created_at or datetime.now(UTC)
    context = json.dumps({"source_id": str(uuid.uuid4()), "op": "INSERT"})
    stmt = insert(HealthEvent).values(
        id=event_id,
        event_type=event_type,
        severity=severity,
        source_service=source_service,
        context=context,
        created_at=created_at,
    )
    await db_session.execute(stmt)
    await db_session.commit()
    return str(event_id)


# ---------------------------------------------------------------------------
# AC-4.1 + AC-4.2 — the /health endpoint flip
# ---------------------------------------------------------------------------


class TestHealthDegradedOnUuidBlock:
    """GET /api/v1/health flips to ``degraded`` when
    ``uuid_titled_source_blocked`` events exist in the last 24h."""

    @pytest.mark.asyncio
    async def test_returns_ok_when_no_events(self, client: AsyncClient) -> None:
        """AC-4.1 — empty ``health_events`` table -> ``status='ok'``.

        Resets ``worker_health`` first so a previous test's failure
        counter doesn't bleed into this assertion (the worker
        tracker is a module-level singleton — see NFM-2014).
        """
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()
        response = await client.get(HEALTH_ENDPOINT)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok", (
            "with no uuid_titled_source_blocked events in 24h, "
            f"/health must remain 'ok' — got {data}"
        )
        worker_health.reset()

    @pytest.mark.asyncio
    async def test_returns_degraded_when_recent_event(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """AC-4.2 — one recent ``uuid_titled_source_blocked`` event
        -> ``status='degraded'``.  Threshold is 24h count > 0.
        """
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()
        await _insert_event(
            db_session,
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )

        response = await client.get(HEALTH_ENDPOINT)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded", (
            "a uuid_titled_source_blocked event in the last 24h must "
            f"flip /health to 'degraded' — got {data}"
        )
        # The original snapshot fields must still be present so a
        # load balancer / monitoring agent that reads them doesn't
        # break.
        assert "consecutive_failures" in data
        worker_health.reset()

    @pytest.mark.asyncio
    async def test_returns_ok_when_event_is_older_than_24h(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """AC-4.3 — events older than 24h must NOT flip the status.

        The ops contract is "recent"; a stale historical event is
        not actionable on-call material.
        """
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()
        await _insert_event(
            db_session,
            created_at=datetime.now(UTC) - timedelta(hours=25),
        )

        response = await client.get(HEALTH_ENDPOINT)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok", f"an event older than 24h must NOT flip /health — got {data}"
        worker_health.reset()

    @pytest.mark.asyncio
    async def test_other_event_types_do_not_trigger_degraded(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """AC-4.4 — only ``event_type='uuid_titled_source_blocked'``
        flips the status.  Other event types (e.g.
        ``fallback_triggered``) must not trigger degraded, since
        those surface through ``/health/alerts`` already.
        """
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()
        await _insert_event(
            db_session,
            event_type="fallback_triggered",
            severity="warning",
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )

        response = await client.get(HEALTH_ENDPOINT)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok", (
            "non-uuid_titled_source_blocked events must NOT flip "
            f"/health to 'degraded' — got {data}"
        )
        worker_health.reset()


# ---------------------------------------------------------------------------
# AC-4.5 — service-layer helper, exposed for on-call dashboards
# ---------------------------------------------------------------------------


class TestCountRecentUuidTitledSourceBlocks:
    """Unit tests for ``count_recent_uuid_titled_source_blocks``
    — the service helper that drives the AC-4 flip.

    Other callers (on-call dashboards, custom monitors) need to
    query the same count without going through the HTTP layer;
    this is the public seam.
    """

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_events(self, db_session: AsyncSession) -> None:
        count = await count_recent_uuid_titled_source_blocks(db_session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_returns_count_of_recent_events(self, db_session: AsyncSession) -> None:
        # Three recent events.
        for _ in range(3):
            await _insert_event(
                db_session,
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        count = await count_recent_uuid_titled_source_blocks(db_session)
        assert count == 3

    @pytest.mark.asyncio
    async def test_excludes_events_older_than_24h(self, db_session: AsyncSession) -> None:
        # Two recent + one old.
        await _insert_event(db_session, created_at=datetime.now(UTC))
        await _insert_event(db_session, created_at=datetime.now(UTC) - timedelta(hours=1))
        await _insert_event(db_session, created_at=datetime.now(UTC) - timedelta(hours=48))
        count = await count_recent_uuid_titled_source_blocks(db_session)
        assert count == 2

    @pytest.mark.asyncio
    async def test_excludes_other_event_types(self, db_session: AsyncSession) -> None:
        # Two uuid_titled + one fallback.
        await _insert_event(db_session, created_at=datetime.now(UTC))
        await _insert_event(db_session, created_at=datetime.now(UTC))
        await _insert_event(
            db_session,
            event_type="fallback_triggered",
            severity="warning",
            created_at=datetime.now(UTC),
        )
        count = await count_recent_uuid_titled_source_blocks(db_session)
        assert count == 2

    @pytest.mark.asyncio
    async def test_event_type_constant_matches_issue_spec(self) -> None:
        """AC-4 contract — the event_type literal must match what the
        AC-3 trigger writes.  Drift between the two surfaces as
        'trigger fires but /health stays ok' — the silent failure
        NFM-4097 exists to prevent."""
        assert UUID_TITLE_BLOCKED_EVENT_TYPE == "uuid_titled_source_blocked"
