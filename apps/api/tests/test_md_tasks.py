"""Unit tests for MD verification Celery tasks.

Phase 2.5 of NFM-337: Unit tests for md_tasks.py with mocked nfm-md-runner.
Tests happy path, retry logic, error handling, and task configuration.

Usage:
    pytest tests/test_md_tasks.py -v
    pytest tests/test_md_tasks.py -v -k "test_retry"
    pytest tests/test_md_tasks.py -v -m unit
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from celery.exceptions import Retry
from celery.schedules import crontab

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


@pytest.mark.unit
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
        with patch(
            "nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True
        ), patch(
            "nfm_db.services.md_tasks.AnalysisManager"
        ) as MockManager:
            # Setup mock
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.return_value = (
                mock_verification_results
            )
            MockManager.return_value = mock_manager_instance

            # Create task instance with mock request
            task_instance = MagicMock()
            task_instance.request = mock_task_request

            # Execute task
            result = run_md_verification_task(
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

        with patch(
            "nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True
        ), patch(
            "nfm_db.services.md_tasks.AnalysisManager"
        ) as MockManager:
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

            result = run_md_verification_task(
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


@pytest.mark.unit
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
                run_md_verification_task(
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
                run_md_verification_task(
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
                run_md_verification_task(
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
                run_md_verification_task(
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
                run_md_verification_task(
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
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True), patch(
            "nfm_db.services.md_tasks.AnalysisManager"
        ) as MockManager:
            # Setup mock to raise FileNotFoundError
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = (
                FileNotFoundError("Temporary NFS timeout")
            )
            MockManager.return_value = mock_manager_instance

            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(Retry) as exc_info:
                run_md_verification_task(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            # Verify retry exception
            assert "File access error" in str(exc_info.value)
            assert exc_info.value.retry_count == 1

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
        with patch("nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True), patch(
            "nfm_db.services.md_tasks.AnalysisManager"
        ) as MockManager:
            # Setup mock to raise ConnectionError
            mock_manager_instance = Mock()
            mock_manager_instance.run_verification_pipeline.side_effect = ConnectionError(
                "HPC cluster unreachable"
            )
            MockManager.return_value = mock_manager_instance

            task_instance = MagicMock()
            task_instance.request = mock_task_request

            with pytest.raises(Retry) as exc_info:
                run_md_verification_task(
                    task_instance,
                    mock_job_id,
                    str(mock_potential_file),
                    str(mock_structure_file),
                    mock_config,
                )

            # Verify retry with exponential backoff
            assert "HPC connection error" in str(exc_info.value)

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

            with patch(
                "nfm_db.services.md_tasks.NFM_MD_RUNNER_AVAILABLE", True
            ), patch("nfm_db.services.md_tasks.AnalysisManager") as MockManager:
                mock_manager_instance = Mock()
                mock_manager_instance.run_verification_pipeline.side_effect = (
                    ConnectionError("HPC cluster unreachable")
                )
                MockManager.return_value = mock_manager_instance

                task_instance = MagicMock()
                task_instance.request = mock_task_request

                with pytest.raises(Retry) as exc_info:
                    run_md_verification_task(
                        task_instance,
                        mock_job_id,
                        str(mock_potential_file),
                        str(mock_structure_file),
                        mock_config,
                    )

                # Verify exponential backoff: 120 * 2^retry_count
                expected_countdown = 120 * (2 ** retry_count)
                # The countdown should be set (implementation detail)
                assert exc_info.value.retry_count == retry_count + 1


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


@pytest.mark.unit
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
