"""Tests for HPCOrchestrator uncovered methods.

Covers submit_job, poll_job_status, sync_all_active_jobs,
file transfer delegates, _log_failover_event, __del__,
and private helper methods.
"""

import asyncio
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from nfm_db.services.hpc_ssh import (
    SSHConnectionConfig,
    SSHConnectionManager,
    HPCConnectionError,
    JobSubmissionError,
)
from nfm_db.services.hpc_orchestration import HPCOrchestrator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_config() -> SSHConnectionConfig:
    """Create a minimal SSH config without backup."""
    return SSHConnectionConfig(
        hosts=("primary.example.com",),
        username="testuser",
        ssh_key_path="/path/to/key",
        skip_key_validation=True,
    )


@pytest.fixture
def config_with_backup() -> SSHConnectionConfig:
    """Create SSH config with backup cluster."""
    return SSHConnectionConfig(
        hosts=("primary.example.com",),
        username="testuser",
        ssh_key_path="/path/to/key",
        backup_hosts=("backup.example.com",),
        backup_username="backup_user",
        backup_ssh_key_path="/path/to/backup_key",
        failover_threshold_seconds=300,
        skip_key_validation=True,
    )


@pytest.fixture
def orchestrator(minimal_config: SSHConnectionConfig) -> HPCOrchestrator:
    """Create a basic HPCOrchestrator."""
    return HPCOrchestrator(minimal_config)


@pytest.fixture
def orchestrator_with_backup(config_with_backup: SSHConnectionConfig) -> HPCOrchestrator:
    """Create an HPCOrchestrator with backup cluster."""
    return HPCOrchestrator(config_with_backup)


def _valid_params() -> Dict[str, Any]:
    """Return valid simulation parameters."""
    return {
        "temperature": 300,
        "pressure": 1.0,
        "timesteps": 10000,
        "job_name": "test_job",
    }


# ---------------------------------------------------------------------------
# __del__ exception handling
# ---------------------------------------------------------------------------


class TestDestructor:
    """Tests for HPCOrchestrator.__del__."""

    @pytest.mark.unit
    def test_del_calls_cleanup(self, orchestrator: HPCOrchestrator) -> None:
        """__del__ should call cleanup without raising."""
        with patch.object(orchestrator, "cleanup") as mock_cleanup:
            orchestrator.__del__()
            mock_cleanup.assert_called_once()

    @pytest.mark.unit
    def test_del_suppresses_exceptions(self, orchestrator: HPCOrchestrator) -> None:
        """__del__ should suppress any exceptions from cleanup."""
        with patch.object(orchestrator, "cleanup", side_effect=RuntimeError("boom")):
            orchestrator.__del__()  # Should not raise

    @pytest.mark.unit
    def test_del_no_error_when_cleanup_succeeds(self, orchestrator: HPCOrchestrator) -> None:
        """__del__ should silently succeed when cleanup works."""
        with patch.object(orchestrator, "cleanup"):
            orchestrator.__del__()  # Should not raise


# ---------------------------------------------------------------------------
# _log_failover_event
# ---------------------------------------------------------------------------


