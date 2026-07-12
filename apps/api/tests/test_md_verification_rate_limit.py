"""Rate-limit tests for MD verification job submission (NFM-401).

Verifies that POST /api/v1/md-verification/jobs enforces per-IP rate limiting
to prevent cost/DoS abuse on shared HPC resources.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.auth import get_current_user
from nfm_db.main import app
from nfm_db.models import User
from nfm_db.services.rate_limit import (
    InProcessRateLimiter,
    make_rate_limit_dependency,
    md_verification_rate_limit,
)

_SUBMIT_URL = "/api/v1/md-verification/jobs"

_JOB_PAYLOAD = {
    "potential_id": "test_pot_001",
    "element_system": "UO2",
    "phase": "BCC",
    "potential_file": "/data/potentials/UO2.eam.alloy",
    "structure_file": "/data/structures/UO2.cif",
    "config": {"temperature": 300, "pressure": 0},
    "priority": 5,
}


@pytest.fixture
async def _auth_override(admin_user: User):
    """Override get_current_user so the stub auth doesn't block tests."""

    async def _fake_current_user() -> User:
        return admin_user

    app.dependency_overrides[get_current_user] = _fake_current_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_md_job_submission_rate_limit_returns_429(
    async_client,
    db_session: AsyncSession,
    admin_user: User,
    _auth_override,
) -> None:
    """After the limit, the next submission request is 429 with a Retry-After header."""
    tight = InProcessRateLimiter(max_requests=2, window_seconds=60)
    app.dependency_overrides[md_verification_rate_limit] = make_rate_limit_dependency(tight)

    try:
        with (
            patch(
                "nfm_db.api.v1.md_verification.CELERY_AVAILABLE",
                True,
            ),
            patch(
                "nfm_db.api.v1.md_verification.celery_app.send_task",
            ) as mock_send_task,
        ):
            mock_send_task.return_value = MagicMock(id="test-task-id")

            first = await async_client.post(
                _SUBMIT_URL,
                json=_JOB_PAYLOAD,
            )
            second = await async_client.post(
                _SUBMIT_URL,
                json=_JOB_PAYLOAD,
            )
            third = await async_client.post(
                _SUBMIT_URL,
                json=_JOB_PAYLOAD,
            )

            assert first.status_code == 201, f"Expected 201, got {first.status_code}: {first.text}"
            assert second.status_code == 201, (
                f"Expected 201, got {second.status_code}: {second.text}"
            )
            assert third.status_code == 429, f"Expected 429, got {third.status_code}: {third.text}"
            assert "retry-after" in {k.lower() for k in third.headers}
    finally:
        app.dependency_overrides.pop(md_verification_rate_limit, None)


@pytest.mark.asyncio
async def test_md_job_submission_under_limit_succeeds(
    async_client,
    db_session: AsyncSession,
    admin_user: User,
    _auth_override,
) -> None:
    """Requests within the rate limit are accepted normally."""
    limiter = InProcessRateLimiter(max_requests=5, window_seconds=60)
    app.dependency_overrides[md_verification_rate_limit] = make_rate_limit_dependency(limiter)

    try:
        with (
            patch(
                "nfm_db.api.v1.md_verification.CELERY_AVAILABLE",
                True,
            ),
            patch(
                "nfm_db.api.v1.md_verification.celery_app.send_task",
            ) as mock_send_task,
        ):
            mock_send_task.return_value = MagicMock(id="test-task-id")

            response = await async_client.post(
                _SUBMIT_URL,
                json=_JOB_PAYLOAD,
            )

            assert response.status_code == 201, (
                f"Expected 201, got {response.status_code}: {response.text}"
            )
    finally:
        app.dependency_overrides.pop(md_verification_rate_limit, None)
