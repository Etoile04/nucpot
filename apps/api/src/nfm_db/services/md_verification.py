"""MD verification service: CRUD operations for MD verification entities.

Phase 2.2 of NFM-313: Service layer for MD verification backend integration.
Provides async CRUD operations for all 5 MD verification tables.

Usage:
    from nfm_db.services.md_verification import MDVerificationService
    from sqlalchemy.ext.asyncio import AsyncSession

    svc = MDVerificationService(session)
    job = await svc.create_job({
        "potential_id": "EAM_alloy_U",
        "element_system": "U",
        "phase": "BCC",
        "config": {...},
    })
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.md_verification import (
    DefectAnalysisResult,
    DefectType,
    FittingMethod,
    HpcJob,
    HpcJobStatus,
    JobStatus,
    MDVerificationJob,
    MDSimulationResult,
    PotentialFittingResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Schemas for API Responses
# =============================================================================


class MDVerificationJobCreate(BaseModel):
    """Schema for creating a new MD verification job."""

    potential_id: str
    element_system: str
    phase: str | None = None
    config: dict[str, Any]
    priority: int = 5
    status: JobStatus = JobStatus.PENDING
    owner_id: uuid.UUID | None = None


class MDVerificationJobUpdate(BaseModel):
    """Schema for updating an existing MD verification job."""

    status: JobStatus | None = None
    priority: int | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    config: dict[str, Any] | None = None


class MDVerificationJobResponse(BaseModel):
    """Schema for MD verification job API response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID | None
    potential_id: str
    element_system: str
    phase: str | None
    config: dict[str, Any]
    status: JobStatus
    priority: int
    submitted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class HpcJobCreate(BaseModel):
    """Schema for creating a new HPC job."""

    verification_job_id: uuid.UUID
    hpc_cluster: str
    hpc_job_id: str | None = None
    status: HpcJobStatus = HpcJobStatus.PENDING
    partition: str | None = None
    nodes: int | None = None
    walltime_requested: int | None = None
    submission_output: str | None = None
    submission_errors: str | None = None


class HpcJobUpdate(BaseModel):
    """Schema for updating an existing HPC job."""

    hpc_job_id: str | None = None
    status: HpcJobStatus | None = None
    partition: str | None = None
    nodes: int | None = None
    walltime_requested: int | None = None
    walltime_used: int | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    submission_output: str | None = None
    submission_errors: str | None = None


class HpcJobResponse(BaseModel):
    """Schema for HPC job API response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verification_job_id: uuid.UUID
    hpc_cluster: str
    hpc_job_id: str | None
    status: HpcJobStatus
    partition: str | None
    nodes: int | None
    walltime_requested: int | None
    walltime_used: int | None
    submitted_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    submission_output: str | None
    submission_errors: str | None
    created_at: datetime
    updated_at: datetime


class MDSimulationResultCreate(BaseModel):
    """Schema for creating MD simulation results."""

    verification_job_id: uuid.UUID
    trajectory_file_path: str | None = None
    thermodynamic_data: dict[str, Any] | None = None
    simulation_time_ps: float | None = None
    steps_completed: int | None = None
    final_energy: float | None = None
    final_temperature: float | None = None
    final_pressure: float | None = None


class MDSimulationResultUpdate(BaseModel):
    """Schema for updating MD simulation results."""

    trajectory_file_path: str | None = None
    thermodynamic_data: dict[str, Any] | None = None
    simulation_time_ps: float | None = None
    steps_completed: int | None = None
    final_energy: float | None = None
    final_temperature: float | None = None
    final_pressure: float | None = None


class MDSimulationResultResponse(BaseModel):
    """Schema for MD simulation result API response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verification_job_id: uuid.UUID
    trajectory_file_path: str | None
    thermodynamic_data: dict[str, Any] | None
    simulation_time_ps: float | None
    steps_completed: int | None
    final_energy: float | None
    final_temperature: float | None
    final_pressure: float | None
    created_at: datetime


class DefectAnalysisResultCreate(BaseModel):
    """Schema for creating defect analysis results."""

    verification_job_id: uuid.UUID
    defect_type: DefectType
    concentration: float
    formation_energy: float | None = None
    metadata: dict[str, Any] | None = None  # Maps to analysis_metadata column


class DefectAnalysisResultUpdate(BaseModel):
    """Schema for updating defect analysis results."""

    concentration: float | None = None
    formation_energy: float | None = None
    metadata: dict[str, Any] | None = None