class TestLogFailoverEvent:
    """Tests for HPCOrchestrator._log_failover_event."""

    @pytest.mark.unit
    async def test_logs_event_to_database(self, orchestrator: HPCOrchestrator) -> None:
        """Should log failover event to database successfully."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        async def mock_get_db():
            yield mock_db

        with patch("nfm_db.services.hpc_orchestration.get_db", return_value=mock_get_db()):
            await orchestrator._log_failover_event(
                event_type="failover",
                source_cluster="primary",
                reason="health_check_failed",
                success=True,
                target_cluster="backup",
            )
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    @pytest.mark.unit
    async def test_logs_event_with_failure_count(self, orchestrator: HPCOrchestrator) -> None:
        """Should pass failure_count to HPCFailoverEvent."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        async def mock_get_db():
            yield mock_db

        with patch("nfm_db.services.hpc_orchestration.get_db", return_value=mock_get_db()):
            await orchestrator._log_failover_event(
                event_type="failover",
                source_cluster="primary",
                reason="timeout",
                failure_count=3,
                success=False,
            )
            mock_db.add.assert_called_once()

    @pytest.mark.unit
    async def test_logs_event_with_metadata(self, orchestrator: HPCOrchestrator) -> None:
        """Should pass event_metadata to HPCFailoverEvent."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()

        async def mock_get_db():
            yield mock_db

        metadata = {"latency_ms": 150, "retry_count": 2}
        with patch("nfm_db.services.hpc_orchestration.get_db", return_value=mock_get_db()):
            await orchestrator._log_failover_event(
                event_type="recovery",
                source_cluster="primary",
                reason="health_restored",
                event_metadata=metadata,
            )
            mock_db.add.assert_called_once()

    @pytest.mark.unit
    async def test_handles_database_error_gracefully(self, orchestrator: HPCOrchestrator) -> None:
        """Should not raise when database commit fails."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock(side_effect=Exception("DB connection lost"))

        async def mock_get_db():
            yield mock_db

        with patch("nfm_db.services.hpc_orchestration.get_db", return_value=mock_get_db()):
            await orchestrator._log_failover_event(
                event_type="failover",
                source_cluster="primary",
                reason="error",
            )
            # Should not raise

    @pytest.mark.unit
    async def test_handles_get_db_exception(self, orchestrator: HPCOrchestrator) -> None:
        """Should not raise when get_db itself fails."""
        with patch(
            "nfm_db.services.hpc_orchestration.get_db",
            side_effect=Exception("Database unavailable"),
        ):
            await orchestrator._log_failover_event(
                event_type="failover",
                source_cluster="primary",
                reason="error",
            )
            # Should not raise


# ---------------------------------------------------------------------------
# submit_job
# ---------------------------------------------------------------------------


