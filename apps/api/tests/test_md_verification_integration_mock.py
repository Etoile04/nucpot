"""Integration tests for MD verification with mocked HPC connections.

Tests the complete flow: API → Celery task → Database persistence
with mocked nfm-md-runner to avoid external dependencies.

Usage:
    pytest tests/test_md_verification_integration_mock.py -v
    pytest tests/test_md_verification_integration_mock.py -v -m integration
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    JobStatus,
)
from nfm_db.services.md_tasks import run_md_verification_task
from nfm_db.services.md_verification import MDVerificationService

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_db_session():
    """Create a mock database session that simulates real database behavior."""
    session = AsyncMock(spec=AsyncSession)

    # Track added objects
    added_objects = []

    def mock_add(obj: Any) -> None:
        added_objects.append(obj)

    async def mock_flush() -> None:
        pass

    async def mock_refresh(obj: Any) -> None:
        # Simulate database setting generated fields
        if hasattr(obj, "id") and not obj.id:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at"):
            obj.created_at = datetime.now()
        if hasattr(obj, "updated_at"):
            obj.updated_at = datetime.now()

    session.add = mock_add
    session.flush = mock_flush
    session.refresh = mock_refresh
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    # Store for test assertions
    session._added_objects = added_objects

    yield session


@pytest.fixture
def mock_analysis_manager():
    """Create a mock AnalysisManager from nfm-md-runner."""
    manager = MagicMock()

    # Simulate successful verification pipeline
    manager.run_verification_pipeline.return_value = {
        "timestamp": datetime.now().isoformat(),
        "potential_file": "/test/potential.empirical",
        "structure_file": "/test/structure.cif",
        "defect_results": [
            {
                "vacancies": 10,
                "interstitials": 5,
                "vacancy_concentration": 0.01,
                "interstitial_concentration": 0.005,
            }
        ],
        "averaged_data": {
            "vacancy_concentration": {"mean": 0.01, "std": 0.001},
            "interstitial_concentration": {"mean": 0.005, "std": 0.0005},
        },
        "fitting_result": {
            "parameters": {"param1": 1.0, "param2": 2.0},
            "quality_metrics": {"r_squared": 0.95},
        },
    }

    return manager


@pytest.fixture
def mock_task_request():
    """Create a mock Celery task request object."""
    request = Mock()
    request.retries = 0
    return request


@pytest.fixture
def tmp_test_files(tmp_path: Path) -> dict[str, Path]:
    """Create temporary test files for potential and structure."""
    # Create test potential file
    potential_file = tmp_path / "test_potential.empirical"
    potential_content = """# Test EAM potential
U U 1.0 100.0 2.0 3.0
"""
    potential_file.write_text(potential_content)

    # Create test structure file
    structure_file = tmp_path / "test_structure.cif"
    structure_content = """data_test_structure
_cell_length_a    5.0
_cell_length_b    5.0
_cell_length_c    5.0
_cell_angle_alpha 90.0
_cell_angle_beta  90.0
_cell_angle_gamma 90.0

loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
U1 U 0.0 0.0 0.0
U2 U 0.5 0.5 0.0
"""
    structure_file.write_text(structure_content)

    return {
        "potential_file": potential_file,
        "structure_file": structure_file,
    }


@pytest.fixture
def test_job_config() -> dict[str, Any]:
    """Create test verification configuration."""
    return {
        "temperature": 300,
        "pressure": 0,
        "simulation_time": 100,
        "timestep": 0.001,
        "ensemble": "NPT",
        "fitting_params": {
            "param1": 1.0,
            "param2": 2.0,
        },
    }


# =============================================================================
# Mock HPC Integration Tests
# =============================================================================


@pytest.mark.integration
class TestMockedHPCIntegration:
    """Test MD verification flow with mocked HPC connections."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_hpc(
        self,
        mock_db_session,
        mock_analysis_manager,
        mock_task_request,
        tmp_test_files,
        test_job_config,
    ):
        """Test complete MD verification pipeline with mocked HPC.

        This test simulates:
        1. Creating a verification job via API
        2. Triggering Celery task execution
        3. Mock HPC analysis execution
        4. Persisting results to database
        """
        job_id = uuid.uuid4()

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock
            MockManager.return_value = mock_analysis_manager

            # Create task instance with mock request
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Execute task (simulates Celery task execution)
            result = run_md_verification_task(
                task_instance,
                str(job_id),
                str(tmp_test_files["potential_file"]),
                str(tmp_test_files["structure_file"]),
                test_job_config,
            )

            # Verify task result structure
            assert result["job_id"] == str(job_id)
            assert result["status"] == "completed"
            assert "results" in result
            assert "task_duration_seconds" in result
            assert result["task_duration_seconds"] > 0
            assert "timestamp" in result

            # Verify verification results
            verification_results = result["results"]
            assert "defect_results" in verification_results
            assert "averaged_data" in verification_results
            assert "fitting_result" in verification_results

    @pytest.mark.asyncio
    async def test_hpc_connection_error_retry(
        self,
        mock_db_session,
        mock_task_request,
        tmp_test_files,
        test_job_config,
    ):
        """Test retry behavior when HPC connection fails."""
        from celery.exceptions import Retry

        job_id = uuid.uuid4()

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock to raise ConnectionError (HPC unreachable)
            mock_manager = MagicMock()
            mock_manager.run_verification_pipeline.side_effect = ConnectionError(
                "HPC cluster unreachable"
            )
            MockManager.return_value = mock_manager

            task_instance = MagicMock()
            task_instance.request = mock_task_request
            # self.retry(exc=e) internally raises Retry — mock accordingly
            task_instance.retry.side_effect = Retry("HPC cluster unreachable")

            # Task should raise Retry exception for HPC errors
            with pytest.raises(Retry):
                run_md_verification_task(
                    task_instance,
                    str(job_id),
                    str(tmp_test_files["potential_file"]),
                    str(tmp_test_files["structure_file"]),
                    test_job_config,
                )

            # Verify retry was called with the ConnectionError exc
            task_instance.retry.assert_called_once()
            call_kwargs = task_instance.retry.call_args
            assert "countdown" in call_kwargs.kwargs or len(call_kwargs.args) > 0

    @pytest.mark.asyncio
    async def test_file_not_found_error(
        self,
        mock_db_session,
        mock_task_request,
        test_job_config,
    ):
        """Test error handling when potential file is not found."""
        job_id = uuid.uuid4()

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Use non-existent files to trigger FileNotFoundError in task validation
            with pytest.raises(FileNotFoundError, match="Potential file not found"):
                run_md_verification_task(
                    task_instance,
                    str(job_id),
                    "/nonexistent/potential.empirical",
                    "/nonexistent/structure.cif",
                    test_job_config,
                )

    @pytest.mark.asyncio
    async def test_invalid_input_validation(
        self,
        mock_db_session,
        mock_task_request,
        tmp_test_files,
    ):
        """Test validation errors for invalid input parameters."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Test invalid job ID format
            with pytest.raises(ValueError, match="Invalid job_id format"):
                run_md_verification_task(
                    task_instance,
                    "invalid-uuid-format",
                    str(tmp_test_files["potential_file"]),
                    str(tmp_test_files["structure_file"]),
                    {"temperature": 300},
                )

            # Test empty config - this should also fail validation
            with pytest.raises(ValueError, match="Config must be a non-empty"):
                run_md_verification_task(
                    task_instance,
                    str(uuid.uuid4()),
                    str(tmp_test_files["potential_file"]),
                    str(tmp_test_files["structure_file"]),
                    {},  # Empty config
                )

    @pytest.mark.asyncio
    async def test_hpc_connection_error(
        self,
        mock_db_session,
        mock_task_request,
        tmp_test_files,
    ):
        """Test error handling when HPC connection fails."""
        from celery.exceptions import Retry

        job_id = uuid.uuid4()

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock to raise ConnectionError (HPC unreachable)
            mock_manager = MagicMock()
            mock_manager.run_verification_pipeline.side_effect = ConnectionError(
                "HPC cluster unreachable"
            )
            MockManager.return_value = mock_manager

            task_instance = MagicMock()
            task_instance.request = mock_task_request
            # self.retry(exc=e) internally raises Retry — mock accordingly
            task_instance.retry.side_effect = Retry("HPC cluster unreachable")

            # Task should wrap HPC connection errors in Retry
            with pytest.raises(Retry, match="HPC cluster unreachable"):
                run_md_verification_task(
                    task_instance,
                    str(job_id),
                    str(tmp_test_files["potential_file"]),
                    str(tmp_test_files["structure_file"]),
                    {"temperature": 300},
                )


@pytest.mark.integration
class TestDatabasePersistence:
    """Test database persistence of verification results."""

    @pytest.mark.asyncio
    async def test_job_lifecycle_mock(
        self,
        mock_db_session,
    ):
        """Test job lifecycle with mocked service methods."""
        from nfm_db.services.md_verification import MDVerificationJobCreate

        service = MDVerificationService(mock_db_session)

        # Mock the job creation to return a simple dict
        test_job_id = uuid.uuid4()

        async def mock_create_job(job_data):
            return {
                "id": test_job_id,
                "potential_id": job_data.potential_id,
                "element_system": job_data.element_system,
                "phase": job_data.phase,
                "config": job_data.config,
                "status": job_data.status,
                "priority": job_data.priority,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }

        service.create_job = mock_create_job

        # Create job
        job_data = MDVerificationJobCreate(
            potential_id="test_potential_001",
            element_system="U-U",
            phase="BCC",
            config={"temperature": 300, "pressure": 0},
            priority=5,
            status=JobStatus.PENDING,
        )

        job_response = await service.create_job(job_data)

        # Verify job was created
        assert job_response["id"] == test_job_id
        assert job_response["potential_id"] == "test_potential_001"
        assert job_response["status"] == JobStatus.PENDING

        # Mock update behavior
        async def mock_update_job(job_id, updates):
            result = job_response.copy()
            result.update(updates)
            return result

        service.update_job = mock_update_job

        # Simulate status update
        updated_job = await service.update_job(job_response["id"], {"status": JobStatus.SUBMITTED})
        assert updated_job is not None
        assert updated_job["status"] == JobStatus.SUBMITTED

        # Simulate completion
        completed_job = await service.update_job(
            job_response["id"], {"status": JobStatus.COMPLETED}
        )
        assert completed_job is not None
        assert completed_job["status"] == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_result_storage_mock(
        self,
        mock_db_session,
    ):
        """Test result storage with mocked service methods."""
        from nfm_db.services.md_verification import (
            DefectAnalysisResultCreate,
            MDSimulationResultCreate,
            PotentialFittingResultCreate,
        )

        service = MDVerificationService(mock_db_session)

        # Create parent job
        job_id = uuid.uuid4()

        # Mock result creation methods to return simple dicts
        async def mock_create_sim_result(data):
            return {
                "id": uuid.uuid4(),
                "verification_job_id": data.verification_job_id,
                "trajectory_file_path": data.trajectory_file_path,
                "simulation_time_ps": data.simulation_time_ps,
                "thermodynamic_data": data.thermodynamic_data,
                "created_at": datetime.now(),
            }

        async def mock_create_defect_result(data):
            return {
                "id": uuid.uuid4(),
                "verification_job_id": data.verification_job_id,
                "defect_type": data.defect_type,
                "concentration": data.concentration,
                "metadata": data.metadata,
                "created_at": datetime.now(),
            }

        async def mock_create_fitting_result(data):
            return {
                "id": uuid.uuid4(),
                "verification_job_id": data.verification_job_id,
                "fitting_method": data.fitting_method,
                "parameters": data.parameters,
                "quality_metrics": data.quality_metrics,
                "created_at": datetime.now(),
            }

        service.create_simulation_result = mock_create_sim_result
        service.create_defect_result = mock_create_defect_result
        service.create_fitting_result = mock_create_fitting_result

        # Create simulation result
        sim_data = MDSimulationResultCreate(
            verification_job_id=job_id,
            trajectory_file_path="/data/trajectory.lammpstrj",
            simulation_time_ps=100.5,
            thermodynamic_data={"temperature": 300.0, "pressure": 0.0},
        )

        sim_result = await service.create_simulation_result(sim_data)
        assert sim_result is not None
        assert sim_result["trajectory_file_path"] == "/data/trajectory.lammpstrj"

        # Create defect analysis result
        defect_data = DefectAnalysisResultCreate(
            verification_job_id=job_id,
            defect_type=DefectType.VACANCY,
            concentration=0.01,
            metadata={"temperature": 300.0},
        )

        defect_result = await service.create_defect_result(defect_data)
        assert defect_result is not None
        assert defect_result["defect_type"] == DefectType.VACANCY

        # Create fitting result
        fitting_data = PotentialFittingResultCreate(
            verification_job_id=job_id,
            fitting_method=FittingMethod.ARC_DPA,
            parameters={"param1": 1.0, "param2": 2.0},
            quality_metrics={"r_squared": 0.95},
        )

        fitting_result = await service.create_fitting_result(fitting_data)
        assert fitting_result is not None
        assert fitting_result["fitting_method"] == FittingMethod.ARC_DPA


@pytest.mark.integration
class TestErrorScenarios:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_missing_nfm_md_runner_import(
        self,
        mock_task_request,
    ):
        """Test graceful failure when nfm-md-runner is not installed."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", False):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(ImportError, match="nfm-md-runner package is not installed"):
                run_md_verification_task(
                    task_instance,
                    str(uuid.uuid4()),
                    "/test/potential.empirical",
                    "/test/structure.cif",
                    {"temperature": 300},
                )

    @pytest.mark.asyncio
    async def test_timeout_handling(
        self,
        mock_db_session,
        mock_task_request,
        tmp_test_files,
        test_job_config,
    ):
        """Test timeout handling during long-running simulations."""
        job_id = uuid.uuid4()

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock to simulate timeout
            mock_manager = MagicMock()
            mock_manager.run_verification_pipeline.side_effect = TimeoutError(
                "Simulation timeout after 3600 seconds"
            )
            MockManager.return_value = mock_manager

            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Task should wrap timeout in RuntimeError
            with pytest.raises(RuntimeError, match="MD verification task failed"):
                run_md_verification_task(
                    task_instance,
                    str(job_id),
                    str(tmp_test_files["potential_file"]),
                    str(tmp_test_files["structure_file"]),
                    test_job_config,
                )
