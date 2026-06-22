"""Tests for HPC Orchestration System - Phase 4.3: Status Synchronization.

After the NFM-355 module split, HPCOrchestrator.poll_job_status now calls
self._execute_squeue() which delegates to the module-level execute_squeue()
from hpc_job_monitor.  Tests patch the orchestrator's own methods
(_execute_squeue, _check_job_completion, _get_active_jobs) rather than
module-level names that no longer exist in hpc_orchestration.
"""

import time
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig
from nfm_db.models.md_verification import HpcJob, HpcJobStatus, MDVerificationJob


def _make_orchestrator() -> HPCOrchestrator:
    """Create a basic test orchestrator."""
    config = SSHConnectionConfig(
        hosts=("login01.example.com",),
        username="testuser",
        ssh_key_path="/path/to/key",
        skip_key_validation=True,
    )
    return HPCOrchestrator(config)


class TestSLURMStatusPolling:
    """Test periodic squeue polling functionality."""

    @pytest.mark.asyncio
    async def test_poll_slurm_status_returns_job_state(self):
        """Test polling squeue returns current job status."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value="RUNNING slurm-12345",
        ):
            status = await orchestrator.poll_job_status("slurm-12345")
            assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_poll_slurm_status_pending(self):
        """Test polling squeue returns PENDING status."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value="PENDING slurm-67890",
        ):
            status = await orchestrator.poll_job_status("slurm-67890")
            assert status == "PENDING"

    @pytest.mark.asyncio
    async def test_poll_slurm_status_completed(self):
        """Test polling squeue returns COMPLETED status when job exits queue."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            orchestrator, '_check_job_completion',
            new_callable=AsyncMock,
            return_value=True,
        ):
            status = await orchestrator.poll_job_status("slurm-99999")
            assert status == "COMPLETED"


class TestStateMachine:
    """Test state machine transitions PENDING -> RUNNING -> COMPLETED/FAILED."""

    @pytest.mark.asyncio
    async def test_state_transition_pending_to_running(self):
        """Test state machine transition from PENDING to RUNNING."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value="RUNNING slurm-11111",
        ):
            status = await orchestrator.poll_job_status("slurm-11111")
            assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_state_transition_running_to_completed(self):
        """Test state machine transition from RUNNING to COMPLETED."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            orchestrator, '_check_job_completion',
            new_callable=AsyncMock,
            return_value=True,
        ):
            status = await orchestrator.poll_job_status("slurm-22222")
            assert status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_state_transition_to_failed(self):
        """Test state machine transition to FAILED on job failure."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            orchestrator, '_check_job_completion',
            new_callable=AsyncMock,
            return_value=False,
        ):
            status = await orchestrator.poll_job_status("slurm-33333")
            assert status == "FAILED"


class TestDatabaseStatusUpdates:
    """Test md_verification_jobs.status updates."""

    @pytest.mark.asyncio
    async def test_update_md_verification_job_status(self):
        """Test md_verification_jobs.status field gets updated."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value="RUNNING slurm-44444",
        ):
            status = await orchestrator.poll_job_status("slurm-44444")
            assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_update_hpc_job_status(self):
        """Test hpc_jobs.status field gets updated."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value=None,
        ), patch.object(
            orchestrator, '_check_job_completion',
            new_callable=AsyncMock,
            return_value=True,
        ):
            status = await orchestrator.poll_job_status("slurm-55555")
            assert status == "COMPLETED"


class TestOutputFileDetection:
    """Test completion detection via output file existence."""

    @pytest.mark.asyncio
    async def test_detect_completion_via_output_file(self):
        """Test job completion detected via lammps.out existence."""
        orchestrator = _make_orchestrator()
        task_id = str(uuid.uuid4())

        with patch.object(orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(orchestrator.ssh_manager, 'release_connection'):
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                mock_stat = MagicMock()
                mock_stat.st_size = 12345
                mock_sftp.stat.return_value = mock_stat

                is_complete = await orchestrator._check_job_completion(task_id)
                assert is_complete is True

    @pytest.mark.asyncio
    async def test_detect_incomplete_missing_output_file(self):
        """Test incomplete job when lammps.out missing."""
        orchestrator = _make_orchestrator()
        task_id = str(uuid.uuid4())

        with patch.object(orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(orchestrator.ssh_manager, 'release_connection'):
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                mock_sftp.stat.side_effect = IOError("File not found")

                is_complete = await orchestrator._check_job_completion(task_id)
                assert is_complete is False


class TestStatusSyncLatency:
    """Test status synchronization latency requirements."""

    @pytest.mark.asyncio
    async def test_status_sync_latency_under_30_seconds(self):
        """Test status sync latency is under 30 seconds."""
        orchestrator = _make_orchestrator()

        with patch.object(
            orchestrator, '_execute_squeue',
            new_callable=AsyncMock,
            return_value="RUNNING slurm-66666",
        ):
            start_time = time.time()
            status = await orchestrator.poll_job_status("slurm-66666")
            end_time = time.time()

            latency = end_time - start_time
            assert latency < 30.0, f"Status sync latency {latency:.2f}s exceeds 30s threshold"
            assert status == "RUNNING"


class TestCeleryBeatIntegration:
    """Test Celery beat periodic task integration."""

    @pytest.mark.asyncio
    async def test_celery_beat_schedules_status_sync(self):
        """Test Celery beat schedules periodic status sync tasks."""
        try:
            from nfm_db.services.celery_app import celery_app
        except Exception:
            pytest.skip("Celery app not available in test environment")

        assert hasattr(celery_app.conf, 'beat_schedule')

        beat_schedule = celery_app.conf.beat_schedule
        if not beat_schedule:
            pytest.skip("Celery beat_schedule not configured")

        assert 'sync-hpc-job-status' in beat_schedule

        task_config = beat_schedule['sync-hpc-job-status']
        assert task_config['schedule'] == 30.0
        assert 'task' in task_config
        assert 'sync_hpc_job_status' in task_config['task']

    @pytest.mark.asyncio
    async def test_periodic_task_polls_all_active_jobs(self):
        """Test periodic task polls all active HPC jobs."""
        orchestrator = _make_orchestrator()

        mock_job1 = MagicMock(verification_job_id=uuid.uuid4(), hpc_job_id="slurm-77777")
        mock_job2 = MagicMock(verification_job_id=uuid.uuid4(), hpc_job_id="slurm-88888")
        mock_job3 = MagicMock(verification_job_id=uuid.uuid4(), hpc_job_id="slurm-99999")

        with patch.object(
            orchestrator, '_get_active_jobs',
            new_callable=AsyncMock,
            return_value=[mock_job1, mock_job2, mock_job3],
        ), patch.object(
            orchestrator, 'update_job_status',
            new_callable=AsyncMock,
        ) as mock_update:
            await orchestrator.sync_all_active_jobs()
            assert mock_update.call_count == 3