class TestSubmitJob:
    """Tests for HPCOrchestrator.submit_job."""

    @pytest.mark.unit
    async def test_submit_job_success_on_primary(
        self, orchestrator: HPCOrchestrator
    ) -> None:
        """Should submit job on primary cluster when healthy."""
        params = _valid_params()

        orchestrator.failover_manager.should_trigger_failover = MagicMock(return_value=False)

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch("nfm_db.services.hpc_orchestration.submit_to_slurm", new_callable=AsyncMock, return_value="12345"):
                    with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock):
                        result = await orchestrator.submit_job("task-1", "structure.xyz", params)
                        assert result == "12345"

    @pytest.mark.unit
    async def test_submit_job_triggers_failover_when_needed(
        self, orchestrator_with_backup: HPCOrchestrator
    ) -> None:
        """Should trigger failover when primary is unhealthy."""
        params = _valid_params()

        orchestrator_with_backup.failover_manager.should_trigger_failover = MagicMock(return_value=True)
        orchestrator_with_backup.failover_manager.trigger_failover = AsyncMock(return_value=True)

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch("nfm_db.services.hpc_orchestration.submit_to_slurm", new_callable=AsyncMock, return_value="12345"):
                    with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock):
                        result = await orchestrator_with_backup.submit_job("task-1", "structure.xyz", params)
                        assert result == "12345"
                        orchestrator_with_backup.failover_manager.trigger_failover.assert_called_once()

    @pytest.mark.unit
    async def test_submit_job_raises_when_both_clusters_unavailable(
        self, orchestrator_with_backup: HPCOrchestrator
    ) -> None:
        """Should raise HPCConnectionError when failover fails."""
        params = _valid_params()

        orchestrator_with_backup.failover_manager.should_trigger_failover = MagicMock(return_value=True)
        orchestrator_with_backup.failover_manager.trigger_failover = AsyncMock(return_value=False)

        with pytest.raises(HPCConnectionError, match="Both primary and backup"):
            await orchestrator_with_backup.submit_job("task-1", "structure.xyz", params)

    @pytest.mark.unit
    async def test_submit_job_recovers_primary_when_on_backup(
        self, orchestrator_with_backup: HPCOrchestrator
    ) -> None:
        """Should switch back to primary when on backup and primary recovers."""
        params = _valid_params()

        orchestrator_with_backup.failover_manager.should_trigger_failover = MagicMock(return_value=False)
        orchestrator_with_backup.failover_manager.current_cluster = "backup"
        orchestrator_with_backup.failover_manager.try_recover_primary = AsyncMock(return_value=True)

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch("nfm_db.services.hpc_orchestration.submit_to_slurm", new_callable=AsyncMock, return_value="12345"):
                    with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock):
                        result = await orchestrator_with_backup.submit_job("task-1", "structure.xyz", params)
                        assert result == "12345"
                        assert orchestrator_with_backup.failover_manager.current_cluster == "primary"

    @pytest.mark.unit
    async def test_submit_job_retries_on_backup_on_submission_error(
        self, orchestrator_with_backup: HPCOrchestrator
    ) -> None:
        """Should retry on backup when primary submission fails."""
        params = _valid_params()

        orchestrator_with_backup.failover_manager.should_trigger_failover = MagicMock(return_value=False)
        orchestrator_with_backup.failover_manager.current_cluster = "primary"
        orchestrator_with_backup.failover_manager.trigger_failover = AsyncMock(return_value=True)

        call_count = 0

        async def mock_submit(*args: object, **kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("SSH connection dropped")
            return "67890"

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch("nfm_db.services.hpc_orchestration.submit_to_slurm", side_effect=mock_submit):
                    with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock):
                        result = await orchestrator_with_backup.submit_job("task-1", "structure.xyz", params)
                        assert result == "67890"
                        assert call_count == 2

    @pytest.mark.unit
    async def test_submit_job_raises_on_primary_failure_no_backup(
        self, orchestrator: HPCOrchestrator
    ) -> None:
        """Should raise HPCConnectionError when primary submission fails and no backup."""
        params = _valid_params()

        orchestrator.failover_manager.should_trigger_failover = MagicMock(return_value=False)
        orchestrator.failover_manager.current_cluster = "primary"

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch(
                    "nfm_db.services.hpc_orchestration.submit_to_slurm",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("Connection refused"),
                ):
                    with pytest.raises(HPCConnectionError, match="Job submission failed on all clusters"):
                        await orchestrator.submit_job("task-1", "structure.xyz", params)

    @pytest.mark.unit
    async def test_submit_job_raises_on_backup_failure(
        self, orchestrator_with_backup: HPCOrchestrator
    ) -> None:
        """Should raise HPCConnectionError when backup submission also fails."""
        params = _valid_params()

        orchestrator_with_backup.failover_manager.should_trigger_failover = MagicMock(return_value=False)
        orchestrator_with_backup.failover_manager.current_cluster = "backup"

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch(
                    "nfm_db.services.hpc_orchestration.submit_to_slurm",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("Backup also down"),
                ):
                    with pytest.raises(HPCConnectionError, match="Job submission failed on all clusters"):
                        await orchestrator_with_backup.submit_job("task-1", "structure.xyz", params)

    @pytest.mark.unit
    async def test_submit_job_creates_hpc_job_record(
        self, orchestrator: HPCOrchestrator
    ) -> None:
        """Should create HPC job record after successful submission."""
        params = _valid_params()

        orchestrator.failover_manager.should_trigger_failover = MagicMock(return_value=False)

        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params"):
            with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash"):
                with patch("nfm_db.services.hpc_orchestration.submit_to_slurm", new_callable=AsyncMock, return_value="12345"):
                    with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock) as mock_create:
                        await orchestrator.submit_job("task-1", "structure.xyz", params)
                        mock_create.assert_called_once()
                        # Verify it was called with task_id, hpc_job_id, params, and a cluster name
                        call_args = mock_create.call_args
                        assert call_args[0][0] == "task-1"
                        assert call_args[0][1] == "12345"
                        assert call_args[0][2] == params
                        assert isinstance(call_args[0][3], str)


