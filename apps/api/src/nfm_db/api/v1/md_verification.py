"""MD verification API endpoints (NFM-336).

Exposes MD verification job management endpoints:
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

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.core.auth import get_current_user
from nfm_db.database import get_db
from nfm_db.models import User
from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcBackend,
    HpcJobStatus,
    JobStatus,
)
from nfm_db.services.md_verification import (
    DefectAnalysisResultResponse,
    MDSimulationResultResponse,
    MDVerificationJobCreate,
    MDVerificationJobResponse,
    MDVerificationService,
    PotentialFittingResultResponse,
)
from nfm_db.services.rate_limit import md_verification_rate_limit

# Try to import Celery app (may not be available in all environments)
try:
    from nfm_db.services.celery_app import celery_app
    CELERY_AVAILABLE = True
except ImportError:
    celery_app = None  # type: ignore
    CELERY_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request/Response Schemas
# ---------------------------------------------------------------------------


class MDVerificationJobSubmitRequest(BaseModel):
    """Request body for POST /api/v1/md-verification/jobs."""

    potential_id: str
    element_system: str
    phase: str | None = None
    potential_file: str  # Path to potential function file
    structure_file: str  # Path to atomic structure file
    config: dict[str, str | int | float]  # Simulation parameters
    priority: int = 5

    # NFM-374: PK analysis parameters
    pk_energy_min: float | None = None
    pk_energy_max: float | None = None
    pk_range_min: float | None = None
    pk_range_max: float | None = None
    hpc_backend: HpcBackend | None = None

    @field_validator("pk_energy_min", "pk_energy_max", "pk_range_min", "pk_range_max")
    @classmethod
    def validate_positive(cls, v: float | None) -> float | None:
        """Validate that PK parameters are positive when provided."""
        if v is not None and v <= 0:
            raise ValueError("must be greater than 0")
        return v

    @model_validator(mode="after")
    def validate_pk_energy_range(self) -> MDVerificationJobSubmitRequest:
        """Validate pk_energy_min <= pk_energy_max when both are provided."""
        if (
            self.pk_energy_min is not None
            and self.pk_energy_max is not None
            and self.pk_energy_min > self.pk_energy_max
        ):
            raise ValueError("pk_energy_min must not exceed pk_energy_max")
        return self


class MDVerificationJobListResponse(BaseModel):
    """Response body for GET /api/v1/md-verification/jobs."""

    jobs: list[MDVerificationJobResponse] = []
    total: int
    limit: int
    offset: int


class JobStatusResponse(BaseModel):
    """Response body for GET /api/v1/md-verification/jobs/{id}/status."""

    job_id: uuid.UUID
    status: JobStatus
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    hpc_job_status: HpcJobStatus | None = None
    hpc_cluster: str | None = None


class CancelJobResponse(BaseModel):
    """Response body for DELETE /api/v1/md-verification/jobs/{id}."""

    job_id: uuid.UUID
    previous_status: JobStatus
    new_status: JobStatus
    cancelled_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/jobs",
    response_model=MDVerificationJobResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Submit MD verification job",
    description="Creates a new MD verification job and submits it to Celery for async execution.",
)
async def submit_md_verification_job(
    request: MDVerificationJobSubmitRequest,
    _rate: None = Depends(md_verification_rate_limit),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MDVerificationJobResponse:
    """Submit a new MD verification job.

    This endpoint:
    1. Validates request parameters
    2. Creates job record in database
    3. Submits Celery task for async execution
    4. Returns job details with status=submitted

    Args:
        request: Job submission request
        session: Database session
        current_user: Authenticated user

    Returns:
        Created job details

    Raises:
        HTTPException: If job creation or task submission fails
    """
    if not CELERY_AVAILABLE:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery task service not available",
        )

    try:
        # Merge NFM-374 optional fields into config for storage
        config = dict(request.config)
        if request.pk_energy_min is not None:
            config["pk_energy_min"] = request.pk_energy_min
        if request.pk_energy_max is not None:
            config["pk_energy_max"] = request.pk_energy_max
        if request.pk_range_min is not None:
            config["pk_range_min"] = request.pk_range_min
        if request.pk_range_max is not None:
            config["pk_range_max"] = request.pk_range_max
        if request.hpc_backend is not None:
            config["hpc_backend"] = request.hpc_backend.value

        # Create job record with ownership
        service = MDVerificationService(session)
        job = await service.create_job(
            MDVerificationJobCreate(
                potential_id=request.potential_id,
                element_system=request.element_system,
                phase=request.phase,
                config=config,
                priority=request.priority,
                status=JobStatus.PENDING,
                owner_id=current_user.id,
            )
        )

        # Submit Celery task for async execution
        try:
            task_result = celery_app.send_task(  # type: ignore
                "nfm_db.services.md_tasks.run_md_verification",
                args=[
                    str(job.id),
                    request.potential_file,
                    request.structure_file,
                    request.config,
                ],
            )

            # Update job status to SUBMITTED
            await service.update_job(
                job.id,
                {"status": JobStatus.SUBMITTED, "submitted_at": datetime.now(UTC)},
            )

            logger.info(
                f"Submitted MD verification job {job.id} "
                f"with Celery task {task_result.id}"
            )

            # Refresh job to get updated status
            updated_job = await service.get_job(job.id, owner_id=current_user.id)
            if updated_job is not None:
                job = updated_job

        except Exception as e:
            # If task submission fails, delete the job record
            await service.delete_job(job.id)
            logger.error(f"Failed to submit Celery task for job {job.id}: {e}")
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to submit job to Celery: {e!s}",
            ) from e

        return job

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to submit MD verification job")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit job: {e!s}",
        ) from e


@router.get(
    "/jobs",
    response_model=MDVerificationJobListResponse,
    status_code=http_status.HTTP_200_OK,
    summary="List MD verification jobs",
    description="List MD verification jobs with optional filters for status, potential ID, and element system.",
)
async def list_md_verification_jobs(
    potential_id: str | None = None,
    status: JobStatus | None = None,
    element_system: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MDVerificationJobListResponse:
    """List MD verification jobs with optional filters.

    Args:
        potential_id: Filter by potential ID
        status: Filter by job status
        element_system: Filter by element system
        limit: Maximum number of results
        offset: Query offset for pagination
        session: Database session
        current_user: Authenticated user

    Returns:
        Paginated list of jobs
    """
    try:
        service = MDVerificationService(session)
        jobs = await service.list_jobs(
            potential_id=potential_id,
            status=status,
            element_system=element_system,
            limit=limit,
            offset=offset,
            owner_id=current_user.id,
        )

        return MDVerificationJobListResponse(
            jobs=jobs,
            total=len(jobs),
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.exception("Failed to list MD verification jobs")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list jobs: {e!s}",
        ) from e


@router.get(
    "/jobs/{job_id}",
    response_model=MDVerificationJobResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Get MD verification job details",
    description="Get detailed information about a specific MD verification job.",
)
async def get_md_verification_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MDVerificationJobResponse:
    """Get a single MD verification job by ID.

    Args:
        job_id: Job UUID
        session: Database session
        current_user: Authenticated user

    Returns:
        Job details

    Raises:
        HTTPException: If job not found (404)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        return job

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job: {e!s}",
        ) from e


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Get MD verification job status",
    description="Get current status and execution details for a specific job.",
)
async def get_md_verification_job_status(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JobStatusResponse:
    """Get the status of an MD verification job.

    Args:
        job_id: Job UUID
        session: Database session
        current_user: Authenticated user

    Returns:
        Job status with HPC job details

    Raises:
        HTTPException: If job not found (404)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        # Get HPC job status if available
        hpc_jobs = await service.list_hpc_jobs(verification_job_id=job_id)
        hpc_job = hpc_jobs[0] if hpc_jobs else None

        return JobStatusResponse(
            job_id=job.id,
            status=job.status,
            submitted_at=job.submitted_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error_message=job.error_message,
            hpc_job_status=hpc_job.status if hpc_job else None,
            hpc_cluster=hpc_job.hpc_cluster if hpc_job else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get job status for {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job status: {e!s}",
        ) from e


@router.delete(
    "/jobs/{job_id}",
    response_model=CancelJobResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Cancel MD verification job",
    description="Cancel a pending or running MD verification job. Cannot cancel completed jobs.",
)
async def cancel_md_verification_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CancelJobResponse:
    """Cancel an MD verification job.

    Args:
        job_id: Job UUID
        session: Database session
        current_user: Authenticated user

    Returns:
        Cancellation confirmation

    Raises:
        HTTPException: If job not found (404) or cannot be cancelled (400)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        # Cannot cancel completed jobs
        if job.status == JobStatus.COMPLETED:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel completed job",
            )

        if job.status == JobStatus.FAILED:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Cannot cancel failed job",
            )

        if job.status == JobStatus.CANCELLED:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Job already cancelled",
            )

        previous_status = job.status

        # Update job status to CANCELLED
        await service.update_job(
            job_id,
            {
                "status": JobStatus.CANCELLED,
                "error_message": "Job cancelled by user",
                "completed_at": datetime.now(UTC),
            },
        )

        logger.info(f"Cancelled MD verification job {job_id}")

        return CancelJobResponse(
            job_id=job_id,
            previous_status=previous_status,
            new_status=JobStatus.CANCELLED,
            cancelled_at=datetime.now(UTC),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to cancel job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel job: {e!s}",
        ) from e


