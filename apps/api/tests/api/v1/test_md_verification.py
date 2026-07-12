"""Integration tests for /api/v1/md-verification endpoints (NFM-336).

Tests all MD verification endpoints with mocked MDVerificationService
and Celery task, verifying HTTP layer behaviour including request validation,
response serialization, authentication, and error handling.

Endpoints under prefix ``/api/v1/md-verification``:
- POST   /jobs                       Submit MD verification job
- GET    /jobs                       List jobs with filters
- GET    /jobs/{job_id}              Get job details
- GET    /jobs/{job_id}/status       Get job + HPC status
- DELETE /jobs/{job_id}              Cancel job
- GET    /jobs/{job_id}/simulation   Get simulation results
- GET    /jobs/{job_id}/defects      Get defect analysis
- GET    /jobs/{job_id}/fitting      Get fitting results
- GET    /jobs/{job_id}/results      Get composite results
- GET    /health                     Module health check
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.core.auth import get_current_user
from nfm_db.main import app
from nfm_db.models import User
from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcJobStatus,
    JobStatus,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "/api/v1/md-verification"

MOCK_CELERY_TASK_ID = "mock-celery-task-abc123"

SUBMIT_PAYLOAD: dict = {
    "potential_id": "EAM_alloy_U",
    "element_system": "U",
    "phase": "BCC",
    "potential_file": "/data/potentials/U.eam.alloy",
    "structure_file": "/data/structures/U_bcc.dat",
    "config": {"temperature": 300, "pressure": 0},
    "priority": 5,
}


# ---------------------------------------------------------------------------
# Auth fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client_with_auth(async_client, admin_user: User):
    """AsyncClient with ``get_current_user`` overridden to return admin_user."""
    async def _override():
        return admin_user

    app.dependency_overrides[get_current_user] = _override
    yield async_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_job_response(**overrides) -> MagicMock:
    """Build a MagicMock that quacks like ``MDVerificationJobResponse``."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "potential_id": "EAM_alloy_U",
        "element_system": "U",
        "phase": "BCC",
        "status": JobStatus.PENDING,
        "config": {"temperature": 300},
        "priority": 5,
        "submitted_at": None,
        "started_at": None,
        "completed_at": None,
        "error_message": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


def _make_hpc_job(**overrides) -> MagicMock:
    """Build a MagicMock resembling an HPC job record."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "verification_job_id": uuid.uuid4(),
        "status": HpcJobStatus.RUNNING,
        "hpc_cluster": "slurm-local",
        "hpc_job_id": "12345",
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


def _make_simulation_result(**overrides) -> MagicMock:
    """Build a MagicMock resembling ``MDSimulationResultResponse``."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "verification_job_id": uuid.uuid4(),
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
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