class DefectAnalysisResultResponse(BaseModel):
    """Schema for defect analysis result API response.

    Note: The database model uses 'analysis_metadata' as the column name to avoid
    conflicts with SQLAlchemy's internal metadata, but the API schema exposes
    this as 'metadata' for consistency.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: uuid.UUID
    verification_job_id: uuid.UUID
    defect_type: DefectType
    concentration: float
    formation_energy: float | None
    metadata: dict[str, Any] = Field(
        default=None,
        validation_alias="analysis_metadata",
        serialization_alias="metadata",
    )


class PotentialFittingResultCreate(BaseModel):
    """Schema for creating potential fitting results."""

    verification_job_id: uuid.UUID
    fitting_method: FittingMethod
    parameters: dict[str, Any]
    quality_metrics: dict[str, Any] | None = None


class PotentialFittingResultUpdate(BaseModel):
    """Schema for updating potential fitting results."""

    parameters: dict[str, Any] | None = None
    quality_metrics: dict[str, Any] | None = None


class PotentialFittingResultResponse(BaseModel):
    """Schema for potential fitting result API response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    verification_job_id: uuid.UUID
    fitting_method: FittingMethod
    parameters: dict[str, Any]
    quality_metrics: dict[str, Any] | None
    created_at: datetime


# =============================================================================
# Service Layer
# =============================================================================


class MDVerificationService:
    """Service for MD verification CRUD operations.

    Provides async database operations for all MD verification entities.
    All methods return Pydantic schemas suitable for API responses.

    Usage:
        svc = MDVerificationService(session)
        job = await svc.create_job({
            "potential_id": "EAM_alloy_U",
            "element_system": "U",
            "phase": "BCC",
            "config": {"temperature": 300, "pressure": 0},
        })
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the service with a database session.

        Args:
            session: SQLAlchemy async session
        """
        self._session = session

    # -------------------------------------------------------------------------
    # MD Verification Job CRUD
    # -------------------------------------------------------------------------

    async def create_job(
        self,
        data: MDVerificationJobCreate | dict[str, Any],
    ) -> MDVerificationJobResponse:
        """Create a new MD verification job.

        Args:
            data: Job creation data

        Returns:
            Created job as response schema
        """
        if isinstance(data, dict):
            data = MDVerificationJobCreate(**data)

        job = MDVerificationJob(
            potential_id=data.potential_id,
            element_system=data.element_system,
            phase=data.phase,
            config=data.config,
            priority=data.priority,
            status=data.status,
            owner_id=data.owner_id,
        )

        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)

        logger.info(f"Created MD verification job {job.id}")
        return MDVerificationJobResponse.model_validate(job)

    async def get_job(
        self,
        job_id: uuid.UUID,
        owner_id: uuid.UUID | None = None,
    ) -> MDVerificationJobResponse | None:
        """Get a single MD verification job by ID.

        Args:
            job_id: Job UUID
            owner_id: If provided, only return job if owned by this user

        Returns:
            Job as response schema, or None if not found
        """
        query = select(MDVerificationJob).where(MDVerificationJob.id == job_id)

        if owner_id is not None:
            query = query.where(MDVerificationJob.owner_id == owner_id)

        result = await self._session.execute(query)
        job = result.scalar_one_or_none()

        if job is None:
            return None

        return MDVerificationJobResponse.model_validate(job)

    async def list_jobs(
        self,
        potential_id: str | None = None,
        status: JobStatus | None = None,
        element_system: str | None = None,
        limit: int = 100,
        offset: int = 0,
        owner_id: uuid.UUID | None = None,
    ) -> list[MDVerificationJobResponse]:
        """List MD verification jobs with optional filters.

        Args:
            potential_id: Filter by potential ID
            status: Filter by job status
            element_system: Filter by element system
            limit: Maximum number of results
            offset: Query offset for pagination
            owner_id: If provided, only return jobs owned by this user

        Returns:
            List of jobs as response schemas
        """
        query = select(MDVerificationJob)

        if owner_id is not None:
            query = query.where(MDVerificationJob.owner_id == owner_id)

        if potential_id is not None:
            query = query.where(MDVerificationJob.potential_id == potential_id)

        if status is not None:
            query = query.where(MDVerificationJob.status == status)

        if element_system is not None:
            query = query.where(MDVerificationJob.element_system == element_system)

        query = query.order_by(MDVerificationJob.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        jobs = result.scalars().all()

        return [
            MDVerificationJobResponse.model_validate(job)
            for job in jobs
        ]

    async def update_job(
        self,
        job_id: uuid.UUID,
        data: MDVerificationJobUpdate | dict[str, Any],
    ) -> MDVerificationJobResponse | None:
        """Update an existing MD verification job.

        Args:
            job_id: Job UUID
            data: Update data

        Returns:
            Updated job as response schema, or None if not found
        """
        if isinstance(data, dict):
            data = MDVerificationJobUpdate(**data)

        # Build update dict with only non-None fields
        update_values = {
            k: v
            for k, v in {
                "status": data.status,
                "priority": data.priority,
                "submitted_at": data.submitted_at,
                "started_at": data.started_at,
                "completed_at": data.completed_at,
                "error_message": data.error_message,
                "config": data.config,
            }.items()
            if v is not None
        }

        if not update_values:
            # No updates to apply, return existing job
            return await self.get_job(job_id)

        await self._session.execute(
            update(MDVerificationJob)
            .where(MDVerificationJob.id == job_id)
            .values(**update_values),
        )
        await self._session.flush()

        logger.info(f"Updated MD verification job {job_id}")
        return await self.get_job(job_id)

    async def delete_job(
        self,
        job_id: uuid.UUID,
    ) -> bool:
        """Delete an MD verification job.

        Due to CASCADE foreign keys, this will also delete all related
        HPC jobs, simulation results, defect analysis results, and fitting results.

        Args:
            job_id: Job UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(MDVerificationJob).where(MDVerificationJob.id == job_id),
        )

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted MD verification job {job_id}")

        return deleted

    # -------------------------------------------------------------------------
    # HPC Job CRUD
    # -------------------------------------------------------------------------

    async def create_hpc_job(
        self,
        data: HpcJobCreate | dict[str, Any],
    ) -> HpcJobResponse:
        """Create a new HPC job.

        Args:
            data: HPC job creation data

        Returns:
            Created HPC job as response schema
        """
        if isinstance(data, dict):
            data = HpcJobCreate(**data)

        job = HpcJob(
            verification_job_id=data.verification_job_id,
            hpc_cluster=data.hpc_cluster,
            hpc_job_id=data.hpc_job_id,
            status=data.status,
            partition=data.partition,
            nodes=data.nodes,
            walltime_requested=data.walltime_requested,
            submission_output=data.submission_output,
            submission_errors=data.submission_errors,
        )

        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)

        logger.info(f"Created HPC job {job.id}")
        return HpcJobResponse.model_validate(job)

    async def get_hpc_job(
        self,
        hpc_job_id: uuid.UUID,
    ) -> HpcJobResponse | None:
        """Get a single HPC job by ID.

        Args:
            hpc_job_id: HPC job UUID

        Returns:
            HPC job as response schema, or None if not found
        """
        result = await self._session.execute(
            select(HpcJob).where(HpcJob.id == hpc_job_id),
        )
        job = result.scalar_one_or_none()

        if job is None:
            return None

        return HpcJobResponse.model_validate(job)

    async def list_hpc_jobs(
        self,
        verification_job_id: uuid.UUID | None = None,
        hpc_cluster: str | None = None,
        status: HpcJobStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[HpcJobResponse]:
        """List HPC jobs with optional filters.

        Args:
            verification_job_id: Filter by verification job ID
            hpc_cluster: Filter by HPC cluster
            status: Filter by job status
            limit: Maximum number of results
            offset: Query offset for pagination

        Returns:
            List of HPC jobs as response schemas
        """
        query = select(HpcJob)

        if verification_job_id is not None:
            query = query.where(HpcJob.verification_job_id == verification_job_id)

        if hpc_cluster is not None:
            query = query.where(HpcJob.hpc_cluster == hpc_cluster)

        if status is not None:
            query = query.where(HpcJob.status == status)

        query = query.order_by(HpcJob.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        jobs = result.scalars().all()

        return [HpcJobResponse.model_validate(job) for job in jobs]

    async def update_hpc_job(
        self,
        hpc_job_id: uuid.UUID,
        data: HpcJobUpdate | dict[str, Any],
    ) -> HpcJobResponse | None:
        """Update an existing HPC job.

        Args:
            hpc_job_id: HPC job UUID
            data: Update data

        Returns:
            Updated HPC job as response schema, or None if not found
        """
        if isinstance(data, dict):
            data = HpcJobUpdate(**data)

        update_values = {
            k: v
            for k, v in {
                "hpc_job_id": data.hpc_job_id,
                "status": data.status,
                "partition": data.partition,
                "nodes": data.nodes,
                "walltime_requested": data.walltime_requested,
                "walltime_used": data.walltime_used,
                "submitted_at": data.submitted_at,
                "started_at": data.started_at,
                "completed_at": data.completed_at,
                "submission_output": data.submission_output,
                "submission_errors": data.submission_errors,
            }.items()
            if v is not None
        }

        if not update_values:
            return await self.get_hpc_job(hpc_job_id)

        await self._session.execute(
            update(HpcJob).where(HpcJob.id == hpc_job_id).values(**update_values),
        )
        await self._session.flush()

        logger.info(f"Updated HPC job {hpc_job_id}")
        return await self.get_hpc_job(hpc_job_id)

    async def delete_hpc_job(
        self,
        hpc_job_id: uuid.UUID,
    ) -> bool:
        """Delete an HPC job.

        Args:
            hpc_job_id: HPC job UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(HpcJob).where(HpcJob.id == hpc_job_id),
        )

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted HPC job {hpc_job_id}")

        return deleted

    # -------------------------------------------------------------------------
    # MD Simulation Result CRUD
    # -------------------------------------------------------------------------

    async def create_simulation_result(
        self,
        data: MDSimulationResultCreate | dict[str, Any],
    ) -> MDSimulationResultResponse:
        """Create MD simulation results.

        Args:
            data: Simulation result creation data

        Returns:
            Created simulation result as response schema
        """
        if isinstance(data, dict):
            data = MDSimulationResultCreate(**data)

        result = MDSimulationResult(
            verification_job_id=data.verification_job_id,
            trajectory_file_path=data.trajectory_file_path,
            thermodynamic_data=data.thermodynamic_data,
            simulation_time_ps=data.simulation_time_ps,
            steps_completed=data.steps_completed,
            final_energy=data.final_energy,
            final_temperature=data.final_temperature,
            final_pressure=data.final_pressure,
        )

        self._session.add(result)
        await self._session.flush()
        await self._session.refresh(result)

        logger.info(f"Created simulation result {result.id}")
        return MDSimulationResultResponse.model_validate(result)

    async def get_simulation_result(
        self,
        result_id: uuid.UUID,
    ) -> MDSimulationResultResponse | None:
        """Get MD simulation results by ID.

        Args:
            result_id: Result UUID

        Returns:
            Simulation result as response schema, or None if not found
        """
        result = await self._session.execute(
            select(MDSimulationResult).where(
                MDSimulationResult.id == result_id,
            ),
        )
        sim_result = result.scalar_one_or_none()

        if sim_result is None:
            return None

        return MDSimulationResultResponse.model_validate(sim_result)

    async def update_simulation_result(
        self,
        result_id: uuid.UUID,
        data: MDSimulationResultUpdate | dict[str, Any],
    ) -> MDSimulationResultResponse | None:
        """Update MD simulation results.

        Args:
            result_id: Result UUID
            data: Update data

        Returns:
            Updated simulation result as response schema, or None if not found
        """
        if isinstance(data, dict):
            data = MDSimulationResultUpdate(**data)

        update_values = {
            k: v
            for k, v in {
                "trajectory_file_path": data.trajectory_file_path,
                "thermodynamic_data": data.thermodynamic_data,
                "simulation_time_ps": data.simulation_time_ps,
                "steps_completed": data.steps_completed,
                "final_energy": data.final_energy,
                "final_temperature": data.final_temperature,
                "final_pressure": data.final_pressure,
            }.items()
            if v is not None
        }

        if not update_values:
            return await self.get_simulation_result(result_id)

        await self._session.execute(
            update(MDSimulationResult)
            .where(MDSimulationResult.id == result_id)
            .values(**update_values),
        )
        await self._session.flush()

        logger.info(f"Updated simulation result {result_id}")
        return await self.get_simulation_result(result_id)

    async def delete_simulation_result(
        self,
        result_id: uuid.UUID,
    ) -> bool:
        """Delete MD simulation results.

        Args:
            result_id: Result UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(MDSimulationResult).where(
                MDSimulationResult.id == result_id,
            ),
        )

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted simulation result {result_id}")

        return deleted

    # -------------------------------------------------------------------------
    # Defect Analysis Result CRUD
    # -------------------------------------------------------------------------

    async def create_defect_result(
        self,
        data: DefectAnalysisResultCreate | dict[str, Any],
    ) -> DefectAnalysisResultResponse:
        """Create defect analysis results.

        Args:
            data: Defect result creation data

        Returns:
            Created defect result as response schema
        """
        if isinstance(data, dict):
            data = DefectAnalysisResultCreate(**data)

        result = DefectAnalysisResult(
            verification_job_id=data.verification_job_id,
            defect_type=data.defect_type,
            concentration=data.concentration,
            formation_energy=data.formation_energy,
            analysis_metadata=data.metadata,
        )

        self._session.add(result)
        await self._session.flush()
        await self._session.refresh(result)

        logger.info(f"Created defect result {result.id}")
        return DefectAnalysisResultResponse.model_validate(result)

    async def get_defect_result(
        self,
        result_id: uuid.UUID,
    ) -> DefectAnalysisResultResponse | None:
        """Get defect analysis results by ID.

        Args:
            result_id: Result UUID

        Returns:
            Defect result as response schema, or None if not found
        """
        result = await self._session.execute(
            select(DefectAnalysisResult).where(
                DefectAnalysisResult.id == result_id,
            ),
        )
        defect_result = result.scalar_one_or_none()

        if defect_result is None:
            return None

        return DefectAnalysisResultResponse.model_validate(defect_result)

    async def list_defect_results(
        self,
        verification_job_id: uuid.UUID | None = None,
        defect_type: DefectType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DefectAnalysisResultResponse]:
        """List defect analysis results with optional filters.

        Args:
            verification_job_id: Filter by verification job ID
            defect_type: Filter by defect type
            limit: Maximum number of results
            offset: Query offset for pagination

        Returns:
            List of defect results as response schemas
        """
        query = select(DefectAnalysisResult)

        if verification_job_id is not None:
            query = query.where(
                DefectAnalysisResult.verification_job_id == verification_job_id,
            )

        if defect_type is not None:
            query = query.where(DefectAnalysisResult.defect_type == defect_type)

        query = query.order_by(DefectAnalysisResult.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self._session.execute(query)
        results = result.scalars().all()

        return [
            DefectAnalysisResultResponse.model_validate(r)
            for r in results
        ]

    async def update_defect_result(
        self,
        result_id: uuid.UUID,
        data: DefectAnalysisResultUpdate | dict[str, Any],
    ) -> DefectAnalysisResultResponse | None:
        """Update defect analysis results.

        Args:
            result_id: Result UUID
            data: Update data

        Returns:
            Updated defect result as response schema, or None if not found
        """
        if isinstance(data, dict):
            data = DefectAnalysisResultUpdate(**data)

        update_values = {
            k: v
            for k, v in {
                "analysis_metadata": data.metadata,
                "concentration": data.concentration,
                "formation_energy": data.formation_energy,
            }.items()
            if v is not None
        }

        if not update_values:
            return await self.get_defect_result(result_id)

        await self._session.execute(
            update(DefectAnalysisResult)
            .where(DefectAnalysisResult.id == result_id)
            .values(**update_values),
        )
        await self._session.flush()

        logger.info(f"Updated defect result {result_id}")
        return await self.get_defect_result(result_id)

    async def delete_defect_result(
        self,
        result_id: uuid.UUID,
    ) -> bool:
        """Delete defect analysis results.

        Args:
            result_id: Result UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(DefectAnalysisResult).where(
                DefectAnalysisResult.id == result_id,
            ),
        )

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted defect result {result_id}")

        return deleted

    # -------------------------------------------------------------------------
    # Potential Fitting Result CRUD
    # -------------------------------------------------------------------------

    async def create_fitting_result(
        self,
        data: PotentialFittingResultCreate | dict[str, Any],
    ) -> PotentialFittingResultResponse:
        """Create potential fitting results.

        Args:
            data: Fitting result creation data

        Returns:
            Created fitting result as response schema
        """
        if isinstance(data, dict):
            data = PotentialFittingResultCreate(**data)

        result = PotentialFittingResult(
            verification_job_id=data.verification_job_id,
            fitting_method=data.fitting_method,
            parameters=data.parameters,
            quality_metrics=data.quality_metrics,
        )

        self._session.add(result)
        await self._session.flush()
        await self._session.refresh(result)

        logger.info(f"Created fitting result {result.id}")
        return PotentialFittingResultResponse.model_validate(result)

    async def get_fitting_result(
        self,
        result_id: uuid.UUID,
    ) -> PotentialFittingResultResponse | None:
        """Get potential fitting results by ID.

        Args:
            result_id: Result UUID

        Returns:
            Fitting result as response schema, or None if not found
        """
        result = await self._session.execute(
            select(PotentialFittingResult).where(
                PotentialFittingResult.id == result_id,
            ),
        )
        fitting_result = result.scalar_one_or_none()

        if fitting_result is None:
            return None

        return PotentialFittingResultResponse.model_validate(fitting_result)

    async def update_fitting_result(
        self,
        result_id: uuid.UUID,
        data: PotentialFittingResultUpdate | dict[str, Any],
    ) -> PotentialFittingResultResponse | None:
        """Update potential fitting results.

        Args:
            result_id: Result UUID
            data: Update data

        Returns:
            Updated fitting result as response schema, or None if not found
        """
        if isinstance(data, dict):
            data = PotentialFittingResultUpdate(**data)

        update_values = {
            k: v
            for k, v in {
                "parameters": data.parameters,
                "quality_metrics": data.quality_metrics,
            }.items()
            if v is not None
        }

        if not update_values:
            return await self.get_fitting_result(result_id)

        await self._session.execute(
            update(PotentialFittingResult)
            .where(PotentialFittingResult.id == result_id)
            .values(**update_values),
        )
        await self._session.flush()

        logger.info(f"Updated fitting result {result_id}")
        return await self.get_fitting_result(result_id)

    async def delete_fitting_result(
        self,
        result_id: uuid.UUID,
    ) -> bool:
        """Delete potential fitting results.

        Args:
            result_id: Result UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(PotentialFittingResult).where(
                PotentialFittingResult.id == result_id,
            ),
        )

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted fitting result {result_id}")

        return deleted

    # -------------------------------------------------------------------------
    # Composite Queries
    # -------------------------------------------------------------------------

    async def get_job_with_results(
        self,
        job_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get a job with all its related results.

        Args:
            job_id: Job UUID

        Returns:
            Dictionary with job and all related data, or None if not found
        """
        result = await self._session.execute(
            select(MDVerificationJob)
            .where(MDVerificationJob.id == job_id),
        )
        job = result.scalar_one_or_none()

        if job is None:
            return None

        # Fetch related data
        hpc_jobs = await self.list_hpc_jobs(verification_job_id=job_id)
        sim_result = await self.get_simulation_result_by_job(job_id)
        defect_results = await self.list_defect_results(verification_job_id=job_id)
        fitting_results = await self.list_fitting_results_by_job(job_id)

        return {
            "job": MDVerificationJobResponse.model_validate(job),
            "hpc_jobs": hpc_jobs,
            "simulation_result": sim_result,
            "defect_results": defect_results,
            "fitting_results": fitting_results,
        }

    async def get_simulation_result_by_job(
        self,
        job_id: uuid.UUID,
    ) -> MDSimulationResultResponse | None:
        """Get simulation result by verification job ID.

        Args:
            job_id: Verification job UUID

        Returns:
            Simulation result as response schema, or None if not found
        """
        result = await self._session.execute(
            select(MDSimulationResult).where(
                MDSimulationResult.verification_job_id == job_id,
            ),
        )
        sim_result = result.scalar_one_or_none()

        if sim_result is None:
            return None

        return MDSimulationResultResponse.model_validate(sim_result)

    async def list_fitting_results_by_job(
        self,
        job_id: uuid.UUID,
    ) -> list[PotentialFittingResultResponse]:
        """List fitting results by verification job ID.

        Args:
            job_id: Verification job UUID

        Returns:
            List of fitting results as response schemas
        """
        result = await self._session.execute(
            select(PotentialFittingResult)
            .where(PotentialFittingResult.verification_job_id == job_id)
            .order_by(PotentialFittingResult.created_at.desc()),
        )
        results = result.scalars().all()

        return [
            PotentialFittingResultResponse.model_validate(r)
            for r in results
        ]