# ---------------------------------------------------------------------------
# Private helper delegates
# ---------------------------------------------------------------------------


class TestPrivateHelpers:
    """Tests for private helper methods on HPCOrchestrator."""

    @pytest.mark.unit
    def test_validate_simulation_params_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_validate_simulation_params should call validate_simulation_params."""
        params = _valid_params()
        with patch("nfm_db.services.hpc_orchestration.validate_simulation_params") as mock_validate:
            orchestrator._validate_simulation_params(params)
            mock_validate.assert_called_once_with(params)

    @pytest.mark.unit
    def test_generate_slurm_script_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_generate_slurm_script should call generate_slurm_script."""
        params = _valid_params()
        with patch("nfm_db.services.hpc_orchestration.generate_slurm_script", return_value="#!/bin/bash") as mock_gen:
            result = orchestrator._generate_slurm_script(params)
            assert result == "#!/bin/bash"
            mock_gen.assert_called_once_with(params)

    @pytest.mark.unit
    async def test_submit_to_slurm_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_submit_to_slurm should call submit_to_slurm with current cluster."""
        mock_manager = MagicMock()
        with patch.object(
            type(orchestrator.failover_manager),
            "current_ssh_manager",
            new_callable=PropertyMock,
            return_value=mock_manager,
        ), patch.object(
            type(orchestrator.failover_manager),
            "current_cluster_name",
            new_callable=PropertyMock,
            return_value="primary",
        ), patch("nfm_db.services.hpc_orchestration.submit_to_slurm", new_callable=AsyncMock, return_value="999") as mock_submit:
            result = await orchestrator._submit_to_slurm("task-1", "#!/bin/bash")
            assert result == "999"
            mock_submit.assert_called_once()

    @pytest.mark.unit
    def test_upload_script_via_sftp_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_upload_script_via_sftp should call upload_script_via_sftp."""
        mock_client = MagicMock()
        with patch("nfm_db.services.hpc_slurm.upload_script_via_sftp") as mock_upload:
            orchestrator._upload_script_via_sftp(mock_client, "#!/bin/bash", "/remote/path/job.sh")
            mock_upload.assert_called_once_with(mock_client, "#!/bin/bash", "/remote/path/job.sh")

    @pytest.mark.unit
    async def test_create_hpc_job_record_primary(self, orchestrator: HPCOrchestrator) -> None:
        """_create_hpc_job_record should use primary cluster for cluster_used='primary'."""
        with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock) as mock_create:
            await orchestrator._create_hpc_job_record("task-1", "12345", _valid_params(), "primary")
            mock_create.assert_called_once_with("task-1", "12345", _valid_params(), "primary.example.com")

    @pytest.mark.unit
    async def test_create_hpc_job_record_backup(self, orchestrator_with_backup: HPCOrchestrator) -> None:
        """_create_hpc_job_record should use backup host for cluster_used='backup'."""
        with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock) as mock_create:
            await orchestrator_with_backup._create_hpc_job_record("task-1", "12345", _valid_params(), "backup")
            mock_create.assert_called_once_with("task-1", "12345", _valid_params(), "backup.example.com")

    @pytest.mark.unit
    async def test_create_hpc_job_record_backup_no_hosts(self, orchestrator: HPCOrchestrator) -> None:
        """_create_hpc_job_record should use 'unknown' when backup requested but no backup hosts."""
        with patch("nfm_db.services.hpc_orchestration.create_hpc_job_record", new_callable=AsyncMock) as mock_create:
            await orchestrator._create_hpc_job_record("task-1", "12345", _valid_params(), "backup")
            mock_create.assert_called_once_with("task-1", "12345", _valid_params(), "unknown")

    @pytest.mark.unit
    def test_parse_walltime_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_parse_walltime should call parse_walltime."""
        with patch("nfm_db.services.hpc_orchestration.parse_walltime", return_value=120) as mock_parse:
            result = orchestrator._parse_walltime("02:00:00")
            assert result == 120
            mock_parse.assert_called_once_with("02:00:00")


# ---------------------------------------------------------------------------
# poll_job_status
# ---------------------------------------------------------------------------


class TestPollJobStatus:
    """Tests for HPCOrchestrator.poll_job_status."""

    @pytest.mark.unit
    async def test_poll_returns_running(self, orchestrator: HPCOrchestrator) -> None:
        """Should return RUNNING when squeue output contains RUNNING."""
        with patch.object(orchestrator, "_execute_squeue", new_callable=AsyncMock, return_value="12345 RUNNING"):
            result = await orchestrator.poll_job_status("slurm-12345")
            assert result == "RUNNING"

    @pytest.mark.unit
    async def test_poll_returns_pending(self, orchestrator: HPCOrchestrator) -> None:
        """Should return PENDING when squeue output contains PENDING."""
        with patch.object(orchestrator, "_execute_squeue", new_callable=AsyncMock, return_value="12345 PENDING"):
            result = await orchestrator.poll_job_status("slurm-12345")
            assert result == "PENDING"

    @pytest.mark.unit
    async def test_poll_returns_completed_when_job_done(self, orchestrator: HPCOrchestrator) -> None:
        """Should return COMPLETED when squeue returns None and job is complete."""
        with patch.object(orchestrator, "_execute_squeue", new_callable=AsyncMock, return_value=None):
            with patch.object(orchestrator, "_check_job_completion", new_callable=AsyncMock, return_value=True):
                result = await orchestrator.poll_job_status("slurm-12345")
                assert result == "COMPLETED"

    @pytest.mark.unit
    async def test_poll_returns_failed_when_job_not_complete(self, orchestrator: HPCOrchestrator) -> None:
        """Should return FAILED when squeue returns None and job is not complete."""
        with patch.object(orchestrator, "_execute_squeue", new_callable=AsyncMock, return_value=None):
            with patch.object(orchestrator, "_check_job_completion", new_callable=AsyncMock, return_value=False):
                result = await orchestrator.poll_job_status("slurm-12345")
                assert result == "FAILED"

    @pytest.mark.unit
    async def test_poll_returns_failed_for_unknown_status(self, orchestrator: HPCOrchestrator) -> None:
        """Should return FAILED for unrecognized squeue output."""
        with patch.object(orchestrator, "_execute_squeue", new_callable=AsyncMock, return_value="12345 UNKNOWN_STATE"):
            result = await orchestrator.poll_job_status("slurm-12345")
            assert result == "FAILED"

    @pytest.mark.unit
    async def test_poll_returns_failed_on_exception(self, orchestrator: HPCOrchestrator) -> None:
        """Should return FAILED when _execute_squeue raises an exception."""
        with patch.object(
            orchestrator, "_execute_squeue", new_callable=AsyncMock, side_effect=RuntimeError("SSH timeout")
        ):
            result = await orchestrator.poll_job_status("slurm-12345")
            assert result == "FAILED"

    @pytest.mark.unit
    async def test_execute_squeue_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_execute_squeue should delegate to module-level execute_squeue."""
        with patch("nfm_db.services.hpc_orchestration.execute_squeue", new_callable=AsyncMock, return_value="output") as mock_exec:
            result = await orchestrator._execute_squeue("slurm-12345")
            assert result == "output"
            mock_exec.assert_called_once_with(orchestrator.ssh_manager, "slurm-12345")

    @pytest.mark.unit
    async def test_update_job_status_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """update_job_status should delegate to module-level update_job_status."""
        with patch("nfm_db.services.hpc_orchestration.update_job_status", new_callable=AsyncMock) as mock_update:
            await orchestrator.update_job_status("task-1", "slurm-12345")
            mock_update.assert_called_once_with(orchestrator.ssh_manager, "task-1", "slurm-12345")

    @pytest.mark.unit
    async def test_check_job_completion_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_check_job_completion should delegate to module-level check_job_completion."""
        with patch("nfm_db.services.hpc_orchestration.check_job_completion", new_callable=AsyncMock, return_value=True) as mock_check:
            result = await orchestrator._check_job_completion("task-1")
            assert result is True
            mock_check.assert_called_once_with(orchestrator.ssh_manager, "task-1")

    @pytest.mark.unit
    async def test_get_active_jobs_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_get_active_jobs should delegate to module-level get_active_jobs."""
        mock_jobs = [MagicMock(verification_job_id="v1", hpc_job_id="slurm-1")]
        with patch("nfm_db.services.hpc_orchestration.get_active_jobs", new_callable=AsyncMock, return_value=mock_jobs) as mock_get:
            result = await orchestrator._get_active_jobs()
            assert result == mock_jobs
            mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# sync_all_active_jobs
