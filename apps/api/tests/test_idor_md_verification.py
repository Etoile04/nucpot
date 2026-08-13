"""Tests for NFM-400: IDOR fix on MD verification jobs.

Verifies that ownership checks prevent cross-user access:
- Users can only see their own jobs
- Cross-user access returns 404 (not泄露 existence)
- List endpoint filters by owner
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.auth import get_current_user
from nfm_db.database import get_db
from nfm_db.models import User
from nfm_db.models.md_verification import JobStatus
from nfm_db.services.md_verification import MDVerificationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_override(user: User):
    """Create a dependency override that returns the given user."""

    async def override():
        return user

    return override


async def _create_job_for_user(
    db_session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    potential_id: str = "test_potential_idor",
    element_system: str = "U",
    status: JobStatus = JobStatus.PENDING,
) -> uuid.UUID:
    """Create a job owned by the given user, return job UUID."""
    service = MDVerificationService(db_session)
    job = await service.create_job(
        {
            "potential_id": potential_id,
            "element_system": element_system,
            "phase": "BCC",
            "config": {"temperature": 300},
            "priority": 5,
            "status": status,
            "owner_id": owner_id,
        }
    )
    await db_session.commit()
    return job.id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def owner_user(db_session: AsyncSession) -> User:
    """Create the 'owner' test user."""
    user = User(
        username="idor_owner",
        email="idor_owner@example.com",
        hashed_password="hashed",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def other_user(db_session: AsyncSession) -> User:
    """Create the 'other' test user (not the owner)."""
    user = User(
        username="idor_other",
        email="idor_other@example.com",
        hashed_password="hashed",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def owned_job_id(db_session: AsyncSession, owner_user: User) -> uuid.UUID:
    """Create a job owned by owner_user."""
    return await _create_job_for_user(db_session, owner_user.id)


# ---------------------------------------------------------------------------
# IDOR Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMDVerificationIDOR:
    """NFM-400: Verify IDOR protection on MD verification endpoints."""

    async def test_list_jobs_only_shows_own(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        other_user: User,
    ) -> None:
        """GET /jobs should only return jobs owned by the requesting user."""
        from nfm_db.main import app

        # Create a job for each user
        await _create_job_for_user(db_session, owner_user.id, potential_id="owner_job")
        await _create_job_for_user(db_session, other_user.id, potential_id="other_job")

        # List as owner_user
        app.dependency_overrides[get_current_user] = _make_auth_override(owner_user)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/v1/md-verification/jobs")
            assert response.status_code == 200
            jobs = response.json()["jobs"]
            job_ids = {j["id"] for j in jobs}
            # owner_user's job must be present
            owner_jobs = await _fetch_job_ids(db_session, owner_user.id)
            assert owner_jobs.issubset(job_ids)
            # other_user's job must NOT be present
            other_jobs = await _fetch_job_ids(db_session, other_user.id)
            assert not other_jobs.intersection(job_ids)
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_get_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """GET /jobs/{id} returns 404 when user doesn't own the job."""
        from nfm_db.main import app

        app.dependency_overrides[get_current_user] = _make_auth_override(owner_user)
        # Note: owner_user doesn't own owned_job_id, so this should still work
        # because owned_job_id belongs to the *fixture* owner_user, not the
        # request owner. We need a different user.

        other = User(
            username="idor_interloper",
            email="interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_cancel_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """DELETE /jobs/{id} returns 404 when user doesn't own the job."""
        from nfm_db.main import app

        other = User(
            username="idor_cancel_interloper",
            email="cancel_interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.delete(f"/api/v1/md-verification/jobs/{owned_job_id}")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_status_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """GET /jobs/{id}/status returns 404 when user doesn't own the job."""
        from nfm_db.main import app

        other = User(
            username="idor_status_interloper",
            email="status_interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/status")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_simulation_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """GET /jobs/{id}/simulation returns 404 for non-owner."""
        from nfm_db.main import app

        other = User(
            username="idor_sim_interloper",
            email="sim_interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    f"/api/v1/md-verification/jobs/{owned_job_id}/simulation"
                )
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_defects_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """GET /jobs/{id}/defects returns 404 for non-owner."""
        from nfm_db.main import app

        other = User(
            username="idor_defects_interloper",
            email="defects_interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/defects")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_fitting_other_users_job_returns_404(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """GET /jobs/{id}/fitting returns 404 for non-owner."""
        from nfm_db.main import app

        other = User(
            username="idor_fitting_interloper",
            email="fitting_interloper@example.com",
            hashed_password="hashed",
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        app.dependency_overrides[get_current_user] = _make_auth_override(other)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/fitting")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)

    async def test_owner_can_access_own_job(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
        owner_user: User,
        owned_job_id: uuid.UUID,
    ) -> None:
        """The job owner CAN access their own job via all endpoints."""
        from nfm_db.main import app

        app.dependency_overrides[get_current_user] = _make_auth_override(owner_user)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # GET job
                resp = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}")
                assert resp.status_code == 200
                assert resp.json()["id"] == str(owned_job_id)
                assert resp.json()["owner_id"] == str(owner_user.id)

                # GET status
                resp = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/status")
                assert resp.status_code == 200

                # GET defects
                resp = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/defects")
                assert resp.status_code == 200

                # GET fitting
                resp = await client.get(f"/api/v1/md-verification/jobs/{owned_job_id}/fitting")
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_db, None)


async def _fetch_job_ids(
    db_session: AsyncSession,
    owner_id: uuid.UUID,
) -> set[str]:
    """Helper: get all job IDs owned by owner_id."""
    service = MDVerificationService(db_session)
    jobs = await service.list_jobs(owner_id=owner_id, limit=1000)
    return {str(j.id) for j in jobs}
