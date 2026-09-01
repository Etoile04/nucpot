"""Tests for health check endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.main import app


@pytest.mark.asyncio
async def test_health_check_returns_ok(db_session: AsyncSession) -> None:
    """Health endpoint returns status ok.

    NFM-4097 — the endpoint reads
    ``health_events.event_type = 'uuid_titled_source_blocked'`` to
    flip ``status`` to ``degraded``.  We override ``get_db`` with
    the SQLite test session so the endpoint can run without a live
    PostgreSQL.
    """
    from nfm_db.database import get_db
    from nfm_db.monitoring.worker_health import worker_health

    worker_health.reset()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["consecutive_failures"] == 0

    worker_health.reset()