# ---------------------------------------------------------------------------


class TestSyncAllActiveJobs:
    """Tests for HPCOrchestrator.sync_all_active_jobs."""

    @pytest.mark.unit
    async def test_syncs_all_active_jobs(self, orchestrator: HPCOrchestrator) -> None:
        """Should sync status for each active job."""
        job1 = MagicMock(verification_job_id="v1", hpc_job_id="slurm-1")
        job2 = MagicMock(verification_job_id="v2", hpc_job_id="slurm-2")

        with patch.object(orchestrator, "_get_active_jobs", new_callable=AsyncMock, return_value=[job1, job2]):
            with patch.object(orchestrator, "update_job_status", new_callable=AsyncMock) as mock_update:
                await orchestrator.sync_all_active_jobs()
                assert mock_update.call_count == 2

    @pytest.mark.unit
    async def test_sync_continues_on_individual_job_failure(self, orchestrator: HPCOrchestrator) -> None:
        """Should continue syncing remaining jobs when one fails."""
        job1 = MagicMock(verification_job_id="v1", hpc_job_id="slurm-1")
        job2 = MagicMock(verification_job_id="v2", hpc_job_id="slurm-2")

        with patch.object(orchestrator, "_get_active_jobs", new_callable=AsyncMock, return_value=[job1, job2]):
            with patch.object(
                orchestrator, "update_job_status", new_callable=AsyncMock, side_effect=[RuntimeError("fail"), None]
            ):
                await orchestrator.sync_all_active_jobs()
                # Should not raise

    @pytest.mark.unit
    async def test_sync_handles_empty_active_jobs(self, orchestrator: HPCOrchestrator) -> None:
        """Should handle no active jobs gracefully."""
        with patch.object(orchestrator, "_get_active_jobs", new_callable=AsyncMock, return_value=[]):
            with patch.object(orchestrator, "update_job_status", new_callable=AsyncMock) as mock_update:
                await orchestrator.sync_all_active_jobs()
                mock_update.assert_not_called()

    @pytest.mark.unit
    async def test_sync_handles_get_active_jobs_failure(self, orchestrator: HPCOrchestrator) -> None:
        """Should not raise when _get_active_jobs fails."""
        with patch.object(
            orchestrator, "_get_active_jobs", new_callable=AsyncMock, side_effect=Exception("DB error")
        ):
            await orchestrator.sync_all_active_jobs()
            # Should not raise