@router.get(
    "/jobs/{job_id}/simulation",
    response_model=MDSimulationResultResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Get MD simulation results",
    description="Get simulation results (thermodynamic data, trajectory) for a completed job.",
)
async def get_simulation_results(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MDSimulationResultResponse:
    """Get MD simulation results for a job.

    Args:
        job_id: Job UUID
        session: Database session
        current_user: Authenticated user

    Returns:
        Simulation results

    Raises:
        HTTPException: If job not found (404) or no results available (404)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        result = await service.get_simulation_result_by_job(job_id)

        if result is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"No simulation results available for job {job_id}",
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get simulation results for job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get simulation results: {e!s}",
        ) from e


@router.get(
    "/jobs/{job_id}/defects",
    response_model=list[DefectAnalysisResultResponse],
    status_code=http_status.HTTP_200_OK,
    summary="Get defect analysis results",
    description="Get defect analysis results (defect types, concentrations, formation energies) for a completed job.",
)
async def get_defect_analysis_results(
    job_id: uuid.UUID,
    defect_type: DefectType | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DefectAnalysisResultResponse]:
    """Get defect analysis results for a job.

    Args:
        job_id: Job UUID
        defect_type: Optional filter by defect type
        session: Database session
        current_user: Authenticated user

    Returns:
        List of defect analysis results

    Raises:
        HTTPException: If job not found (404)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        results = await service.list_defect_results(
            verification_job_id=job_id,
            defect_type=defect_type,
        )

        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get defect results for job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get defect results: {e!s}",
        ) from e