def _make_defect_result(**overrides) -> MagicMock:
    """Build a MagicMock resembling ``DefectAnalysisResultResponse``."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "verification_job_id": uuid.uuid4(),
        "defect_type": DefectType.VACANCY,
        "concentration": 0.001,
        "formation_energy": 1.5,
        "metadata": {"analysis_method": "Wigner-Seitz"},
        "analysis_metadata": {"analysis_method": "Wigner-Seitz"},
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


def _make_fitting_result(**overrides) -> MagicMock:
    """Build a MagicMock resembling ``PotentialFittingResultResponse``."""
    defaults: dict = {
        "id": uuid.uuid4(),
        "verification_job_id": uuid.uuid4(),
        "fitting_method": FittingMethod.ARC_DPA,
        "parameters": {"epsilon": 0.5, "sigma": 2.5, "a": 1.0, "q": 0.0},
        "quality_metrics": {"rmse": 0.01, "r_squared": 0.99},
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    mock = MagicMock()
    for key, value in defaults.items():
        setattr(mock, key, value)
    return mock


# ===========================================================================
# POST /api/v1/md-verification/jobs
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.celery_app")
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_submit_job_success(
    mock_service_cls: MagicMock,
    mock_celery_app: MagicMock,
    client_with_auth,
) -> None:
    """Successful job submission returns 201 with job details."""
    job_id = uuid.uuid4()
    created_job = _make_job_response(id=job_id, status=JobStatus.PENDING)
    submitted_job = _make_job_response(
        id=job_id,
        status=JobStatus.SUBMITTED,
        submitted_at=datetime.now(UTC),
    )

    mock_instance = AsyncMock()
    mock_instance.create_job = AsyncMock(return_value=created_job)
    mock_instance.update_job = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=submitted_job)
    mock_instance.delete_job = AsyncMock(return_value=True)
    mock_service_cls.return_value = mock_instance

    mock_celery_app.send_task = MagicMock(
        return_value=MagicMock(id=MOCK_CELERY_TASK_ID)
    )

    with patch("nfm_db.api.v1.md_verification.CELERY_AVAILABLE", True):
        response = await client_with_auth.post(f"{BASE_URL}/jobs", json=SUBMIT_PAYLOAD)

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "submitted"
    assert data["potential_id"] == "EAM_alloy_U"
    assert data["element_system"] == "U"

    mock_instance.create_job.assert_awaited_once()
    mock_instance.update_job.assert_awaited_once()
    mock_instance.get_job.assert_awaited_once()
    mock_celery_app.send_task.assert_called_once()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.celery_app")
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_submit_job_with_pk_parameters(
    mock_service_cls: MagicMock,
    mock_celery_app: MagicMock,
    client_with_auth,
) -> None:
    """Job submission merges NFM-374 PK analysis parameters into config."""
    job_id = uuid.uuid4()
    created_job = _make_job_response(id=job_id, status=JobStatus.PENDING)
    submitted_job = _make_job_response(
        id=job_id,
        status=JobStatus.SUBMITTED,
        submitted_at=datetime.now(UTC),
    )

    mock_instance = AsyncMock()
    mock_instance.create_job = AsyncMock(return_value=created_job)
    mock_instance.update_job = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=submitted_job)
    mock_service_cls.return_value = mock_instance

    mock_celery_app.send_task = MagicMock(
        return_value=MagicMock(id=MOCK_CELERY_TASK_ID)
    )

    payload = {
        **SUBMIT_PAYLOAD,
        "pk_energy_min": 1.0,
        "pk_energy_max": 100.0,
        "pk_range_min": 0.5,
        "pk_range_max": 10.0,
        "hpc_backend": "slurm",
    }

    with patch("nfm_db.api.v1.md_verification.CELERY_AVAILABLE", True):
        response = await client_with_auth.post(f"{BASE_URL}/jobs", json=payload)

    assert response.status_code == 201

    # Verify that the config passed to create_job includes PK params
    create_call = mock_instance.create_job.call_args
    create_data = create_call[0][0]
    assert "pk_energy_min" in create_data.config
    assert create_data.config["pk_energy_min"] == 1.0
    assert create_data.config["hpc_backend"] == "slurm"


@pytest.mark.asyncio
async def test_submit_job_celery_unavailable(client_with_auth) -> None:
    """Returns 503 when Celery is not available."""
    with patch("nfm_db.api.v1.md_verification.CELERY_AVAILABLE", False):
        response = await client_with_auth.post(f"{BASE_URL}/jobs", json=SUBMIT_PAYLOAD)

    assert response.status_code == 503
    assert "celery" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_submit_job_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    response = await async_client.post(f"{BASE_URL}/jobs", json=SUBMIT_PAYLOAD)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_submit_job_validation_error(client_with_auth) -> None:
    """Returns 422 for malformed request body."""
    payload = {"element_system": "U"}
    response = await client_with_auth.post(f"{BASE_URL}/jobs", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_job_negative_pk_energy_rejected(client_with_auth) -> None:
    """Returns 422 when pk_energy_min is negative."""
    payload = {
        **SUBMIT_PAYLOAD,
        "pk_energy_min": -1.0,
    }
    response = await client_with_auth.post(f"{BASE_URL}/jobs", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_job_pk_energy_range_inverted(client_with_auth) -> None:
    """Returns 422 when pk_energy_min > pk_energy_max."""
    payload = {
        **SUBMIT_PAYLOAD,
        "pk_energy_min": 100.0,
        "pk_energy_max": 1.0,
    }
    response = await client_with_auth.post(f"{BASE_URL}/jobs", json=payload)
    assert response.status_code == 422


# ===========================================================================
# GET /api/v1/md-verification/jobs
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_list_jobs_empty(mock_service_cls: MagicMock, client_with_auth) -> None:
    """Returns 200 with empty list when no jobs exist."""
    mock_instance = AsyncMock()
    mock_instance.list_jobs = AsyncMock(return_value=[])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs")

    assert response.status_code == 200
    data = response.json()
    assert data["jobs"] == []
    assert data["total"] == 0
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_list_jobs_with_filters(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Passes query params through to the service layer."""
    job = _make_job_response(
        id=uuid.uuid4(),
        element_system="U",
        status=JobStatus.COMPLETED,
    )
    mock_instance = AsyncMock()
    mock_instance.list_jobs = AsyncMock(return_value=[job])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(
        f"{BASE_URL}/jobs",
        params={
            "element_system": "U",
            "status": "completed",
            "potential_id": "EAM_alloy_U",
            "limit": 10,
            "offset": 5,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 1
    assert data["total"] == 1
    assert data["limit"] == 10
    assert data["offset"] == 5

    mock_instance.list_jobs.assert_awaited_once()
    call_kwargs = mock_instance.list_jobs.call_args[1]
    assert call_kwargs["potential_id"] == "EAM_alloy_U"
    assert call_kwargs["status"] == JobStatus.COMPLETED
    assert call_kwargs["element_system"] == "U"
    assert call_kwargs["limit"] == 10
    assert call_kwargs["offset"] == 5
    assert "owner_id" in call_kwargs


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_list_jobs_pagination_defaults(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Uses default limit=20, offset=0 when not specified (matches main's per_page=20 default)."""
    mock_instance = AsyncMock()
    mock_instance.list_jobs = AsyncMock(return_value=[])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs")
    assert response.status_code == 200

    call_kwargs = mock_instance.list_jobs.call_args[1]
    assert call_kwargs["limit"] == 20
    assert call_kwargs["offset"] == 0


@pytest.mark.asyncio
async def test_list_jobs_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    response = await async_client.get(f"{BASE_URL}/jobs")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_job_success(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with job details."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.RUNNING)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(job_id)
    assert data["status"] == "running"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_job_not_found(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_job_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}/status
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_status_with_hpc_info(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with HPC status when HPC jobs are present."""
    job_id = uuid.uuid4()
    job = _make_job_response(
        id=job_id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    hpc_job = _make_hpc_job(
        verification_job_id=job_id,
        status=HpcJobStatus.RUNNING,
        hpc_cluster="slurm-local",
    )

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_hpc_jobs = AsyncMock(return_value=[hpc_job])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/status")

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["status"] == "running"
    assert data["hpc_job_status"] == "running"
    assert data["hpc_cluster"] == "slurm-local"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_status_without_hpc_info(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with null HPC fields when no HPC jobs exist."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.PENDING)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_hpc_jobs = AsyncMock(return_value=[])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/status")

    assert response.status_code == 200
    data = response.json()
    assert data["hpc_job_status"] is None
    assert data["hpc_cluster"] is None


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_status_job_not_found(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/status")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}/status")
    assert response.status_code == 401


# ===========================================================================
# DELETE /api/v1/md-verification/jobs/{job_id}
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_pending_job(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 when cancelling a PENDING job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.PENDING)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.update_job = AsyncMock()
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["previous_status"] == "pending"
    assert data["new_status"] == "cancelled"
    assert "cancelled_at" in data

    mock_instance.update_job.assert_awaited_once()
    update_args = mock_instance.update_job.call_args
    assert update_args[0][0] == job_id
    assert update_args[0][1]["status"] == JobStatus.CANCELLED


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_running_job(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 when cancelling a RUNNING job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.RUNNING)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.update_job = AsyncMock()
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["previous_status"] == "running"
    assert data["new_status"] == "cancelled"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_submitted_job(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 when cancelling a SUBMITTED job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.SUBMITTED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.update_job = AsyncMock()
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["previous_status"] == "submitted"


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_completed_job_returns_400(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 400 when trying to cancel a COMPLETED job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 400
    assert "cancel" in response.json()["detail"].lower()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_failed_job_returns_400(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 400 when trying to cancel a FAILED job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.FAILED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 400


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_already_cancelled_job_returns_400(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 400 when trying to cancel an already CANCELLED job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.CANCELLED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")

    assert response.status_code == 400
    assert "already" in response.json()["detail"].lower()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_cancel_job_not_found_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.delete(f"{BASE_URL}/jobs/{job_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cancel_job_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.delete(f"{BASE_URL}/jobs/{job_id}")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}/simulation
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_simulation_success(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with simulation result data."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    sim_result = _make_simulation_result(verification_job_id=job_id)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.get_simulation_result_by_job = AsyncMock(return_value=sim_result)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/simulation")

    assert response.status_code == 200


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_simulation_no_results_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when no simulation results exist for the job."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.get_simulation_result_by_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/simulation")

    assert response.status_code == 404
    assert "no simulation results" in response.json()["detail"].lower()


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_simulation_job_not_found_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/simulation")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_simulation_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}/simulation")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}/defects
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_defects_success(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with list of defect results."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    defect1 = _make_defect_result(
        verification_job_id=job_id, defect_type=DefectType.VACANCY
    )
    defect2 = _make_defect_result(
        verification_job_id=job_id, defect_type=DefectType.INTERSTITIAL
    )

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_defect_results = AsyncMock(return_value=[defect1, defect2])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/defects")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_defects_empty(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with empty list when no defect results exist."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_defect_results = AsyncMock(return_value=[])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/defects")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_defects_with_filter(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Passes defect_type query param through to service."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    defect = _make_defect_result(
        verification_job_id=job_id, defect_type=DefectType.VACANCY
    )

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_defect_results = AsyncMock(return_value=[defect])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(
        f"{BASE_URL}/jobs/{job_id}/defects",
        params={"defect_type": "vacancy"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    mock_instance.list_defect_results.assert_awaited_once_with(
        verification_job_id=job_id,
        defect_type=DefectType.VACANCY,
    )


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_defects_job_not_found_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/defects")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_defects_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}/defects")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}/fitting
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_fitting_success(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with list of fitting results."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    fitting = _make_fitting_result(
        verification_job_id=job_id, fitting_method=FittingMethod.ARC_DPA
    )

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_fitting_results_by_job = AsyncMock(return_value=[fitting])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/fitting")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_fitting_with_filter(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Filters fitting results by fitting_method in Python."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    fitting_arc = _make_fitting_result(
        verification_job_id=job_id, fitting_method=FittingMethod.ARC_DPA
    )
    fitting_rpa = _make_fitting_result(
        verification_job_id=job_id, fitting_method=FittingMethod.RPA
    )

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_fitting_results_by_job = AsyncMock(
        return_value=[fitting_arc, fitting_rpa]
    )
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(
        f"{BASE_URL}/jobs/{job_id}/fitting",
        params={"fitting_method": "arc-dpa"},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_fitting_empty(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with empty list when no fitting results exist."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.list_fitting_results_by_job = AsyncMock(return_value=[])
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/fitting")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_get_fitting_job_not_found_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/fitting")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_fitting_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}/fitting")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/jobs/{job_id}/results (composite)
# ===========================================================================


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_composite_results_success(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with job + all associated results."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.COMPLETED)
    sim = _make_simulation_result(verification_job_id=job_id)
    defect = _make_defect_result(verification_job_id=job_id)
    fitting = _make_fitting_result(verification_job_id=job_id)

    composite_data: dict = {
        "job": job,
        "simulation_result": sim,
        "defect_results": [defect],
        "fitting_results": [fitting],
    }

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.get_job_with_results = AsyncMock(return_value=composite_data)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/results")

    assert response.status_code == 200
    data = response.json()
    assert "job" in data
    assert "simulation_result" in data
    assert "defect_results" in data
    assert "fitting_results" in data


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_composite_results_partial_data(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 200 with None/empty for missing result types."""
    job_id = uuid.uuid4()
    job = _make_job_response(id=job_id, status=JobStatus.RUNNING)

    composite_data: dict = {
        "job": job,
        "simulation_result": None,
        "defect_results": [],
        "fitting_results": [],
    }

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=job)
    mock_instance.get_job_with_results = AsyncMock(return_value=composite_data)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/results")

    assert response.status_code == 200
    data = response.json()
    assert data["simulation_result"] is None
    assert data["defect_results"] == []
    assert data["fitting_results"] == []


@pytest.mark.asyncio
@patch("nfm_db.api.v1.md_verification.MDVerificationService")
async def test_composite_results_job_not_found_returns_404(
    mock_service_cls: MagicMock, client_with_auth
) -> None:
    """Returns 404 when the job does not exist."""
    job_id = uuid.uuid4()

    mock_instance = AsyncMock()
    mock_instance.get_job = AsyncMock(return_value=None)
    mock_service_cls.return_value = mock_instance

    response = await client_with_auth.get(f"{BASE_URL}/jobs/{job_id}/results")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_composite_results_unauthenticated(async_client) -> None:
    """Returns 401 when no auth token is provided."""
    job_id = uuid.uuid4()
    response = await async_client.get(f"{BASE_URL}/jobs/{job_id}/results")
    assert response.status_code == 401


# ===========================================================================
# GET /api/v1/md-verification/health
# ===========================================================================


@pytest.mark.asyncio
async def test_health_returns_healthy(async_client) -> None:
    """Returns 200 with status 'healthy' and module name."""
    response = await async_client.get(f"{BASE_URL}/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["module"] == "md-verification"
    assert "version" in data
    assert "timestamp" in data
    assert "celery_available" in data


@pytest.mark.asyncio
async def test_health_no_auth_required(async_client) -> None:
    """Health endpoint does not require authentication."""
    response = await async_client.get(f"{BASE_URL}/health")
    assert response.status_code == 200
