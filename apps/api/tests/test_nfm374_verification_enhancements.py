"""Tests for NFM-374 verification enhancements.

Tests the new features added by NFM-374:
- MD_CASCADE job type
- HpcBackend enum validation
- pk_energy / pk_range parameter validation
- Composite results endpoint GET /jobs/{id}/results
- Celery task database persistence
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.auth import get_current_user
from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcBackend,
    HpcJobStatus,
    JobStatus,
    JobType,
)
from nfm_db.services.md_verification import (
    MDVerificationService,
)

# ---------------------------------------------------------------------------
# Unit tests for model enums
# ---------------------------------------------------------------------------


class TestJobTypeEnum:
    """Test JobType enum values."""

    def test_lookup_exists(self) -> None:
        assert JobType.LOOKUP.value == "lookup"

    def test_md_simulation_exists(self) -> None:
        assert JobType.MD_SIMULATION.value == "md_simulation"

    def test_md_cascade_exists(self) -> None:
        assert JobType.MD_CASCADE.value == "md_cascade"


class TestHpcBackendEnum:
    """Test HpcBackend enum values."""

    def test_slurm_exists(self) -> None:
        assert HpcBackend.SLURM.value == "slurm"

    def test_pbs_exists(self) -> None:
        assert HpcBackend.PBS.value == "pbs"

    def test_local_exists(self) -> None:
        assert HpcBackend.LOCAL.value == "local"

    def test_enum_values_count(self) -> None:
        assert len(HpcBackend) == 3


# ---------------------------------------------------------------------------
# Unit tests for Pydantic validation
# ---------------------------------------------------------------------------


class TestVerifyRequestValidation:
    """Test MDVerificationJobSubmitRequest validation for NFM-374 fields."""

    def test_valid_request_with_all_optional_fields(self) -> None:
        """Test a valid request with pk_energy, pk_range, and hpc_backend."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        req = MDVerificationJobSubmitRequest(
            potential_id="EAM_U_test",
            element_system="U",
            phase="BCC",
            potential_file="/data/potentials/U.eam.alloy",
            structure_file="/data/structures/U_BCC.cif",
            config={"temperature": 300},
            priority=5,
            pk_energy_min=0.1,
            pk_energy_max=20.0,
            pk_range_min=1.0,
            pk_range_max=5.0,
            hpc_backend="slurm",
        )

        assert req.pk_energy_min == 0.1
        assert req.pk_energy_max == 20.0
        assert req.pk_range_min == 1.0
        assert req.pk_range_max == 5.0
        assert req.hpc_backend == "slurm"

    def test_invalid_pk_energy_negative(self) -> None:
        """Test that negative pk_energy raises ValidationError."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        with pytest.raises(ValidationError, match="greater than"):
            MDVerificationJobSubmitRequest(
                potential_id="EAM_U_test",
                element_system="U",
                potential_file="/data/U.eam",
                structure_file="/data/U.cif",
                config={"temperature": 300},
                pk_energy_min=-1.0,
            )

    def test_invalid_pk_range_zero(self) -> None:
        """Test that zero pk_range raises ValidationError."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        with pytest.raises(ValidationError, match="greater than"):
            MDVerificationJobSubmitRequest(
                potential_id="EAM_U_test",
                element_system="U",
                potential_file="/data/U.eam",
                structure_file="/data/U.cif",
                config={"temperature": 300},
                pk_range_min=0.0,
            )

    def test_invalid_hpc_backend(self) -> None:
        """Test that invalid hpc_backend raises ValidationError."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        with pytest.raises(ValidationError, match="hpc_backend"):
            MDVerificationJobSubmitRequest(
                potential_id="EAM_U_test",
                element_system="U",
                potential_file="/data/U.eam",
                structure_file="/data/U.cif",
                config={"temperature": 300},
                hpc_backend="invalid_cluster",
            )

    def test_pk_energy_min_exceeds_max(self) -> None:
        """Test that pk_energy_min > pk_energy_max raises ValidationError."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        with pytest.raises(ValidationError):
            MDVerificationJobSubmitRequest(
                potential_id="EAM_U_test",
                element_system="U",
                potential_file="/data/U.eam",
                structure_file="/data/U.cif",
                config={"temperature": 300},
                pk_energy_min=50.0,
                pk_energy_max=10.0,
            )

    def test_request_without_optional_fields_is_valid(self) -> None:
        """Test that request without optional fields is valid."""
        from nfm_db.api.v1.md_verification import MDVerificationJobSubmitRequest

        req = MDVerificationJobSubmitRequest(
            potential_id="EAM_U_test",
            element_system="U",
            potential_file="/data/U.eam",
            structure_file="/data/U.cif",
            config={"temperature": 300},
        )

        assert req.pk_energy_min is None
        assert req.hpc_backend is None


# ---------------------------------------------------------------------------
# Integration test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def md_cascade_job(
    db_session: AsyncSession,
    admin_user,
) -> dict[str, str]:
    """Create a completed MD_CASCADE job with full results for testing."""
    service = MDVerificationService(db_session)

    job = await service.create_job(
        {
            "potential_id": "EAM_alloy_UO2",
            "element_system": "UO2",
            "phase": "FLUORITE",
            "config": {
                "temperature": 300,
                "pk_energy_min": 0.5,
                "pk_energy_max": 15.0,
                "pk_range_min": 2.0,
                "pk_range_max": 6.0,
                "hpc_backend": "slurm",
            },
            "priority": 5,
            "status": JobStatus.COMPLETED,
            "owner_id": admin_user.id,
        }
    )

    # Create HPC job
    await service.create_hpc_job(
        {
            "verification_job_id": job.id,
            "hpc_cluster": "slurm",
            "hpc_job_id": "slurm-12345",
            "status": HpcJobStatus.COMPLETED,
            "partition": "normal",
            "nodes": 4,
            "walltime_used": 7200,
        }
    )

    # Create simulation result
    await service.create_simulation_result(
        {
            "verification_job_id": job.id,
            "trajectory_file_path": "/data/outputs/trajectory.lammpstrj",
            "thermodynamic_data": {
                "temperature": [300, 305, 310],
                "pressure": [0.0, 0.1, 0.1],
            },
            "simulation_time_ps": 50.0,
            "steps_completed": 50000,
            "final_energy": -2500.0,
            "final_temperature": 310.0,
            "final_pressure": 0.1,
        }
    )

    # Create defect results
    await service.create_defect_result(
        {
            "verification_job_id": job.id,
            "defect_type": DefectType.VACANCY,
            "concentration": 0.003,
            "formation_energy": 3.5,
            "metadata": {},
        }
    )

    await service.create_defect_result(
        {
            "verification_job_id": job.id,
            "defect_type": DefectType.INTERSTITIAL,
            "concentration": 0.005,
            "formation_energy": 4.2,
            "metadata": {},
        }
    )

    # Create fitting result
    await service.create_fitting_result(
        {
            "verification_job_id": job.id,
            "fitting_method": FittingMethod.ARC_DPA,
            "parameters": {"b": 0.75, "c": 0.85},
            "quality_metrics": {"r_squared": 0.985, "rmse": 0.008},
        }
    )

    await db_session.commit()

    return {"job_id": str(job.id)}


@pytest.fixture
async def client_with_auth(async_client: AsyncClient, admin_user):
    """Create async client with authentication override for NFM-374 tests."""
    from nfm_db.main import app

    async def override_get_current_user():
        return admin_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    yield async_client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Integration tests: Composite results endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCompositeResultsEndpoint:
    """Test GET /api/v1/md-verification/jobs/{id}/results endpoint."""

    async def test_composite_results_returns_all_data(
        self,
        client_with_auth: AsyncClient,
        md_cascade_job: dict[str, str],
    ) -> None:
        """Test composite results endpoint returns job + simulation + defects + fitting."""
        job_id = md_cascade_job["job_id"]

        response = await client_with_auth.get(
            f"/api/v1/md-verification/jobs/{job_id}/results"
        )

        assert response.status_code == 200
        data = response.json()
        assert "job" in data
        assert "simulation_result" in data
        assert "defect_results" in data
        assert "fitting_results" in data
        assert data["job"]["id"] == job_id
        assert len(data["defect_results"]) == 2
        assert len(data["fitting_results"]) == 1
        assert data["simulation_result"]["simulation_time_ps"] == 50.0

    async def test_composite_results_job_not_found(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Test composite results returns 404 for missing job."""
        job_id = uuid.uuid4()

        response = await client_with_auth.get(
            f"/api/v1/md-verification/jobs/{job_id}/results"
        )

        assert response.status_code == 404

    async def test_composite_results_no_simulation_yet(
        self,
        client_with_auth: AsyncClient,
        db_session: AsyncSession,
        admin_user,
    ) -> None:
        """Test composite results returns null simulation for pending job."""
        service = MDVerificationService(db_session)
        job = await service.create_job(
            {
                "potential_id": "test_no_results",
                "element_system": "Fe",
                "config": {"temperature": 300},
                "status": JobStatus.PENDING,
                "owner_id": admin_user.id,
            }
        )
        await db_session.commit()

        response = await client_with_auth.get(
            f"/api/v1/md-verification/jobs/{job.id}/results"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job"]["id"] == str(job.id)
        assert data["simulation_result"] is None
        assert data["defect_results"] == []
        assert data["fitting_results"] == []


# ---------------------------------------------------------------------------
# Integration tests: Submit with new fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSubmitWithCascadeFields:
    """Test POST /api/v1/md-verification/jobs with NFM-374 fields."""

    async def test_submit_with_hpc_backend(
        self,
        client_with_auth: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test job submission with hpc_backend stores the value in config."""

        def mock_delay(*args: object, **kwargs: object) -> object:
            return type("MockTask", (), {"id": "test-task-id"})()

        import nfm_db.api.v1.md_verification as md_module
        monkeypatch.setattr(
            md_module, "run_md_verification_task",
            type("obj", (object,), {"delay": mock_delay})(),
        )

        response = await client_with_auth.post(
            "/api/v1/md-verification/jobs",
            json={
                "potential_id": "EAM_UO2_test",
                "element_system": "UO2",
                "potential_file": "/data/UO2.eam",
                "structure_file": "/data/UO2.cif",
                "config": {"temperature": 600},
                "hpc_backend": "slurm",
                "pk_energy_min": 0.5,
                "pk_energy_max": 15.0,
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["config"]["hpc_backend"] == "slurm"

    async def test_submit_with_invalid_hpc_backend_rejected(
        self,
        client_with_auth: AsyncClient,
    ) -> None:
        """Test submission with invalid hpc_backend returns 422."""
        response = await client_with_auth.post(
            "/api/v1/md-verification/jobs",
            json={
                "potential_id": "EAM_UO2_test",
                "element_system": "UO2",
                "potential_file": "/data/UO2.eam",
                "structure_file": "/data/UO2.cif",
                "config": {"temperature": 300},
                "hpc_backend": "nonexistent",
            },
        )

        assert response.status_code == 422