@router.get(
    "/jobs/{job_id}/fitting",
    response_model=list[PotentialFittingResultResponse],
    status_code=http_status.HTTP_200_OK,
    summary="Get potential fitting results",
    description="Get potential fitting results (parameters, quality metrics) for a completed job.",
)
async def get_fitting_results(
    job_id: uuid.UUID,
    fitting_method: FittingMethod | None = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PotentialFittingResultResponse]:
    """Get potential fitting results for a job.

    Args:
        job_id: Job UUID
        fitting_method: Optional filter by fitting method
        session: Database session
        current_user: Authenticated user

    Returns:
        List of fitting results

    Raises:
        HTTPException: If job not found (404)
    """
    try:
        service = MDVerificationService(session)
        job = await service.get_job(job_id, owner_id=current_user.id)

        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )

        results = await service.list_fitting_results_by_job(job_id)

        # Filter by fitting method if specified
        if fitting_method is not None:
            results = [r for r in results if r.fitting_method == fitting_method]

        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get fitting results for job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get fitting results: {e!s}",
        ) from e


# ---------------------------------------------------------------------------
# Composite Results Endpoint (NFM-374)
# ---------------------------------------------------------------------------


class CompositeJobResultsResponse(BaseModel):
    """Composite response containing job + all associated results."""

    job: MDVerificationJobResponse
    simulation_result: MDSimulationResultResponse | None = None
    defect_results: list[DefectAnalysisResultResponse] = []
    fitting_results: list[PotentialFittingResultResponse] = []


@router.get(
    "/jobs/{job_id}/results",
    response_model=CompositeJobResultsResponse,
    status_code=http_status.HTTP_200_OK,
    summary="Get composite job results",
    description="Get job details together with simulation, defect, and fitting results in a single call.",
)
async def get_composite_job_results(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompositeJobResultsResponse:
    """Get composite results for a job in a single API call.

    Args:
        job_id: Job UUID
        session: Database session
        current_user: Authenticated user

    Returns:
        Job details with simulation, defect, and fitting results

    Raises:
        HTTPException: If job not found (404)
    """
    try:
        service = MDVerificationService(session)
        # Verify job exists and belongs to user
        job = await service.get_job(job_id, owner_id=current_user.id)
        if job is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )
        results = await service.get_job_with_results(job_id)
        if results is None:
            # Should not happen after owner check, but handle gracefully
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} results not found",
            )

        return CompositeJobResultsResponse(
            job=results["job"],
            simulation_result=results.get("simulation_result"),
            defect_results=results.get("defect_results", []),
            fitting_results=results.get("fitting_results", []),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get composite results for job {job_id}")
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get composite results: {e!s}",
        ) from e


@router.get(
    "/health",
    status_code=http_status.HTTP_200_OK,
    summary="MD verification module health check",
)
async def md_verification_health() -> dict[str, str]:
    """Health check for the MD verification module."""
    return {
        "status": "healthy",
        "module": "md-verification",
        "celery_available": "true" if CELERY_AVAILABLE else "false",
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }
