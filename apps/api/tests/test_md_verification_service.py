"""Unit tests for MD verification service.

Tests per NFM-334 acceptance criteria:
- ORM models match database schema (5 tables with relationships)
- Service layer provides CRUD operations for all MD verification entities
- Pydantic schemas for API responses
- Async database operations throughout
- 80%+ coverage

Test organization:
- Model validation tests
- MD verification job CRUD
- HPC job CRUD
- MD simulation result CRUD
- Defect analysis result CRUD
- Potential fitting result CRUD
- Composite query tests
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcJobStatus,
    JobStatus,
)
from nfm_db.services.md_verification import (
    DefectAnalysisResultCreate,
    DefectAnalysisResultResponse,
    HpcJobCreate,
    HpcJobResponse,
    HpcJobUpdate,
    MDSimulationResultCreate,
    MDSimulationResultResponse,
    MDVerificationJobCreate,
    MDVerificationJobResponse,
    MDVerificationJobUpdate,
    MDVerificationService,
    PotentialFittingResultCreate,
    PotentialFittingResultResponse,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock AsyncSession."""
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()

    async def _refresh(obj: Any) -> None:
        """Simulate DB refresh by setting generated fields."""
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now()
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime.now()

    session.flush = AsyncMock()
    session.refresh = AsyncMock(side_effect=_refresh)
    session.execute = AsyncMock()
    return session


@pytest.fixture
def job_id() -> uuid.UUID:
    """Sample job UUID for testing."""
    return uuid.uuid4()


@pytest.fixture
def sample_job_data() -> dict[str, Any]:
    """Sample MD verification job data."""
    return {
        "potential_id": "EAM_alloy_U",
        "element_system": "U",
        "phase": "BCC",
        "config": {
            "temperature": 300,
            "pressure": 0,
            "ensemble": "NPT",
        },
        "priority": 5,
        "status": JobStatus.PENDING,
    }


@pytest.fixture
def sample_hpc_job_data(job_id: uuid.UUID) -> dict[str, Any]:
    """Sample HPC job data."""
    return {
        "verification_job_id": job_id,
        "hpc_cluster": "guangzhou-hpc",
        "hpc_job_id": "12345",
        "status": HpcJobStatus.PENDING,
        "partition": "compute",
        "nodes": 4,
        "walltime_requested": 3600,
    }


@pytest.fixture
def sample_simulation_data(job_id: uuid.UUID) -> dict[str, Any]:
    """Sample MD simulation result data."""
    return {
        "verification_job_id": job_id,
        "trajectory_file_path": "/data/trajectory.lammpstrj",
        "thermodynamic_data": {
            "temperature": [300, 301, 302],
            "pressure": [0.1, 0.2, 0.1],
            "energy": [-1000, -1001, -1002],
        },
        "simulation_time_ps": 100.0,
        "steps_completed": 100000,
        "final_energy": -1002.5,
        "final_temperature": 302.0,
        "final_pressure": 0.1,
    }


@pytest.fixture
def sample_defect_data(job_id: uuid.UUID) -> dict[str, Any]:
    """Sample defect analysis result data."""
    return {
        "verification_job_id": job_id,
        "defect_type": DefectType.VACANCY,
        "concentration": 0.001,
        "formation_energy": 3.5,
        "metadata": {
            "site": "substitutional",
            "charge_state": 0,
        },
    }


@pytest.fixture
def sample_fitting_data(job_id: uuid.UUID) -> dict[str, Any]:
    """Sample potential fitting result data."""
    return {
        "verification_job_id": job_id,
        "fitting_method": FittingMethod.ARC_DPA,
        "parameters": {
            "epsilon": 0.5,
            "sigma": 2.5,
            "cutoff": 10.0,
        },
        "quality_metrics": {
            "rmse": 0.05,
            "r_squared": 0.98,
        },
    }


# ===========================================================================
# Pydantic Schema Tests
# ===========================================================================


class TestMDVerificationJobSchemas:
    """Test MD verification job Pydantic schemas."""

    def test_create_schema_valid(self, sample_job_data: dict[str, Any]) -> None:
        """Valid data passes schema validation."""
        schema = MDVerificationJobCreate(**sample_job_data)
        assert schema.potential_id == "EAM_alloy_U"
        assert schema.element_system == "U"
        assert schema.phase == "BCC"
        assert schema.status == JobStatus.PENDING
        assert schema.priority == 5

    def test_create_schema_defaults(self) -> None:
        """Default values are applied correctly."""
        schema = MDVerificationJobCreate(
            potential_id="EAM_alloy_U",
            element_system="U",
            config={"temperature": 300},
        )
        assert schema.phase is None
        assert schema.status == JobStatus.PENDING
        assert schema.priority == 5

    def test_create_schema_missing_required(self) -> None:
        """Missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            MDVerificationJobCreate(
                potential_id="EAM_alloy_U",
                # Missing element_system, config
            )

    def test_update_schema_partial(self) -> None:
        """Partial updates only modify provided fields."""
        schema = MDVerificationJobUpdate(status=JobStatus.RUNNING)
        assert schema.status == JobStatus.RUNNING
        assert schema.priority is None
        assert schema.error_message is None


class TestHpcJobSchemas:
    """Test HPC job Pydantic schemas."""

    def test_create_schema_valid(self, sample_hpc_job_data: dict[str, Any]) -> None:
        """Valid data passes schema validation."""
        schema = HpcJobCreate(**sample_hpc_job_data)
        assert schema.hpc_cluster == "guangzhou-hpc"
        assert schema.partition == "compute"
        assert schema.nodes == 4

    def test_update_schema_partial(self) -> None:
        """Partial updates work correctly."""
        schema = HpcJobUpdate(status=HpcJobStatus.RUNNING, walltime_used=1800)
        assert schema.status == HpcJobStatus.RUNNING
        assert schema.partition is None


class TestMDSimulationResultSchemas:
    """Test MD simulation result Pydantic schemas."""

    def test_create_schema_valid(self, sample_simulation_data: dict[str, Any]) -> None:
        """Valid data passes schema validation."""
        schema = MDSimulationResultCreate(**sample_simulation_data)
        assert schema.trajectory_file_path == "/data/trajectory.lammpstrj"
        assert schema.simulation_time_ps == 100.0
        assert schema.steps_completed == 100000

    def test_create_schema_optional_fields(self) -> None:
        """Optional fields can be omitted."""
        schema = MDSimulationResultCreate(
            verification_job_id=uuid.uuid4(),
            steps_completed=1000,
        )
        assert schema.trajectory_file_path is None
        assert schema.thermodynamic_data is None


class TestDefectAnalysisResultSchemas:
    """Test defect analysis result Pydantic schemas."""

    def test_create_schema_valid(self, sample_defect_data: dict[str, Any]) -> None:
        """Valid data passes schema validation."""
        schema = DefectAnalysisResultCreate(**sample_defect_data)
        assert schema.defect_type == DefectType.VACANCY
        assert schema.concentration == 0.001
        assert schema.formation_energy == 3.5

    def test_create_schema_missing_concentration(self, job_id: uuid.UUID) -> None:
        """Missing required concentration raises ValidationError."""
        with pytest.raises(ValidationError):
            DefectAnalysisResultCreate(
                verification_job_id=job_id,
                defect_type=DefectType.VACANCY,
                # Missing concentration
            )


class TestPotentialFittingResultSchemas:
    """Test potential fitting result Pydantic schemas."""

    def test_create_schema_valid(self, sample_fitting_data: dict[str, Any]) -> None:
        """Valid data passes schema validation."""
        schema = PotentialFittingResultCreate(**sample_fitting_data)
        assert schema.fitting_method == FittingMethod.ARC_DPA
        assert "epsilon" in schema.parameters
        assert schema.quality_metrics["rmse"] == 0.05

    def test_create_schema_missing_parameters(self, job_id: uuid.UUID) -> None:
        """Missing required parameters raises ValidationError."""
        with pytest.raises(ValidationError):
            PotentialFittingResultCreate(
                verification_job_id=job_id,
                fitting_method=FittingMethod.RPA,
                # Missing parameters
            )


# ===========================================================================
# MD Verification Job CRUD Tests
# ===========================================================================


class TestMDVerificationJobCrud:
    """Test MD verification job CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_job(
        self,
        mock_session: AsyncMock,
        sample_job_data: dict[str, Any],
    ) -> None:
        """Job creation returns response schema."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_job(sample_job_data)

        assert isinstance(result, MDVerificationJobResponse)
        assert result.potential_id == "EAM_alloy_U"
        assert result.element_system == "U"

    @pytest.mark.asyncio
    async def test_create_job_from_dict(
        self,
        mock_session: AsyncMock,
        sample_job_data: dict[str, Any],
    ) -> None:
        """Job creation accepts dict input."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_job(sample_job_data)

        assert isinstance(result, MDVerificationJobResponse)

    @pytest.mark.asyncio
    async def test_get_job_not_found(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Get job returns None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        result = await svc.get_job(job_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_list_jobs_no_filter(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """List jobs without filters returns all jobs."""
        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()
        mock_job.owner_id = None
        mock_job.potential_id = "EAM_alloy_U"
        mock_job.element_system = "U"
        mock_job.phase = "BCC"
        mock_job.config = {}
        mock_job.status = JobStatus.PENDING
        mock_job.priority = 5
        mock_job.submitted_at = None
        mock_job.started_at = None
        mock_job.completed_at = None
        mock_job.error_message = None
        mock_job.created_at = datetime.now()
        mock_job.updated_at = datetime.now()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_job]
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        results = await svc.list_jobs()

        assert len(results) == 1
        assert isinstance(results[0], MDVerificationJobResponse)

    @pytest.mark.asyncio
    async def test_list_jobs_with_status_filter(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """List jobs with status filter."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        results = await svc.list_jobs(status=JobStatus.RUNNING)

        # Verify query was constructed with filter
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_update_job_status(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Update job status."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.update_job(job_id, {"status": JobStatus.RUNNING})

        # Verify update was called
        mock_session.execute.assert_called()

    @pytest.mark.asyncio
    async def test_delete_job(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Delete job returns True on success."""
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        deleted = await svc.delete_job(job_id)

        assert deleted is True

    @pytest.mark.asyncio
    async def test_delete_job_not_found(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Delete job returns False when not found."""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        deleted = await svc.delete_job(job_id)

        assert deleted is False


# ===========================================================================
# HPC Job CRUD Tests
# ===========================================================================


class TestHpcJobCrud:
    """Test HPC job CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_hpc_job(
        self,
        mock_session: AsyncMock,
        sample_hpc_job_data: dict[str, Any],
    ) -> None:
        """HPC job creation returns response schema."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_hpc_job(sample_hpc_job_data)

        assert isinstance(result, HpcJobResponse)
        assert result.hpc_cluster == "guangzhou-hpc"

    @pytest.mark.asyncio
    async def test_list_hpc_jobs_by_verification(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """List HPC jobs filtered by verification job ID."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        results = await svc.list_hpc_jobs(verification_job_id=job_id)

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_update_hpc_job_status(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Update HPC job status."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.update_hpc_job(
            job_id,
            {"status": HpcJobStatus.RUNNING, "walltime_used": 1800},
        )

        mock_session.execute.assert_called()


# ===========================================================================
# MD Simulation Result CRUD Tests
# ===========================================================================


class TestMDSimulationResultCrud:
    """Test MD simulation result CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_simulation_result(
        self,
        mock_session: AsyncMock,
        sample_simulation_data: dict[str, Any],
    ) -> None:
        """Simulation result creation returns response schema."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_simulation_result(sample_simulation_data)

        assert isinstance(result, MDSimulationResultResponse)
        assert result.steps_completed == 100000

    @pytest.mark.asyncio
    async def test_update_simulation_result(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Update simulation result."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.update_simulation_result(
            job_id,
            {"steps_completed": 200000, "final_energy": -2005.0},
        )

        mock_session.execute.assert_called()


# ===========================================================================
# Defect Analysis Result CRUD Tests
# ===========================================================================


class TestDefectAnalysisResultCrud:
    """Test defect analysis result CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_defect_result(
        self,
        mock_session: AsyncMock,
        sample_defect_data: dict[str, Any],
    ) -> None:
        """Defect result creation returns response schema."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_defect_result(sample_defect_data)

        assert isinstance(result, DefectAnalysisResultResponse)
        assert result.defect_type == DefectType.VACANCY
        assert result.concentration == 0.001

    @pytest.mark.asyncio
    async def test_list_defect_results_by_type(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """List defect results filtered by type."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        results = await svc.list_defect_results(defect_type=DefectType.VACANCY)

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_update_defect_result(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Update defect result."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.update_defect_result(
            job_id,
            {"concentration": 0.002, "formation_energy": 3.8},
        )

        mock_session.execute.assert_called()


# ===========================================================================
# Potential Fitting Result CRUD Tests
# ===========================================================================


class TestPotentialFittingResultCrud:
    """Test potential fitting result CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_fitting_result(
        self,
        mock_session: AsyncMock,
        sample_fitting_data: dict[str, Any],
    ) -> None:
        """Fitting result creation returns response schema."""
        # mock_session.flush and refresh already configured in fixture

        svc = MDVerificationService(mock_session)
        result = await svc.create_fitting_result(sample_fitting_data)

        assert isinstance(result, PotentialFittingResultResponse)
        assert result.fitting_method == FittingMethod.ARC_DPA
        assert "epsilon" in result.parameters

    @pytest.mark.asyncio
    async def test_update_fitting_result(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Update fitting result."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.update_fitting_result(
            job_id,
            {"parameters": {"epsilon": 0.6, "sigma": 2.6}},
        )

        mock_session.execute.assert_called()


# ===========================================================================
# Composite Query Tests
# ===========================================================================


class TestCompositeQueries:
    """Test composite queries that fetch related data."""

    @pytest.mark.asyncio
    async def test_get_job_with_results(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Get job with all related results."""
        # Mock job query
        mock_job = MagicMock()
        mock_job.id = job_id
        mock_job.owner_id = None
        mock_job.potential_id = "EAM_alloy_U"
        mock_job.element_system = "U"
        mock_job.phase = "BCC"
        mock_job.config = {}
        mock_job.status = JobStatus.COMPLETED
        mock_job.priority = 5
        mock_job.submitted_at = datetime.now()
        mock_job.started_at = datetime.now()
        mock_job.completed_at = datetime.now()
        mock_job.error_message = None
        mock_job.created_at = datetime.now()
        mock_job.updated_at = datetime.now()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_job
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Mock related data queries
        svc = MDVerificationService(mock_session)

        # Mock list_hpc_jobs
        svc.list_hpc_jobs = AsyncMock(return_value=[])
        # Mock get_simulation_result_by_job
        svc.get_simulation_result_by_job = AsyncMock(return_value=None)
        # Mock list_defect_results
        svc.list_defect_results = AsyncMock(return_value=[])
        # Mock list_fitting_results_by_job
        svc.list_fitting_results_by_job = AsyncMock(return_value=[])

        result = await svc.get_job_with_results(job_id)

        assert result is not None
        assert "job" in result
        assert "hpc_jobs" in result
        assert "simulation_result" in result
        assert "defect_results" in result
        assert "fitting_results" in result

    @pytest.mark.asyncio
    async def test_get_simulation_result_by_job(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """Get simulation result by verification job ID."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        await svc.get_simulation_result_by_job(job_id)

        # Verify query was constructed
        mock_session.execute.assert_called()

    @pytest.mark.asyncio
    async def test_list_fitting_results_by_job(
        self,
        mock_session: AsyncMock,
        job_id: uuid.UUID,
    ) -> None:
        """List fitting results by verification job ID."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        svc = MDVerificationService(mock_session)
        results = await svc.list_fitting_results_by_job(job_id)

        assert isinstance(results, list)