# ---------------------------------------------------------------------------
# File transfer delegates
# ---------------------------------------------------------------------------


class TestFileTransferDelegates:
    """Tests for HPCOrchestrator file transfer delegate methods."""

    @pytest.mark.unit
    async def test_upload_file_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """upload_file should delegate to module-level _upload_file."""
        with patch("nfm_db.services.hpc_orchestration._upload_file", new_callable=AsyncMock, return_value=True) as mock_upload:
            result = await orchestrator.upload_file("task-1", "/local/file", "/remote/file")
            assert result is True
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/local/file", "/remote/file")

    @pytest.mark.unit
    async def test_upload_files_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """upload_files should delegate to module-level _upload_files."""
        files = ["/local/f1", "/local/f2"]
        expected = {"f1": True, "f2": True}
        with patch("nfm_db.services.hpc_orchestration._upload_files", new_callable=AsyncMock, return_value=expected) as mock_upload:
            result = await orchestrator.upload_files("task-1", files)
            assert result == expected
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", files)

    @pytest.mark.unit
    async def test_create_task_directory_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_create_task_directory should delegate to module-level _create_task_directory."""
        with patch("nfm_db.services.hpc_orchestration._create_task_directory", new_callable=AsyncMock) as mock_create:
            await orchestrator._create_task_directory("task-1")
            mock_create.assert_called_once_with(orchestrator.ssh_manager, "task-1")

    @pytest.mark.unit
    async def test_download_file_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """download_file should delegate to module-level _download_file."""
        with patch("nfm_db.services.hpc_orchestration._download_file", new_callable=AsyncMock, return_value="/local/path") as mock_dl:
            result = await orchestrator.download_file("task-1", "/remote/file", "/local/path")
            assert result == "/local/path"
            mock_dl.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/remote/file", "/local/path")

    @pytest.mark.unit
    async def test_download_results_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """download_results should delegate to module-level _download_results."""
        expected = {"file1": "/local/file1"}
        with patch("nfm_db.services.hpc_orchestration._download_results", new_callable=AsyncMock, return_value=expected) as mock_dl:
            result = await orchestrator.download_results("task-1")
            assert result == expected
            mock_dl.assert_called_once_with(orchestrator.ssh_manager, "task-1")

    @pytest.mark.unit
    async def test_verify_checksum_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """verify_checksum should delegate to module-level _verify_checksum."""
        with patch("nfm_db.services.hpc_orchestration._verify_checksum", new_callable=AsyncMock, return_value=True) as mock_verify:
            result = await orchestrator.verify_checksum("/local/file", "abc123")
            assert result is True
            mock_verify.assert_called_once_with("/local/file", "abc123")

    @pytest.mark.unit
    async def test_get_remote_checksum_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """get_remote_checksum should delegate to module-level _get_remote_checksum."""
        with patch("nfm_db.services.hpc_orchestration._get_remote_checksum", new_callable=AsyncMock, return_value="sha256:abc") as mock_checksum:
            result = await orchestrator.get_remote_checksum("task-1", "/remote/file")
            assert result == "sha256:abc"
            mock_checksum.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/remote/file")

    @pytest.mark.unit
    async def test_save_to_object_storage_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """save_to_object_storage should delegate to module-level _save_to_object_storage."""
        files = {"file1": "/local/file1"}
        expected = {"file1": "storage://path"}
        with patch("nfm_db.services.hpc_orchestration._save_to_object_storage", new_callable=AsyncMock, return_value=expected) as mock_save:
            result = await orchestrator.save_to_object_storage("task-1", files)
            assert result == expected
            mock_save.assert_called_once_with("task-1", files)

    @pytest.mark.unit
    async def test_save_metadata_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """_save_metadata should delegate to module-level _save_metadata."""
        metadata = {"file1": {"size": 1024}}
        with patch("nfm_db.services.hpc_orchestration._save_metadata", new_callable=AsyncMock) as mock_save:
            await orchestrator._save_metadata("task-1", metadata)
            mock_save.assert_called_once_with("task-1", metadata)

    @pytest.mark.unit
    async def test_upload_file_with_retry_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """upload_file_with_retry should delegate to module-level _upload_file_with_retry."""
        with patch("nfm_db.services.hpc_orchestration._upload_file_with_retry", new_callable=AsyncMock, return_value=True) as mock_upload:
            result = await orchestrator.upload_file_with_retry("task-1", "/local/file", "/remote/file", max_retries=5)
            assert result is True
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/local/file", "/remote/file", 5)

    @pytest.mark.unit
    async def test_upload_file_with_resume_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """upload_file_with_resume should delegate to module-level _upload_file_with_resume."""
        with patch("nfm_db.services.hpc_orchestration._upload_file_with_resume", new_callable=AsyncMock, return_value=True) as mock_upload:
            result = await orchestrator.upload_file_with_resume("task-1", "/local/file", "/remote/file", resume_position=1024)
            assert result is True
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/local/file", "/remote/file", 1024)

    @pytest.mark.unit
    async def test_get_remote_file_position_delegates(self, orchestrator: HPCOrchestrator) -> None:
        """get_remote_file_position should delegate to module-level _get_remote_file_position."""
        with patch("nfm_db.services.hpc_orchestration._get_remote_file_position", new_callable=AsyncMock, return_value=2048) as mock_pos:
            result = await orchestrator.get_remote_file_position("task-1", "/remote/file")
            assert result == 2048
            mock_pos.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/remote/file")

    @pytest.mark.unit
    async def test_upload_file_default_max_retries(self, orchestrator: HPCOrchestrator) -> None:
        """upload_file_with_retry should pass default max_retries=3."""
        with patch("nfm_db.services.hpc_orchestration._upload_file_with_retry", new_callable=AsyncMock, return_value=True) as mock_upload:
            await orchestrator.upload_file_with_retry("task-1", "/local", "/remote")
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/local", "/remote", 3)

    @pytest.mark.unit
    async def test_upload_file_with_resume_default_position(self, orchestrator: HPCOrchestrator) -> None:
        """upload_file_with_resume should pass default resume_position=0."""
        with patch("nfm_db.services.hpc_orchestration._upload_file_with_resume", new_callable=AsyncMock, return_value=True) as mock_upload:
            await orchestrator.upload_file_with_resume("task-1", "/local", "/remote")
            mock_upload.assert_called_once_with(orchestrator.ssh_manager, "task-1", "/local", "/remote", 0)


# ---------------------------------------------------------------------------
# Constructor edge cases
# ---------------------------------------------------------------------------


class TestConstructorEdgeCases:
    """Tests for HPCOrchestrator constructor edge cases."""

    @pytest.mark.unit
    def test_empty_hosts_list(self) -> None:
        """Should handle empty hosts list."""
        config = SSHConnectionConfig(
            hosts=(),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.hpc_cluster == ""

    @pytest.mark.unit
    def test_tuple_hosts_converted_to_list(self) -> None:
        """Should convert tuple hosts to list for SSHConnectionManager."""
        config = SSHConnectionConfig(
            hosts=("h1.example.com", "h2.example.com"),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert isinstance(orchestrator.ssh_manager.hosts, list)

    @pytest.mark.unit
    def test_list_hosts_passed_directly(self) -> None:
        """Should pass list hosts directly to SSHConnectionManager."""
        config = SSHConnectionConfig(
            hosts=["h1.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.ssh_manager.hosts == ["h1.example.com"]

    @pytest.mark.unit
    def test_backup_tuple_hosts_converted_to_list(self) -> None:
        """Should convert tuple backup_hosts to list."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup1.example.com", "backup2.example.com"),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.failover_manager.has_backup is True

    @pytest.mark.unit
    def test_no_backup_when_missing_username(self) -> None:
        """Should not create backup manager when backup_username is missing."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup.example.com",),
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.failover_manager.has_backup is False

    @pytest.mark.unit
    def test_no_backup_when_missing_ssh_key(self) -> None:
        """Should not create backup manager when backup_ssh_key_path is missing."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup.example.com",),
            backup_username="backup_user",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.failover_manager.has_backup is False

    @pytest.mark.unit
    def test_no_backup_when_empty_hosts(self) -> None:
        """Should not create backup manager when backup_hosts is empty."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=(),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True,
        )
        orchestrator = HPCOrchestrator(config)
        assert orchestrator.failover_manager.has_backup is False
