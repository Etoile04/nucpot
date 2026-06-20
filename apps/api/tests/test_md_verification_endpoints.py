"""Integration tests for MD verification API endpoints (NFM-336).

Tests all 8 MD verification endpoints:
- POST /api/v1/md-verification/jobs - Submit MD verification job
- GET /api/v1/md-verification/jobs - List jobs with filters
- GET /api/v1/md-verification/jobs/{id} - Get job details
- GET /api/v1/md-verification/jobs/{id}/status - Get job status
- DELETE /api/v1/md-verification/jobs/{id} - Cancel job
- GET /api/v1/md-verification/jobs/{id}/simulation - Get simulation results
- GET /api/v1/md-verification/jobs/{id}/defects - Get defect analysis
- GET /api/v1/md-verification/jobs/{id}/fitting - Get fitting results
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcJobStatus,
    JobStatus,
)
from nfm_db.services.md_verification import MDVerificationService


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def md_job_with_results(
    db_session: AsyncSession,
) -> dict[str, str]:
    """Create a test MD verification job with complete results.

    Returns:
        Dictionary with job_id and related result IDs
    """
    service = MDVerificationService(db_session)

    # Create job
    job = await service.create_job(
        {
            "potential_id": "test_potential",
            "element_system": "U",
            "phase": "BCC",
            "config": {"temperature": 300, "pressure": 0},
            "priority": 5,
            "status": JobStatus.COMPLETED,
        }
    )

    # Create simulation result
    await service.create_simulation_result(
        {
            "verification_job_id": job.id,
            "trajectory_file_path": "/data/trajectory.lammpstrj",
            "thermodynamic_data": {
                "temperature": [300, 301, 302],
                "pressure": [0.1, 0.1, 0.1],
                "volume": [100.0, 100.1, 100.2],
            },
            "simulation_time_ps": 100.0,
            "steps_completed": 100000,
            "final_energy": -1000.5,
            "final_temperature": 300.0,
            "final_pressure": 0.1,
        }
    )

    # Create defect analysis results
    await service.create_defect_result(
        {
            "verification_job_id": job.id,
            "defect_type": DefectType.VACANCY,
            "concentration": 0.001,
            "formation_energy": 1.5,
            "metadata": {"analysis_method": "Wigner-Seitz"},
        }
    )

    await service.create_defect_result(
        {
            "verification_job_id": job.id,
            "defect_type": DefectType.INTERSTITIAL,
            "concentration": 0.002,
            "formation_energy": 2.0,
            "metadata": {"analysis_method": "Wigner-Seitz"},
        }
    )

    # Create fitting result
    await service.create_fitting_result(
        {
            "verification_job_id": job.id,
            "fitting_method": FittingMethod.ARC_DPA,
            "parameters": {"epsilon": 0.5, "sigma": 2.5, "a": 1.0, "q": 0.0},
            "quality_metrics": {"rmse": 0.01, "r_squared": 0.99},
        }
    )

    await db_session.commit()

    return {"job_id": str(job.id)}


@pytest.fixture
async def pending_md_job(
    db_session: AsyncSession,
) -> dict[str, str]:
    """Create a test MD verification job in PENDING status.

    Returns:
        Dictionary with job_id
    """
    service = MDVerificationService(db_session)

    job = await service.create_job(
        {
            "potential_id": "test_potential_pending",
            "element_system": "UO2",
            "phase": "FLUORITE",
            "config": {"temperature": 300, "pressure": 0},
            "priority": 5,
            "status": JobStatus.PENDING,
        }
    )

    await db_session.commit()

    return {"job_id": str(job.id)}


# ---------------------------------------------------------------------------
# Endpoint Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMDVerificationHealth:
    """Test MD verification health check endpoint."""

    async def test_health_check(self, base_url: str) -> None:
        """Test GET /api/v1/md-verification/health returns healthy status."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get("/api/v1/md-verification/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["module"] == "md-verification"
        assert "version" in data
        assert "timestamp" in data


