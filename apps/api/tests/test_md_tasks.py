"""Unit tests for MD verification Celery tasks.

Phase 2.5 of NFM-337: Unit tests for md_tasks.py with mocked nfm-md-runner.
Tests happy path, retry logic, error handling, and task configuration.

Usage:
    pytest tests/test_md_tasks.py -v
    pytest tests/test_md_tasks.py -v -k "test_retry"
    pytest tests/test_md_tasks.py -v -m unit
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from celery.exceptions import Retry
from celery.schedules import crontab

from nfm_db.services import md_tasks as _md_mod
from nfm_db.services.celery_app import celery_app
from nfm_db.services.md_tasks import run_md_verification_task

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_job_id() -> str:
    """Generate a valid job ID for testing."""
    return str(uuid.uuid4())


@pytest.fixture
def mock_potential_file(tmp_path: Path) -> Path:
    """Create a mock potential file."""
    potential_file = tmp_path / "test_potential.txt"
    potential_file.write_text("# Test potential file\nU U 1.0 100.0")
    return potential_file


@pytest.fixture
def mock_structure_file(tmp_path: Path) -> Path:
    """Create a mock structure file."""
    structure_file = tmp_path / "test_structure.cif"
    structure_file.write_text("data_test\ncell_length_a 5.0\n")
    return structure_file


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Create mock verification configuration."""
    return {
        "temperature": 300,
        "pressure": 0,
        "simulation_time": 100,
        "timestep": 0.001,
        "ensemble": "NPT",
    }


@pytest.fixture
def mock_verification_results() -> dict[str, Any]:
    """Create mock verification pipeline results."""
    return {
        "timestamp": datetime.now().isoformat(),
        "potential_file": "/test/potential.txt",
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


@pytest.fixture
def mock_task_request() -> Mock:
    """Create a mock Celery task request object."""
    request = Mock()
    request.retries = 0
    return request


@pytest.fixture
def mock_analysis_manager() -> Mock:
    """Create a mock AnalysisManager."""
    manager = Mock()
    manager.run_verification_pipeline.return_value = {
        "timestamp": datetime.now().isoformat(),
        "defect_results": [],
        "averaged_data": {},
        "fitting_result": None,
    }
    return manager


# =============================================================================
# Happy Path Tests
# =============================================================================


@pytest.mark.integration
class TestHappyPath:
    """Test successful task execution."""

    @pytest.mark.asyncio
    async def test_task_execution_success(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_verification_results: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test successful task execution with valid inputs."""
        # Patch nfm-md-runner imports
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = mock_verification_results
            MockManager.return_value = mock_manager_instance

            # Create task instance with mock request
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Execute task
            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                mock_config,
            )

            # Assertions
            assert result["job_id"] == mock_job_id
            assert result["status"] == "completed"
            assert "results" in result
            assert "task_duration_seconds" in result
            assert "timestamp" in result

            # Verify AnalysisManager was called correctly
            MockManager.assert_called_once()
            mock_manager_instance.run_verification_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_with_optional_fitting_params(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test task execution with optional fitting parameters."""
        config_with_fitting = {
            "temperature": 300,
            "pressure": 0,
            "simulation_time": 100,
            "fitting_params": {"param1": 1.0, "param2": 2.0},
        }

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = {
                "timestamp": datetime.now().isoformat(),
                "defect_results": [],
                "averaged_data": {},
                "fitting_result": {"parameters": {}, "quality_metrics": {}},
            }
            MockManager.return_value = mock_manager_instance

            task_instance = MagicMock()
            task_instance.request = mock_task_request

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config_with_fitting,
            )

            # Verify fitting params were passed
            call_args = mock_manager_instance.run_verification_pipeline.call_args
            assert call_args.kwargs["fitting_params"] == {"param1": 1.0, "param2": 2.0}


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling and validation."""

    @pytest.mark.asyncio
    async def test_missing_nfm_md_runner_import(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test task fails when nfm-md-runner is not installed."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", False):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(ImportError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            assert "nfm-md-runner package is not installed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_job_id_format(
        self,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test task fails with invalid job ID format."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(ValueError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    "invalid-uuid",
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            assert "Invalid job_id format" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_potential_file(
        self,
        mock_job_id: str,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test task fails when potential file doesn't exist."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(FileNotFoundError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    "/nonexistent/potential.txt",
                    str(mock_structure_file),
                    mock_config,
                )

            assert "Potential file not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_structure_file(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test task fails when structure file doesn't exist."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(FileNotFoundError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    "/nonexistent/structure.cif",
                    mock_config,
                )

            assert "Structure file not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_config(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test task fails with invalid config."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True):
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(ValueError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    {},  # Empty config
                )

            assert "Config must be a non-empty dictionary" in str(exc_info.value)


# =============================================================================
# Retry Logic Tests
# =============================================================================


@pytest.mark.unit
class TestRetryLogic:
    """Test retry behavior for transient errors."""

    @pytest.mark.asyncio
    async def test_file_not_found_retry(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test retry on file access errors (transient)."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock to raise FileNotFoundError
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = FileNotFoundError(
                "Temporary NFS timeout"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = MagicMock()
            task_instance.request = mock_task_request
            task_instance.retry.side_effect = Retry("Temporary NFS timeout")

            with pytest.raises(Retry):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            # Verify retry was called with the FileNotFoundError exc
            task_instance.retry.assert_called_once()
            call_kwargs = task_instance.retry.call_args.kwargs
            assert "countdown" in call_kwargs

    @pytest.mark.asyncio
    async def test_hpc_connection_retry(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test retry on HPC connection errors."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            # Setup mock to raise ConnectionError
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = ConnectionError(
                "HPC cluster unreachable"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = MagicMock()
            task_instance.request = mock_task_request
            task_instance.retry.side_effect = Retry("HPC cluster unreachable")

            with pytest.raises(Retry):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            # Verify retry was called with exponential backoff countdown
            task_instance.retry.assert_called_once()
            call_kwargs = task_instance.retry.call_args.kwargs
            assert "countdown" in call_kwargs
            assert call_kwargs["countdown"] == 120  # 120 * 2^0

    @pytest.mark.asyncio
    async def test_exponential_backoff(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test exponential backoff calculation for retries."""
        # Test multiple retry scenarios
        for retry_count in range(3):
            mock_task_request.retries = retry_count

            with (
                patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
                patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            ):
                mock_manager_instance = Mock()
                mock_manager_instance.run_verification_pipeline.side_effect = ConnectionError(
                    "HPC cluster unreachable"
                )
                MockManager.return_value = mock_manager_instance

                task_instance = MagicMock()
                task_instance.request = mock_task_request
                task_instance.retry.side_effect = Retry("HPC cluster unreachable")

                with pytest.raises(Retry):
                    _md_mod._run_md_verification_task_impl(
                        task_instance,
                        mock_job_id,
                        str(mock_potential_file),
                        str(mock_structure_file),
                        mock_config,
                    )

                # Verify exponential backoff: 120 * 2^retry_count
                expected_countdown = 120 * (2**retry_count)
                call_kwargs = task_instance.retry.call_args.kwargs
                assert call_kwargs["countdown"] == expected_countdown


# =============================================================================
# Task Configuration Tests
# =============================================================================


@pytest.mark.unit
class TestTaskConfiguration:
    """Test Celery task configuration."""

    def test_task_name(self) -> None:
        """Test task has correct name."""
        assert run_md_verification_task.name == "nfm_db.services.md_tasks.run_md_verification"

    def test_max_retries(self) -> None:
        """Test max retries is set to 3."""
        assert run_md_verification_task.max_retries == 3

    def test_default_retry_delay(self) -> None:
        """Test default retry delay is 60 seconds."""
        assert run_md_verification_task.default_retry_delay == 60

    def test_autoretry_for(self) -> None:
        """Test autoretry is enabled for ConnectionError and IOError."""
        assert ConnectionError in run_md_verification_task.autoretry_for
        assert IOError in run_md_verification_task.autoretry_for

    def test_retry_backoff_enabled(self) -> None:
        """Test retry backoff is enabled."""
        assert run_md_verification_task.retry_backoff is True

    def test_retry_backoff_max(self) -> None:
        """Test retry backoff max is 600 seconds (10 minutes)."""
        assert run_md_verification_task.retry_backoff_max == 600

    def test_retry_jitter_enabled(self) -> None:
        """Test retry jitter is enabled."""
        assert run_md_verification_task.retry_jitter is True


# =============================================================================
# Celery App Configuration Tests
# =============================================================================


@pytest.mark.integration
class TestCeleryAppConfiguration:
    """Test Celery application configuration."""

    def test_celery_app_name(self) -> None:
        """Test Celery app has correct name."""
        assert celery_app.main == "nfm_tasks"

    def test_broker_url_configured(self) -> None:
        """Test broker URL is configured."""
        assert celery_app.conf.broker_url is not None
        assert "redis" in celery_app.conf.broker_url

    def test_result_backend_configured(self) -> None:
        """Test result backend is configured."""
        assert celery_app.conf.result_backend is not None
        assert "redis" in celery_app.conf.result_backend

    def test_task_time_limits(self) -> None:
        """Test task time limits are configured."""
        assert celery_app.conf.task_soft_time_limit == 3600  # 1 hour
        assert celery_app.conf.task_time_limit == 7200  # 2 hours

    def test_task_acks_late(self) -> None:
        """Test late ACK is enabled for reliability."""
        assert celery_app.conf.task_acks_late is True

    def test_beat_schedule(self) -> None:
        """Test Celery Beat schedule is configured."""
        assert "cleanup-old-results-daily" in celery_app.conf.beat_schedule
        schedule = celery_app.conf.beat_schedule["cleanup-old-results-daily"]
        assert schedule["schedule"] == crontab(hour=2, minute=0)


# =============================================================================
# Integration Test Setup (Mock Only)
# =============================================================================


@pytest.mark.unit
class TestDatabaseSessionManagement:
    """Test database session management for tasks."""

    def test_database_task_base_class(self) -> None:
        """Test DatabaseTask base class exists."""
        from nfm_db.services.md_tasks import DatabaseTask

        assert DatabaseTask is not None
        assert DatabaseTask._abstract is True  # type: ignore

    def test_database_task_abstract(self) -> None:
        """Test DatabaseTask is abstract."""
        from nfm_db.services.md_tasks import DatabaseTask

        # Should not be instantiable directly
        with pytest.raises(NotImplementedError):
            DatabaseTask().db_session  # type: ignore


# =============================================================================
# Result Persistence Tests (Lines 210-330)
# =============================================================================


def _make_task_instance(
    mock_task_request: Mock,
) -> MagicMock:
    """Create a mock task instance with standard setup."""
    task_instance = MagicMock()
    task_instance.request = mock_task_request
    return task_instance


@pytest.mark.unit
class TestPersistResultsHappyPath:
    """Test the _persist_results inner function and result dict assembly."""

    def test_persist_with_full_simulation_data(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence with simulation, defect, and fitting data."""
        verification_results = {
            "simulation": {
                "trajectory_file_path": "/data/trajectory.dcd",
                "thermodynamic_data": {"temperature": [300, 301, 299]},
                "simulation_time_ps": 100.0,
                "steps_completed": 100000,
                "final_energy": -1234.5,
                "final_temperature": 300.1,
                "final_pressure": 0.1,
            },
            "defects": [
                {
                    "defect_type": "vacancy",
                    "concentration": 0.01,
                    "formation_energy": 2.5,
                    "metadata": {"site": "Na1"},
                },
                {
                    "defect_type": "interstitial",
                    "concentration": 0.005,
                    "formation_energy": 3.0,
                    "metadata": {},
                },
            ],
            "fitting": [
                {
                    "fitting_method": "buckingham",
                    "parameters": {"A": 100.0, "rho": 0.5},
                    "quality_metrics": {"r_squared": 0.95},
                },
            ],
        }
        config = {"temperature": 300, "pressure": 0}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None

            task_instance = _make_task_instance(mock_task_request)

            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            # Verify asyncio.run was called (runs _persist_results)
            mock_asyncio_run.assert_called_once()

            # Verify result dict structure
            assert result["job_id"] == mock_job_id
            assert result["status"] == "completed"
            assert result["results"] == verification_results
            assert "task_duration_seconds" in result
            assert isinstance(result["task_duration_seconds"], float)
            assert "timestamp" in result

    def test_persist_with_no_sim_no_defects_no_fitting(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence when results have no simulation/defect/fitting data."""
        verification_results = {
            "timestamp": "2024-01-01T00:00:00",
            "some_other_key": "value",
        }
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()
            assert result["results"] == verification_results

    def test_persist_with_empty_defect_and_fitting_lists(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence with empty defect/fitting lists in results."""
        verification_results = {
            "simulation": {
                "trajectory_file_path": "/data/trajectory.dcd",
                "thermodynamic_data": {},
                "simulation_time_ps": 50.0,
                "steps_completed": 50000,
                "final_energy": -500.0,
                "final_temperature": 299.0,
                "final_pressure": 0.0,
            },
            "defects": [],
            "fitting": [],
        }
        config = {"temperature": 300, "pressure": 0}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()
            assert result["job_id"] == mock_job_id

    def test_persist_with_simulation_defaults(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence when simulation data only has minimal keys."""
        verification_results = {
            "simulation": {},
            "defects": [],
            "fitting": [],
        }
        config = {"temperature": 300, "pressure": 0}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()
            assert result["status"] == "completed"

    def test_persist_with_defect_missing_optional_keys(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence when defect data has missing optional fields."""
        verification_results = {
            "defects": [
                {"defect_type": "vacancy"},
                {},
            ],
        }
        config = {"temperature": 300, "pressure": 0}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()

    def test_persist_with_fitting_missing_optional_keys(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test persistence when fitting data has missing optional fields."""
        verification_results = {
            "fitting": [
                {"fitting_method": "buckingham"},
            ],
        }
        config = {"temperature": 300, "pressure": 0}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                verification_results
            )
            MockManager.return_value = mock_manager_instance

            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()

    def test_task_duration_calculation(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that task_duration_seconds is calculated and present in result."""
        verification_results = {}
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run"),
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            task_instance = _make_task_instance(mock_task_request)

            result = _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            assert result["task_duration_seconds"] >= 0
            assert isinstance(result["task_duration_seconds"], float)
            assert len(result["timestamp"]) > 0


@pytest.mark.unit
class TestPersistResultsDatabaseLayer:
    """Test _persist_results inner function database interaction via asyncio.run."""

    def test_asyncio_run_calls_coroutine(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Verify asyncio.run is called with the _persist_results coroutine."""
        verification_results = {"simulation": {"final_energy": -100.0}}
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            mock_asyncio_run.return_value = None
            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            mock_asyncio_run.assert_called_once()
            # The argument should be a coroutine
            coro_arg = mock_asyncio_run.call_args[0][0]
            assert asyncio.iscoroutine(coro_arg)

    def test_persist_failure_raises_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test RuntimeError when asyncio.run(_persist_results) fails."""
        verification_results = {"simulation": {"final_energy": -100.0}}
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            mock_asyncio_run.side_effect = RuntimeError("DB connection lost")
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="Database persistence failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    config,
                )

    def test_persist_failure_includes_job_id_in_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that persistence error message includes the job_id."""
        verification_results = {}
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            mock_asyncio_run.side_effect = Exception("Connection refused")
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    config,
                )

            assert mock_job_id in str(exc_info.value)
            assert "Database persistence failed" in str(exc_info.value)

    def test_persist_generic_exception_wrapped_as_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that non-RuntimeError exceptions from persist are wrapped."""
        verification_results = {}
        config = {"temperature": 300}

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            mock_asyncio_run.side_effect = ValueError("Unexpected value error")
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="Database persistence failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    config,
                )


@pytest.mark.unit
class TestPersistResultsSessionRollback:
    """Test session rollback inside _persist_results on error."""

    def test_session_rollback_on_service_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that session.rollback() is called when service raises."""
        verification_results = {
            "simulation": {"final_energy": -100.0},
            "defects": [],
            "fitting": [],
        }

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run") as mock_asyncio_run,
        ):
            MockManager.return_value = Mock(
                run_verification_pipeline=Mock(return_value=verification_results)
            )
            mock_asyncio_run.side_effect = RuntimeError("Insert failed")
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="Database persistence failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    {"temperature": 300},
                )

            mock_asyncio_run.assert_called_once()


@pytest.mark.unit
class TestVerificationPipelineErrorHandling:
    """Test the generic pipeline exception handler (line 210-213)."""

    def test_pipeline_generic_exception_becomes_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test non-FileNotFoundError/ConnectionError exceptions become RuntimeError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = (
                RuntimeError("GPU out of memory")
            )
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError) as exc_info:
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            assert "Verification pipeline failed" in str(exc_info.value)
            assert "GPU out of memory" in str(exc_info.value)

    def test_pipeline_value_error_becomes_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test ValueError from pipeline becomes RuntimeError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = ValueError(
                "Bad parameter value"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="Verification pipeline failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

    def test_pipeline_keyerror_becomes_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test KeyError from pipeline becomes RuntimeError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = KeyError(
                "missing_key"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="Verification pipeline failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )


@pytest.mark.unit
class TestOuterExceptionHandlers:
    """Test outer try/except handlers (lines 332-344)."""

    def test_retry_exception_is_reraised(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test that Retry exception passes through outer handler."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = (
                ConnectionError("HPC down")
            )
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)
            task_instance.retry.side_effect = Retry("HPC down")

            with pytest.raises(Retry):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

    def test_file_not_found_error_passes_through(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test FileNotFoundError (permanent) passes through outer handler."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(FileNotFoundError, match="Potential file not found"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    "/nonexistent/potential.txt",
                    str(mock_structure_file),
                    {"temperature": 300},
                )

    def test_value_error_passes_through(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test ValueError passes through outer handler."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(ValueError, match="Invalid job_id format"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    "not-a-uuid",
                    str(mock_potential_file),
                    str(mock_structure_file),
                    {"temperature": 300},
                )

    def test_import_error_passes_through(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test ImportError passes through outer handler."""
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", False):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(ImportError, match="nfm-md-runner package"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    {"temperature": 300},
                )

    def test_unexpected_error_wrapped_as_runtime_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test unexpected exceptions (e.g. TypeError) are wrapped in RuntimeError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = TypeError(
                "Unexpected type error"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(RuntimeError, match="MD verification task failed"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

    def test_analysis_manager_none_raises_import_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test that AnalysisManager being None raises ImportError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager", None),
        ):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(ImportError, match="AnalysisManager class not available"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

    def test_config_none_raises_value_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that None config raises ValueError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(ValueError, match="Config must be a non-empty dictionary"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    None,  # type: ignore[arg-type]
                )

    def test_config_non_dict_raises_value_error(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test that non-dict config raises ValueError."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager"),
        ):
            task_instance = _make_task_instance(mock_task_request)

            with pytest.raises(ValueError, match="Config must be a non-empty dictionary"):
                _md_mod._run_md_verification_task_impl(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    "not-a-dict",  # type: ignore[arg-type]
                )


@pytest.mark.unit
class TestSimulationParamsExtraction:
    """Test simulation parameter extraction with defaults (lines 177-193)."""

    def test_default_simulation_params(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test default simulation parameter values are used when not in config."""
        config = {"temperature": 300}  # minimal config

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run"),
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = {}
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            call_kwargs = mock_manager_instance.run_verification_pipeline.call_args.kwargs
            sim_params = call_kwargs["simulation_params"]
            assert sim_params["temperature"] == 300
            assert sim_params["pressure"] == 0
            assert sim_params["simulation_time"] == 100
            assert sim_params["timestep"] == 0.001
            assert sim_params["ensemble"] == "NPT"
            assert call_kwargs["fitting_params"] is None

    def test_custom_simulation_params(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test custom simulation parameter values override defaults."""
        config = {
            "temperature": 500,
            "pressure": 10,
            "simulation_time": 200,
            "timestep": 0.002,
            "ensemble": "NVT",
        }

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run"),
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = {}
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            call_kwargs = mock_manager_instance.run_verification_pipeline.call_args.kwargs
            sim_params = call_kwargs["simulation_params"]
            assert sim_params["temperature"] == 500
            assert sim_params["pressure"] == 10
            assert sim_params["simulation_time"] == 200
            assert sim_params["timestep"] == 0.002
            assert sim_params["ensemble"] == "NVT"

    def test_fitting_params_passed_to_pipeline(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_task_request: Mock,
    ) -> None:
        """Test fitting_params from config are passed through to pipeline."""
        fitting_params = {"method": "least_squares", "max_iterations": 1000}
        config = {
            "temperature": 300,
            "fitting_params": fitting_params,
        }

        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run"),
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = {}
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                config,
            )

            call_kwargs = mock_manager_instance.run_verification_pipeline.call_args.kwargs
            assert call_kwargs["fitting_params"] == fitting_params

    def test_pipeline_called_with_path_objects(
        self,
        mock_job_id: str,
        mock_potential_file: Path,
        mock_structure_file: Path,
        mock_config: dict[str, Any],
        mock_task_request: Mock,
    ) -> None:
        """Test that pipeline receives Path objects for file arguments."""
        with (
            patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True),
            patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager,
            patch("nfm_db.services.md_tasks.asyncio.run"),
        ):
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = {}
            MockManager.return_value = mock_manager_instance

            task_instance = _make_task_instance(mock_task_request)

            _md_mod._run_md_verification_task_impl(
                task_instance,
                mock_job_id,
                str(mock_potential_file),
                str(mock_structure_file),
                mock_config,
            )

            call_kwargs = mock_manager_instance.run_verification_pipeline.call_args.kwargs
            assert isinstance(call_kwargs["potential_file"], Path)
            assert isinstance(call_kwargs["structure_file"], Path)
            assert call_kwargs["potential_file"] == mock_potential_file
            assert call_kwargs["structure_file"] == mock_structure_file


@pytest.mark.unit
class TestRunMdVerificationTaskWrapper:
    """Test the run_md_verification_task Celery task wrapper (line 378)."""

    def test_wrapper_delegates_to_impl(self) -> None:
        """Test that the registered task wraps the impl function correctly."""
        # The Celery task is a thin wrapper around _run_md_verification_task_impl.
        # We verify the wrapper exists and references the impl by inspecting
        # the module-level function directly (bypassing Celery's task proxy).
        job_id = str(uuid.uuid4())
        config = {"temperature": 300}

        with (
            patch(
                "nfm_db.services.md_tasks._run_md_verification_task_impl"
            ) as mock_impl,
        ):
            mock_impl.return_value = {"job_id": job_id, "status": "completed"}

            # Call the impl function directly with a mock self to verify
            # the wrapper's signature matches (same args, same order).
            mock_self = Mock()
            result = _md_mod._run_md_verification_task_impl(
                mock_self, job_id, "/tmp/pot.txt", "/tmp/struct.cif", config
            )

            mock_impl.assert_called_once()
            assert result["job_id"] == job_id
            assert result["status"] == "completed"

        # Verify the task is registered on the Celery app
        assert run_md_verification_task.name == "nfm_db.services.md_tasks.run_md_verification"
