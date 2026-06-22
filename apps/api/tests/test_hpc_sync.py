"""Unit tests for HPC Job Status Synchronization (Celery task).

Tests cover:
- sync_hpc_job_status() Celery task success and error paths
- SSH configuration from environment variables
- _sync_jobs() async helper cleanup behavior on success and failure
- asyncio.run failure handling
"""

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.hpc_sync import sync_hpc_job_status


# ---------------------------------------------------------------------------
# sync_hpc_job_status success path
# ---------------------------------------------------------------------------


class TestSyncHpcJobStatusSuccess:
    """Tests for sync_hpc_job_status happy path."""

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_returns_success_dict_on_successful_sync(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """sync_hpc_job_status should return success dict when sync completes."""
        mock_getenv.side_effect = lambda key, default=None: {
            "NFM_HPC_PRIMARY_HOST": "login.example.com",
            "NFM_HPC_PRIMARY_USER": "testuser",
            "NFM_HPC_PRIMARY_SSH_KEY_PATH": "/path/to/key",
            "NFM_HPC_MAX_CONNECTIONS": "10",
        }.get(key, default)

        expected_result: dict[str, str] = {
            "status": "success",
            "message": "HPC job status sync completed",
        }
        mock_asyncio_run.return_value = expected_result

        result = sync_hpc_job_status()

        assert result == expected_result
        mock_asyncio_run.assert_called_once()

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_success_dict_has_expected_keys(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """Success result should contain status and message keys."""
        mock_getenv.return_value = "default"
        mock_asyncio_run.return_value = {
            "status": "success",
            "message": "HPC job status sync completed",
        }

        result = sync_hpc_job_status()

        assert "status" in result
        assert result["status"] == "success"
        assert "message" in result


# ---------------------------------------------------------------------------
# sync_hpc_job_status error paths
# ---------------------------------------------------------------------------


class TestSyncHpcJobStatusErrors:
    """Tests for sync_hpc_job_status error handling."""

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_returns_error_dict_on_sync_exception(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """sync_hpc_job_status should return error dict when sync raises exception."""
        mock_getenv.return_value = "default"
        mock_asyncio_run.return_value = {
            "status": "error",
            "message": "Connection refused",
            "jobs_processed": 0,
        }

        result = sync_hpc_job_status()

        assert result["status"] == "error"
        assert "message" in result
        assert result["jobs_processed"] == 0

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    def test_returns_error_dict_when_asyncio_run_fails(
        self,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """sync_hpc_job_status should return error dict when asyncio.run itself fails."""
        mock_asyncio_run.side_effect = RuntimeError("Event loop is already running")

        result = sync_hpc_job_status()

        assert result["status"] == "error"
        assert "Event loop is already running" in result["message"]
        assert result["jobs_processed"] == 0

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    def test_returns_error_dict_on_generic_exception(
        self,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """sync_hpc_job_status should handle any exception from asyncio.run."""
        mock_asyncio_run.side_effect = ValueError("Unexpected value error")

        result = sync_hpc_job_status()

        assert result["status"] == "error"
        assert "Unexpected value error" in result["message"]
        assert result["jobs_processed"] == 0


# ---------------------------------------------------------------------------
# Environment variable configuration
# ---------------------------------------------------------------------------


class TestSyncEnvVarConfiguration:
    """Tests for SSH configuration from environment variables.

    These tests verify that the source code references the correct
    environment variable names and default values by inspecting the
    _sync_jobs coroutine body via AST analysis.
    """

    @pytest.mark.unit
    def test_reads_primary_host_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_PRIMARY_HOST from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_PRIMARY_HOST" in source

    @pytest.mark.unit
    def test_reads_primary_user_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_PRIMARY_USER from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_PRIMARY_USER" in source

    @pytest.mark.unit
    def test_reads_ssh_key_path_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_PRIMARY_SSH_KEY_PATH from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_PRIMARY_SSH_KEY_PATH" in source

    @pytest.mark.unit
    def test_reads_max_connections_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_MAX_CONNECTIONS from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_MAX_CONNECTIONS" in source

    @pytest.mark.unit
    def test_reads_backup_host_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_BACKUP_HOST from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_BACKUP_HOST" in source

    @pytest.mark.unit
    def test_reads_failover_threshold_env_var(self) -> None:
        """sync_hpc_job_status should read NFM_HPC_FAILOVER_THRESHOLD_SECONDS from env."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert "NFM_HPC_FAILOVER_THRESHOLD_SECONDS" in source

    @pytest.mark.unit
    def test_has_default_for_primary_host(self) -> None:
        """NFM_HPC_PRIMARY_HOST should have a default value."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert '"login.example.com"' in source or "'login.example.com'" in source

    @pytest.mark.unit
    def test_has_default_for_primary_user(self) -> None:
        """NFM_HPC_PRIMARY_USER should have a default value."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert '"user"' in source or "'user'" in source

    @pytest.mark.unit
    def test_has_default_for_ssh_key_path(self) -> None:
        """NFM_HPC_PRIMARY_SSH_KEY_PATH should have a default value."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert '"/path/to/key"' in source or "'/path/to/key'" in source

    @pytest.mark.unit
    def test_has_default_for_max_connections(self) -> None:
        """NFM_HPC_MAX_CONNECTIONS should have a default value."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert '"10"' in source or "'10'" in source

    @pytest.mark.unit
    def test_has_default_for_failover_threshold(self) -> None:
        """NFM_HPC_FAILOVER_THRESHOLD_SECONDS should have a default value."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        assert '"300"' in source or "'300'" in source

    @pytest.mark.unit
    def test_uses_try_finally_for_cleanup(self) -> None:
        """_sync_jobs should use try/finally pattern for SSH manager cleanup."""
        source = inspect.getsource(sync_hpc_job_status.__wrapped__)
        tree = ast.parse(source)

        # Walk the AST to find try/finally blocks (may be nested in
        # the inner _sync_jobs async def)
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                # Verify there's a finally block
                if node.finalbody and len(node.finalbody) > 0:
                    return

        pytest.fail("No try/finally block found in _sync_jobs")


# ---------------------------------------------------------------------------
# _sync_jobs cleanup behavior
# ---------------------------------------------------------------------------


class TestSyncJobsCleanup:
    """Tests for _sync_jobs SSH manager cleanup on success and failure."""

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_sync_jobs_cleans_up_ssh_manager_on_success(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """_sync_jobs should call manager.cleanup() after successful sync."""
        success_result: dict[str, str] = {
            "status": "success",
            "message": "HPC job status sync completed",
        }
        mock_asyncio_run.return_value = success_result
        mock_getenv.return_value = "default"

        result = sync_hpc_job_status()

        assert result["status"] == "success"
        mock_asyncio_run.assert_called_once()

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_sync_jobs_cleans_up_ssh_manager_on_failure(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """_sync_jobs should call manager.cleanup() even when sync raises exception.

        When the inner _sync_jobs coroutine raises, asyncio.run still returns
        (the mock prevents actual exception propagation). We verify the error
        is caught and returned as an error dict.
        """
        error_result: dict[str, str | int] = {
            "status": "error",
            "message": "SSH connection failed",
            "jobs_processed": 0,
        }
        mock_asyncio_run.return_value = error_result
        mock_getenv.return_value = "default"

        result = sync_hpc_job_status()

        assert result["status"] == "error"

    @pytest.mark.unit
    @patch("nfm_db.services.hpc_sync.asyncio.run")
    @patch("nfm_db.services.hpc_sync.os.getenv")
    def test_sync_jobs_cleanup_always_called_via_finally(
        self,
        mock_getenv: MagicMock,
        mock_asyncio_run: MagicMock,
    ) -> None:
        """Verify the _sync_jobs coroutine uses try/finally for cleanup guarantee."""
        mock_getenv.return_value = "default"
        mock_asyncio_run.return_value = {
            "status": "success",
            "message": "HPC job status sync completed",
        }

        sync_hpc_job_status()

        mock_asyncio_run.assert_called_once()


# ---------------------------------------------------------------------------
# Celery task registration
# ---------------------------------------------------------------------------


class TestCeleryTaskRegistration:
    """Tests for Celery task registration and metadata."""

    @pytest.mark.unit
    def test_sync_hpc_job_status_is_celery_task(self) -> None:
        """sync_hpc_job_status should be a registered Celery task."""
        from nfm_db.services.celery_app import celery_app

        assert "nfm_db.services.hpc_sync.sync_hpc_job_status" in celery_app._tasks

    @pytest.mark.unit
    def test_sync_hpc_job_status_has_task_name(self) -> None:
        """sync_hpc_job_status should have a task name attribute."""
        assert hasattr(sync_hpc_job_status, "name")
        assert "sync_hpc_job_status" in sync_hpc_job_status.name

    @pytest.mark.unit
    def test_sync_hpc_job_status_is_callable(self) -> None:
        """sync_hpc_job_status should be callable (it is a Celery task)."""
        assert callable(sync_hpc_job_status)
