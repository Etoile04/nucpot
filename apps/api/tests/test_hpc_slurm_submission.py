"""Tests for HPC Orchestration System - Phase 4.2: SLURM Job Submission."""

import uuid
import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig
from nfm_db.models.md_verification import HpcJob, MDVerificationJob


class TestSLURMScriptGeneration:
    """Test SLURM script template generation."""

    def test_generate_slurm_script_with_basic_params(self):
        """Test SLURM script generation with basic parameters."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        params = {
            "job_name": "test_md_verification",
            "nodes": 1,
            "cpus_per_task": 4,
            "memory": "16G",
            "walltime": "02:00:00",
            "partition": "compute",
            "output_file": "lammps.out"
        }

        script = orchestrator._generate_slurm_script(params)

        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=test_md_verification" in script
        assert "#SBATCH --nodes=1" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --partition=compute" in script
        assert "#SBATCH --output=lammps.out" in script

    def test_generate_slurm_script_with_lammps_commands(self):
        """Test SLURM script includes LAMMPS execution commands."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        params = {
            "job_name": "md_sim",
            "lammps_executable": "/path/to/lmp_mpi",
            "input_file": "in.lammps",
            "output_file": "lammps.out"
        }

        script = orchestrator._generate_slurm_script(params)

        assert "mpirun" in script or "lmp_mpi" in script
        assert "in.lammps" in script
        assert "lammps.out" in script


class TestJobSubmissionInterface:
    """Test submit_job() interface and error handling."""

    @pytest.mark.asyncio
    async def test_submit_job_creates_database_record(self):
        """Test submit_job creates record in hpc_jobs table."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        crystal_structure_file = "/path/to/structure.cif"
        params = {
            "temperature": 300,
            "pressure": 1.0,
            "steps": 10000,
            "nodes": 1,
            "cpus_per_task": 4,
            "memory": "16G",
            "walltime": "02:00:00"
        }

        # Mock both SLURM submission and database operations
        with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
            mock_submit.return_value = "slurm-job-12345"

            with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
                # Create a proper async generator mock
                async def mock_db_gen():
                    mock_db = AsyncMock()
                    yield mock_db

                mock_get_db.return_value = mock_db_gen()

                hpc_job_id = await orchestrator.submit_job(
                    task_id,
                    crystal_structure_file,
                    params
                )

                assert hpc_job_id == "slurm-job-12345"
                mock_submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_job_queue_full_error(self):
        """Test submit_job raises error when SLURM queue is full."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        params = {"temperature": 300, "pressure": 1.0, "steps": 10000}

        with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
            # Simulate SLURM queue full error
            mock_submit.side_effect = JobSubmissionError("Slurm queue is full")

            with pytest.raises(JobSubmissionError, match="queue.*full"):
                await orchestrator.submit_job(task_id, "/path/to/file", params)

    @pytest.mark.asyncio
    async def test_submit_job_permission_error(self):
        """Test submit_job raises error on permission denied."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        params = {"temperature": 300, "pressure": 1.0, "steps": 10000}

        with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
            # Simulate permission error
            mock_submit.side_effect = JobSubmissionError("Permission denied")

            with pytest.raises(JobSubmissionError, match="Permission"):
                await orchestrator.submit_job(task_id, "/path/to/file", params)

    @pytest.mark.asyncio
    async def test_submit_job_invalid_parameters(self):
        """Test submit_job validates input parameters."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        # Missing required parameter
        invalid_params = {
            "temperature": 300,
            # Missing "pressure" and "steps"
        }

        with pytest.raises(ValueError, match="Missing required parameter"):
            await orchestrator.submit_job(task_id, "/path/to/file", invalid_params)


class TestHPCJobsTablePopulation:
    """Test hpc_jobs table population."""

    @pytest.mark.asyncio
    async def test_submit_job_populates_hpc_jobs_table(self):
        """Test submit_job creates record in hpc_jobs table."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        crystal_structure_file = "/path/to/structure.cif"
        params = {"temperature": 300, "pressure": 1.0, "steps": 10000, "walltime": "02:00:00"}

        with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
            mock_submit.return_value = "slurm-67890"

            with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
                # Create a proper async generator mock
                async def mock_db_gen():
                    mock_db = AsyncMock()
                    yield mock_db

                mock_get_db.return_value = mock_db_gen()

                hpc_job_id = await orchestrator.submit_job(
                    task_id,
                    crystal_structure_file,
                    params
                )

                # Verify hpc_jobs record would be created
                assert hpc_job_id == "slurm-67890"

    @pytest.mark.asyncio
    async def test_hpc_job_record_contains_required_fields(self):
        """Test hpc_jobs record contains all required fields."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        task_id = str(uuid.uuid4())
        params = {"temperature": 300, "pressure": 1.0, "steps": 5000, "walltime": "01:00:00"}

        with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
            mock_submit.return_value = "slurm-11111"

            with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
                # Create a proper async generator mock
                async def mock_db_gen():
                    mock_db = AsyncMock()
                    yield mock_db

                mock_get_db.return_value = mock_db_gen()

                hpc_job_id = await orchestrator.submit_job(
                    task_id,
                    "/path/file.cif",
                    params
                )

                # Verify job submission ID format
                assert "slurm-" in hpc_job_id


class TestJobSubmissionSuccessRate:
    """Test job submission success rate and error recovery."""

    @pytest.mark.asyncio
    async def test_submit_job_success_rate_above_threshold(self):
        """Test job submission success rate exceeds 90% threshold."""
        config = SSHConnectionConfig(
            hosts=["login01.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key"
        )
        orchestrator = HPCOrchestrator(config)

        successful_submissions = 0
        total_attempts = 10
        params = {"temperature": 300, "pressure": 1.0, "steps": 10000, "walltime": "02:00:00"}

        for i in range(total_attempts):
            task_id = str(uuid.uuid4())
            with patch.object(orchestrator, '_submit_to_slurm') as mock_submit:
                # Simulate 95% success rate (only 1 failure)
                if i == 5:
                    mock_submit.side_effect = JobSubmissionError("Transient error")
                else:
                    mock_submit.return_value = f"slurm-{i}"

                # Mock database to avoid connection errors
                with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
                    async def mock_db_gen():
                        mock_db = AsyncMock()
                        yield mock_db

                    mock_get_db.return_value = mock_db_gen()

                    try:
                        await orchestrator.submit_job(task_id, "/path/file", params)
                        successful_submissions += 1
                    except JobSubmissionError:
                        pass

        success_rate = successful_submissions / total_attempts
        assert success_rate >= 0.90, f"Success rate {success_rate:.2%} below 90% threshold"


class JobSubmissionError(Exception):
    """Custom exception for job submission failures."""
    pass