@pytest.mark.integration
class TestSubmitMDVerificationJob:
    """Test POST /api/v1/md-verification/jobs endpoint."""

    async def test_submit_job_success(
        self,
        base_url: str,
        async_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test successful job submission creates job and returns 201."""

        # Mock Celery task submission
        async def mock_delay(*args: object, **kwargs: object) -> object:
            mock_task = type("MockTask", (), {"id": "test-task-id"})()
            return mock_task

        # Apply monkeypatch to the imported task in md_verification module
        import nfm_db.api.v1.md_verification as md_verification_module

        monkeypatch.setattr(
            md_verification_module, "run_md_verification_task", type("obj", (object,), {"delay": mock_delay})()
        )

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.post(
                "/api/v1/md-verification/jobs",
                json={
                    "potential_id": "EAM_U_test",
                    "element_system": "U",
                    "phase": "BCC",
                    "potential_file": "/data/potentials/U.eam.alloy",
                    "structure_file": "/data/structures/U_BCC.cif",
                    "config": {
                        "temperature": 300,
                        "pressure": 0,
                        "simulation_time": 100,
                    },
                    "priority": 5,
                },
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["potential_id"] == "EAM_U_test"
        assert data["element_system"] == "U"
        assert data["status"] == JobStatus.SUBMITTED.value

        # Verify job was created in database
        service = MDVerificationService(async_session)
        job = await service.get_job(uuid.UUID(data["id"]))
        assert job is not None
        assert job.status == JobStatus.SUBMITTED

    async def test_submit_job_validation_error(self, base_url: str) -> None:
        """Test job submission with invalid data returns 400."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.post(
                "/api/v1/md-verification/jobs",
                json={
                    # Missing required fields
                    "potential_id": "EAM_U_test",
                    # Missing element_system, potential_file, structure_file
                },
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 422  # Validation error


@pytest.mark.integration
class TestListMDVerificationJobs:
    """Test GET /api/v1/md-verification/jobs endpoint."""

    async def test_list_jobs_all(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
        pending_md_job: dict[str, str],
    ) -> None:
        """Test listing all jobs returns both jobs."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                "/api/v1/md-verification/jobs",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert len(data["jobs"]) >= 2
        assert data["total"] >= 2
        assert data["limit"] == 100
        assert data["offset"] == 0

    async def test_list_jobs_filter_by_status(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
        pending_md_job: dict[str, str],
    ) -> None:
        """Test listing jobs filtered by status."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                "/api/v1/md-verification/jobs?status=completed",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert all(job["status"] == "completed" for job in data["jobs"])

    async def test_list_jobs_filter_by_element_system(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test listing jobs filtered by element system."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                "/api/v1/md-verification/jobs?element_system=U",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert all(job["element_system"] == "U" for job in data["jobs"])

    async def test_list_jobs_pagination(
        self,
        base_url: str,
    ) -> None:
        """Test listing jobs with pagination."""
        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                "/api/v1/md-verification/jobs?limit=1&offset=0",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 1
        assert data["offset"] == 0
        assert len(data["jobs"]) <= 1


@pytest.mark.integration
class TestGetMDVerificationJob:
    """Test GET /api/v1/md-verification/jobs/{id} endpoint."""

    async def test_get_job_success(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting existing job returns 200."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["potential_id"] == "test_potential"
        assert data["element_system"] == "U"

    async def test_get_job_not_found(self, base_url: str) -> None:
        """Test getting non-existent job returns 404."""
        job_id = uuid.uuid4()

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


@pytest.mark.integration
class TestGetJobStatus:
    """Test GET /api/v1/md-verification/jobs/{id}/status endpoint."""

    async def test_get_job_status_success(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting job status returns 200."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/status",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "completed"
        assert "completed_at" in data

    async def test_get_job_status_pending(
        self,
        base_url: str,
        pending_md_job: dict[str, str],
    ) -> None:
        """Test getting status of pending job returns 200."""
        job_id = pending_md_job["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/status",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["submitted_at"] is None


@pytest.mark.integration
class TestCancelMDVerificationJob:
    """Test DELETE /api/v1/md-verification/jobs/{id} endpoint."""

    async def test_cancel_pending_job_success(
        self,
        base_url: str,
        pending_md_job: dict[str, str],
        async_session: AsyncSession,
    ) -> None:
        """Test cancelling pending job returns 200."""
        job_id = pending_md_job["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.delete(
                f"/api/v1/md-verification/jobs/{job_id}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["previous_status"] == "pending"
        assert data["new_status"] == "failed"  # Cancelled jobs marked as failed
        assert "cancelled_at" in data

        # Verify job was updated in database
        service = MDVerificationService(async_session)
        job = await service.get_job(uuid.UUID(job_id))
        assert job.status == JobStatus.FAILED
        assert "cancelled" in job.error_message.lower()

    async def test_cancel_completed_job_fails(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test cancelling completed job returns 400."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.delete(
                f"/api/v1/md-verification/jobs/{job_id}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 400
        assert "cancel" in response.json()["detail"].lower()

    async def test_cancel_job_not_found(self, base_url: str) -> None:
        """Test cancelling non-existent job returns 404."""
        job_id = uuid.uuid4()

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.delete(
                f"/api/v1/md-verification/jobs/{job_id}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 404


@pytest.mark.integration
class TestGetSimulationResults:
    """Test GET /api/v1/md-verification/jobs/{id}/simulation endpoint."""

    async def test_get_simulation_results_success(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting simulation results returns 200."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/simulation",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["verification_job_id"] == job_id
        assert data["simulation_time_ps"] == 100.0
        assert data["steps_completed"] == 100000
        assert data["final_energy"] == -1000.5
        assert "thermodynamic_data" in data

    async def test_get_simulation_results_not_found(
        self,
        base_url: str,
        pending_md_job: dict[str, str],
    ) -> None:
        """Test getting simulation results for job without results returns 404."""
        job_id = pending_md_job["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/simulation",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 404
        assert "no simulation results" in response.json()["detail"].lower()


@pytest.mark.integration
class TestGetDefectAnalysisResults:
    """Test GET /api/v1/md-verification/jobs/{id}/defects endpoint."""

    async def test_get_defect_results_success(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting defect analysis results returns 200."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/defects",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["verification_job_id"] == job_id
        assert data[0]["defect_type"] == DefectType.VACANCY.value
        assert data[0]["concentration"] == 0.001
        assert data[1]["defect_type"] == DefectType.INTERSTITIAL.value

    async def test_get_defect_results_filter_by_type(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting defect results filtered by type."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/defects?defect_type=vacancy",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["defect_type"] == DefectType.VACANCY.value

    async def test_get_defect_results_empty(
        self,
        base_url: str,
        pending_md_job: dict[str, str],
    ) -> None:
        """Test getting defect results for job without results returns empty list."""
        job_id = pending_md_job["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/defects",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0


@pytest.mark.integration
class TestGetFittingResults:
    """Test GET /api/v1/md-verification/jobs/{id}/fitting endpoint."""

    async def test_get_fitting_results_success(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting fitting results returns 200."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/fitting",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["verification_job_id"] == job_id
        assert data[0]["fitting_method"] == FittingMethod.ARC_DPA.value
        assert "parameters" in data[0]
        assert data[0]["parameters"]["epsilon"] == 0.5
        assert "quality_metrics" in data[0]
        assert data[0]["quality_metrics"]["rmse"] == 0.01

    async def test_get_fitting_results_filter_by_method(
        self,
        base_url: str,
        md_job_with_results: dict[str, str],
    ) -> None:
        """Test getting fitting results filtered by method."""
        job_id = md_job_with_results["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/fitting?fitting_method=arc-dpa",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["fitting_method"] == FittingMethod.ARC_DPA.value

    async def test_get_fitting_results_empty(
        self,
        base_url: str,
        pending_md_job: dict[str, str],
    ) -> None:
        """Test getting fitting results for job without results returns empty list."""
        job_id = pending_md_job["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=base_url), base_url=base_url
        ) as client:
            response = await client.get(
                f"/api/v1/md-verification/jobs/{job_id}/fitting",
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0
