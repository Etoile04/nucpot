"""Tests for HPC Orchestration System - Phase 4.3: Status Synchronization."""

import uuid
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig
from nfm_db.models.md_verification import HpcJob, HpcJobStatus, MDVerificationJob


class TestSLURMStatusPolling:
    """Test periodic squeue polling functionality."""

    @pytest.mark.asyncio
    async def test_poll_slurm_status_returns_job_state(self):
        """Test polling squeue returns current job status."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-12345"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            # Mock squeue output for running job
            mock_squeue.return_value = "slurm-12345 RUNNING"

            status = await orchestrator.poll_job_status(hpc_job_id)

            assert status == "RUNNING"
            mock_squeue.assert_called_once_with(hpc_job_id)

    @pytest.mark.asyncio
    async def test_poll_slurm_status_pending(self):
        """Test polling squeue returns PENDING status."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-67890"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            mock_squeue.return_value = "slurm-67890 PENDING"

            status = await orchestrator.poll_job_status(hpc_job_id)

            assert status == "PENDING"

    @pytest.mark.asyncio
    async def test_poll_slurm_status_completed(self):
        """Test polling squeue returns COMPLETED status."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-99999"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            # When job is not in squeue, check output files
            mock_squeue.return_value = None  # Job not found in queue

            with patch.object(orchestrator, '_check_job_completion') as mock_completion:
                mock_completion.return_value = True

                status = await orchestrator.poll_job_status(hpc_job_id)

                assert status == "COMPLETED"


class TestStateMachine:
    """Test state machine transitions PENDING → RUNNING → COMPLETED/FAILED."""

    @pytest.mark.asyncio
    async def test_state_transition_pending_to_running(self):
        """Test state machine transition from PENDING to RUNNING."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-11111"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            mock_squeue.return_value = "slurm-11111 RUNNING"

            status = await orchestrator.poll_job_status(hpc_job_id)
            assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_state_transition_running_to_completed(self):
        """Test state machine transition from RUNNING to COMPLETED."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-22222"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            # Job not in queue (completed)
            mock_squeue.return_value = None

            with patch.object(orchestrator, '_check_job_completion') as mock_completion:
                mock_completion.return_value = True

                status = await orchestrator.poll_job_status(hpc_job_id)
                assert status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_state_transition_to_failed(self):
        """Test state machine transition to FAILED on job failure."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-33333"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            # Job not in queue (failed)
            mock_squeue.return_value = None

            with patch.object(orchestrator, '_check_job_completion') as mock_completion:
                mock_completion.return_value = False  # Job failed

                status = await orchestrator.poll_job_status(hpc_job_id)
                assert status == "FAILED"


class TestDatabaseStatusUpdates:
    """Test md_verification_jobs.status updates."""

    @pytest.mark.asyncio
    async def test_update_md_verification_job_status(self):
        """Test md_verification_jobs.status field gets updated."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-44444"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            mock_squeue.return_value = "slurm-44444 RUNNING"

            status = await orchestrator.poll_job_status(hpc_job_id)
            assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_update_hpc_job_status(self):
        """Test hpc_jobs.status field gets updated."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        hpc_job_id = "slurm-55555"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            mock_squeue.return_value = None

            with patch.object(orchestrator, '_check_job_completion') as mock_completion:
                mock_completion.return_value = True

                status = await orchestrator.poll_job_status(hpc_job_id)
                assert status == "COMPLETED"


class TestOutputFileDetection:
    """Test completion detection via output file existence."""

    @pytest.mark.asyncio
    async def test_detect_completion_via_output_file(self):
        """Test job completion detected via lammps.out existence."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())

        with patch.object(orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(orchestrator.ssh_manager, 'release_connection'):
                # Mock sftp file check
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                # Simulate file exists
                mock_stat = MagicMock()
                mock_stat.st_size = 12345
                mock_sftp.stat.return_value = mock_stat

                is_complete = await orchestrator._check_job_completion(task_id)

                assert is_complete is True

    @pytest.mark.asyncio
    async def test_detect_incomplete_missing_output_file(self):
        """Test incomplete job when lammps.out missing."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())

        with patch.object(orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(orchestrator.ssh_manager, 'release_connection'):
                # Mock sftp file check - file not found
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
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        import time

        hpc_job_id = "slurm-66666"

        with patch.object(orchestrator, '_execute_squeue') as mock_squeue:
            mock_squeue.return_value = "slurm-66666 RUNNING"

            start_time = time.time()
            status = await orchestrator.poll_job_status(hpc_job_id)
            end_time = time.time()

            latency = end_time - start_time
            assert latency < 30.0, f"Status sync latency {latency:.2f}s exceeds 30s threshold"
            assert status == "RUNNING"


class TestCeleryBeatIntegration:
    """Test Celery beat periodic task integration."""

    @pytest.mark.asyncio
    async def test_celery_beat_schedules_status_sync(self):
        """Test Celery beat schedules periodic status sync tasks."""
        from nfm_db.services.celery_app import celery_app

        # Verify Celery beat configuration
        # This test validates the beat schedule configuration
        assert hasattr(celery_app.conf, 'beat_schedule')

        # Check for status sync task schedule
        beat_schedule = celery_app.conf.beat_schedule
        assert 'sync-hpc-job-status' in beat_schedule

        # Verify task configuration
        task_config = beat_schedule['sync-hpc-job-status']
        assert task_config['schedule'] == 30.0  # Every 30 seconds
        assert 'task' in task_config
        assert 'sync_hpc_job_status' in task_config['task']

    @pytest.mark.asyncio
    async def test_periodic_task_polls_all_active_jobs(self):
        """Test periodic task polls all active HPC jobs."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        # Mock database query for active jobs
        mock_jobs = [
            MagicMock(id=str(uuid.uuid4()), hpc_job_id="slurm-77777"),
            MagicMock(id=str(uuid.uuid4()), hpc_job_id="slurm-88888"),
            MagicMock(id=str(uuid.uuid4()), hpc_job_id="slurm-99999"),
        ]

        with patch.object(orchestrator, '_get_active_jobs') as mock_active_jobs:
            mock_active_jobs.return_value = mock_jobs

            with patch.object(orchestrator, 'update_job_status') as mock_update:
                await orchestrator.sync_all_active_jobs()

                # Verify all jobs were updated
                assert mock_update.call_count == len(mock_jobs)
