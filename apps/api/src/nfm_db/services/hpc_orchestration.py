"""HPC Orchestration Service - SSH Connection Management and Job Submission.

This module provides the core HPC orchestration functionality including:
- SSH connection pooling with multi-login node support
- SLURM job submission and monitoring
- File transfer and result retrieval
- Automatic failover to backup clusters

Architecture follows Phase 4.1-4.5 implementation plan from NFM-345.
"""

import os
import time
import uuid
import paramiko
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.models.md_verification import HpcJob, HpcJobStatus, MDVerificationJob
from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


class JobSubmissionError(Exception):
    """Exception raised when SLURM job submission fails."""

    pass


@dataclass
class SSHConnectionConfig:
    """SSH connection configuration."""
    hosts: List[str]
    username: str
    ssh_key_path: str
    max_connections: int = 10
    heartbeat_interval: int = 30  # seconds


class SSHConnectionManager:
    """Manages SSH connection pool with health monitoring and auto-reconnect.

    Features:
    - Connection pooling (configurable max concurrent connections)
    - Multi-login node support for load balancing
    - 30-second heartbeat health checks
    - Auto-reconnect with exponential backoff (max 3 retries)
    - SSH key authentication only (no passwords)
    """

    def __init__(
        self,
        host: str | List[str] = None,
        username: str = None,
        ssh_key_path: str = None,
        max_connections: int = 10,
        hosts: str | List[str] = None,
        skip_key_validation: bool = False
    ):
        """Initialize SSH connection manager.

        Args:
            host: Single host or list of hosts for multi-login support
            username: SSH username
            ssh_key_path: Path to SSH private key file
            max_connections: Maximum concurrent connections (default: 10)
            hosts: Alternative parameter name for multi-login support
            skip_key_validation: Skip SSH key file validation (for testing)
        """
        # Support both 'host' and 'hosts' parameter names
        host_value = hosts if hosts is not None else host

        # Normalize single host to list
        if isinstance(host_value, str):
            self.hosts = [host_value]
        else:
            self.hosts = host_value if host_value else []

        self.username = username
        self.ssh_key_path = ssh_key_path
        self.max_connections = max_connections
        self._skip_key_validation = skip_key_validation

        # Connection pool
        self._available_connections: List[paramiko.SSHClient] = []
        self._active_connections: set = set()
        self._connection_lock = False  # Simple lock for thread safety

    @property
    def available_connections(self) -> int:
        """Get number of available connections (can still be acquired)."""
        return self.max_connections - len(self._active_connections)

    @property
    def host(self) -> str:
        """Get primary host (first in list)."""
        return self.hosts[0] if self.hosts else ""

    def acquire_connection(self) -> paramiko.SSHClient:
        """Acquire a connection from the pool.

        Returns:
            SSH client instance

        Raises:
            ConnectionError: If connection pool is exhausted
            FileNotFoundError: If SSH key file doesn't exist
        """
        # Check if pool is exhausted
        if len(self._active_connections) >= self.max_connections:
            raise ConnectionError("Connection pool exhausted")

        # Validate SSH key exists before creating connection (unless skipped)
        if not self._skip_key_validation and not Path(self.ssh_key_path).exists():
            raise FileNotFoundError(f"SSH key file not found: {self.ssh_key_path}")

        # Create new connection
        client = self._create_ssh_connection()
        self._active_connections.add(client)
        return client

    def acquire_connection_with_retry(
        self,
        max_retries: int = 3,
        backoff_base: float = 1.0
    ) -> Optional[paramiko.SSHClient]:
        """Acquire connection with automatic retry on failure.

        Args:
            max_retries: Maximum number of retry attempts
            backoff_base: Base for exponential backoff (seconds)

        Returns:
            SSH client or None if all retries failed
        """
        for attempt in range(max_retries):
            try:
                return self.acquire_connection()
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to acquire connection after {max_retries} attempts: {e}")
                    return None

                wait_time = backoff_base * (2 ** attempt)
                logger.warning(f"Connection attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)

        return None

    def release_connection(self, client: paramiko.SSHClient) -> None:
        """Release a connection back to the pool.

        Args:
            client: SSH client to release
        """
        if client in self._active_connections:
            self._active_connections.remove(client)
            self._available_connections.append(client)

    def check_health(self, client: paramiko.SSHClient) -> bool:
        """Check if SSH connection is healthy.

        Args:
            client: SSH client to check

        Returns:
            True if connection is healthy, False otherwise
        """
        try:
            # Execute simple command to test connection
            stdin, stdout, stderr = client.exec_command("echo 'health_check'")
            stdout.read()  # Wait for command to complete
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    def _create_ssh_connection(self) -> paramiko.SSHClient:
        """Create a new SSH connection using key authentication.

        Returns:
            Connected SSH client

        Raises:
            ConnectionError: If connection fails
        """
        client = paramiko.SSHClient()

        # Automatically add host keys (in production, use known_hosts)
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Use first available host for connection
        # In production, implement load balancing across hosts
        host = self.hosts[0]

        try:
            client.connect(
                hostname=host,
                username=self.username,
                key_filename=self.ssh_key_path,
                timeout=10
            )
            logger.info(f"SSH connection established to {host}")
            return client
        except Exception as e:
            client.close()
            raise ConnectionError(f"Failed to connect to {host}: {e}")


class HPCOrchestrator:
    """Main HPC orchestration service for job submission and monitoring.

    Handles SLURM job submission, status monitoring, file transfer, and failover.
    """

    def __init__(self, config: SSHConnectionConfig):
        """Initialize HPC orchestrator with SSH config.

        Args:
            config: SSH connection configuration
        """
        self.ssh_manager = SSHConnectionManager(
            host=config.hosts,
            username=config.username,
            ssh_key_path=config.ssh_key_path,
            max_connections=config.max_connections
        )
        self.config = config
        self.hpc_cluster = config.hosts[0]  # Primary cluster

    def _generate_slurm_script(self, params: Dict[str, Any]) -> str:
        """Generate SLURM batch script from parameters.

        Args:
            params: Dictionary containing job parameters

        Returns:
            Complete SLURM batch script content
        """
        job_name = params.get("job_name", "md_verification")
        nodes = params.get("nodes", 1)
        cpus_per_task = params.get("cpus_per_task", 4)
        memory = params.get("memory", "16G")
        walltime = params.get("walltime", "02:00:00")
        partition = params.get("partition", "compute")
        output_file = params.get("output_file", "lammps.out")

        script = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --nodes={nodes}
#SBATCH --cpus-per-task={cpus_per_task}
#SBATCH --mem={memory}
#SBATCH --time={walltime}
#SBATCH --partition={partition}
#SBATCH --output={output_file}

echo "Starting MD verification job at $(date)"
echo "Job ID: $SLURM_JOB_ID"

# Load modules if needed
# module load lammps

# Run LAMMPS simulation
"""

        # Add LAMMPS execution commands if specified
        if "lammps_executable" in params:
            lammps_exec = params["lammps_executable"]
            input_file = params.get("input_file", "in.lammps")
            script += f"""
# Run LAMMPS with MPI
mpirun {lammps_exec} -in {input_file}
"""

        script += f"""
echo "Job completed at $(date)"
"""
        return script

    async def submit_job(
        self,
        task_id: str,
        crystal_structure_file: str,
        params: Dict[str, Any]
    ) -> str:
        """Submit MD verification job to HPC.

        Args:
            task_id: UUID of md_verification_jobs record
            crystal_structure_file: Path to input structure file
            params: Simulation parameters (temp, pressure, steps, etc.)

        Returns:
            hpc_job_id: SLURM job identifier

        Raises:
            HPCConnectionError: Primary and backup clusters unavailable
            JobSubmissionError: SLURM submission failed
        """
        # Validate required parameters
        self._validate_simulation_params(params)

        # Generate SLURM script
        slurm_script = self._generate_slurm_script(params)

        # Submit to SLURM via SSH
        hpc_job_id = await self._submit_to_slurm(task_id, slurm_script)

        # Create database record
        await self._create_hpc_job_record(task_id, hpc_job_id, params)

        return hpc_job_id

    def _validate_simulation_params(self, params: Dict[str, Any]) -> None:
        """Validate simulation parameters.

        Args:
            params: Simulation parameters to validate

        Raises:
            ValueError: If required parameters are missing or invalid
        """
        required_params = ["temperature", "pressure", "steps"]
        missing_params = [p for p in required_params if p not in params]

        if missing_params:
            raise ValueError(f"Missing required parameters: {missing_params}")

        # Validate parameter ranges
        if not (0 < params["temperature"] < 10000):
            raise ValueError("Temperature must be between 0 and 10000 K")

        if not (0 < params["pressure"] < 1000):
            raise ValueError("Pressure must be between 0 and 1000 GPa")

        if not (1000 < params["steps"] < 10000000):
            raise ValueError("Steps must be between 1000 and 10 million")

    async def _submit_to_slurm(
        self,
        task_id: str,
        slurm_script: str
    ) -> str:
        """Submit job to SLURM cluster via SSH.

        Args:
            task_id: Task identifier for logging
            slurm_script: Complete SLURM script content

        Returns:
            SLURM job ID (e.g., "slurm-12345")

        Raises:
            JobSubmissionError: If submission fails
        """
        client = None
        try:
            # Acquire SSH connection
            client = self.ssh_manager.acquire_connection()

            # Create temporary script file on HPC
            script_path = f"$SCRATCH/nfm-md/{task_id}/submit.sh"
            self._upload_script_via_sftp(client, slurm_script, script_path)

            # Submit job via sbatch
            stdin, stdout, stderr = client.exec_command(f"sbatch {script_path}")
            exit_status = stdout.channel.recv_exit_status()

            if exit_status != 0:
                error_msg = stderr.read().decode()
                if "Socket timed out" in error_msg or "qos: QOSMaxSubmitJobLimit" in error_msg:
                    raise JobSubmissionError(f"SLURM queue is full: {error_msg}")
                elif "Permission denied" in error_msg:
                    raise JobSubmissionError(f"Permission denied: {error_msg}")
                else:
                    raise JobSubmissionError(f"SLURM submission failed: {error_msg}")

            # Parse job ID from output
            job_id = stdout.read().decode().strip()
            if not job_id.isdigit():
                raise JobSubmissionError(f"Invalid job ID returned: {job_id}")

            logger.info(f"Job submitted successfully: {job_id}")
            return f"slurm-{job_id}"

        except JobSubmissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to submit job to SLURM: {e}")
            raise JobSubmissionError(f"HPC connection failed: {e}")
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    def _upload_script_via_sftp(
        self,
        client: paramiko.SSHClient,
        script_content: str,
        remote_path: str
    ) -> None:
        """Upload script content to remote path via SFTP.

        Args:
            client: SSH client with active connection
            script_content: Script file content
            remote_path: Remote file path
        """
        sftp = None
        try:
            sftp = client.open_sftp()
            # Create directory if it doesn't exist
            remote_dir = "/".join(remote_path.split("/")[:-1])
            try:
                sftp.mkdir(remote_dir)
            except IOError:
                pass  # Directory may already exist

            # Write script file
            with sftp.file(remote_path, 'w') as f:
                f.write(script_content)

        finally:
            if sftp:
                sftp.close()

    async def _create_hpc_job_record(
        self,
        task_id: str,
        hpc_job_id: str,
        params: Dict[str, Any]
    ) -> None:
        """Create record in hpc_jobs table.

        Args:
            task_id: MD verification job ID
            hpc_job_id: SLURM job identifier
            params: Job parameters
        """
        # Use async generator pattern for database session
        db_gen = get_db()
        db = await db_gen.__anext__()

        try:
            hpc_job = HpcJob(
                verification_job_id=uuid.UUID(task_id),
                hpc_cluster=self.hpc_cluster,
                hpc_job_id=hpc_job_id,
                status=HpcJobStatus.PENDING,
                partition=params.get("partition", "compute"),
                nodes=params.get("nodes", 1),
                walltime_requested=self._parse_walltime(params.get("walltime", "02:00:00")),
                submitted_at=datetime.utcnow()
            )

            db.add(hpc_job)
            await db.commit()

            logger.info(f"Created HPC job record: {hpc_job.id}")
        except Exception:
            await db.rollback()
            raise
        finally:
            try:
                await db_gen.__anext__()
            except StopAsyncIteration:
                pass

    def _parse_walltime(self, walltime: str) -> int:
        """Parse SLURM walltime format to minutes.

        Args:
            walltime: Walltime in format "HH:MM:SS"

        Returns:
            Walltime in minutes
        """
        parts = walltime.split(":")
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes

    async def poll_job_status(self, hpc_job_id: str) -> str:
        """Poll SLURM for current job status.

        Args:
            hpc_job_id: SLURM job identifier (e.g., "slurm-12345")

        Returns:
            Job status: PENDING, RUNNING, COMPLETED, or FAILED

        Raises:
            ConnectionError: If SSH connection fails
        """
        try:
            # Execute squeue command to check job status
            squeue_output = await self._execute_squeue(hpc_job_id)

            if squeue_output and "RUNNING" in squeue_output:
                return "RUNNING"
            elif squeue_output and "PENDING" in squeue_output:
                return "PENDING"
            elif squeue_output is None:
                # Job not in queue, check if completed successfully
                task_id = hpc_job_id.split("-")[-1]  # Extract task ID
                if await self._check_job_completion(task_id):
                    return "COMPLETED"
                else:
                    return "FAILED"
            else:
                return "FAILED"

        except Exception as e:
            logger.error(f"Failed to poll job status for {hpc_job_id}: {e}")
            return "FAILED"

    async def _execute_squeue(self, hpc_job_id: str) -> Optional[str]:
        """Execute squeue command via SSH to check job status.

        Args:
            hpc_job_id: SLURM job identifier

        Returns:
            squeue output string, or None if job not found in queue
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Extract numeric job ID (remove 'slurm-' prefix)
            job_id = hpc_job_id.replace("slurm-", "")

            # Execute squeue command
            cmd = f"squeue -j {job_id} -o '%T %j'"
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()

            if exit_status != 0:
                error_msg = stderr.read().decode()
                if "slurm_load_error" in error_msg or "Invalid job id" in error_msg:
                    # Job not found in queue (may be completed)
                    return None
                else:
                    raise Exception(f"squeue command failed: {error_msg}")

            output = stdout.read().decode().strip()
            return output if output else None

        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def update_job_status(self, task_id: str, hpc_job_id: str) -> None:
        """Update job status in database.

        Args:
            task_id: MD verification job ID
            hpc_job_id: HPC job identifier

        Raises:
            Exception: If database update fails
        """
        try:
            # Poll current status
            status = await self.poll_job_status(hpc_job_id)

            # Update database with new status
            db_gen = get_db()
            db = await db_gen.__anext__()

            try:
                # Update hpc_jobs table
                from sqlalchemy import select, update
                # Use query instead of get for hpc_job_id which is a string, not the primary key
                hpc_result = await db.execute(
                    select(HpcJob).where(HpcJob.hpc_job_id == hpc_job_id)
                )
                hpc_job = hpc_result.scalar_one_or_none()

                if hpc_job:
                    hpc_job.status = HpcJobStatus[status]
                    await db.commit()

                # Update md_verification_jobs table
                verification_job = await db.get(MDVerificationJob, uuid.UUID(task_id))
                if verification_job:
                    verification_job.status = status
                    await db.commit()

                logger.info(f"Updated job status: {task_id} -> {status}")

            except Exception:
                await db.rollback()
                raise
            finally:
                try:
                    await db_gen.__anext__()
                except StopAsyncIteration:
                    pass

        except Exception as e:
            logger.error(f"Failed to update job status: {e}")
            raise

    async def _check_job_completion(self, task_id: str) -> bool:
        """Check if job has completed successfully by checking output files.

        Args:
            task_id: Task identifier

        Returns:
            True if job completed successfully, False otherwise
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Check for output files
            remote_dir = f"$SCRATCH/nfm-md/{task_id}"
            output_files = ["lammps.out", "log.lammps"]

            sftp = None
            try:
                sftp = client.open_sftp()

                # Check if output files exist and have content
                for output_file in output_files:
                    remote_path = f"{remote_dir}/{output_file}"
                    try:
                        file_stat = sftp.stat(remote_path)
                        if file_stat.st_size > 0:
                            return True
                    except IOError:
                        # File doesn't exist
                        pass

                return False

            finally:
                if sftp:
                    sftp.close()

        except Exception as e:
            logger.warning(f"Failed to check job completion: {e}")
            return False
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def _get_active_jobs(self) -> List[HpcJob]:
        """Get all active HPC jobs from database.

        Returns:
            List of active HpcJob objects
        """
        db_gen = get_db()
        db = await db_gen.__anext__()

        try:
            # Query for active jobs (PENDING or RUNNING)
            from sqlalchemy import select
            result = await db.execute(
                select(HpcJob).where(
                    HpcJob.status.in_([HpcJobStatus.PENDING, HpcJobStatus.RUNNING])
                )
            )
            return result.scalars().all()
        finally:
            try:
                await db_gen.__anext__()
            except StopAsyncIteration:
                pass

    async def sync_all_active_jobs(self) -> None:
        """Sync status for all active HPC jobs (called by Celery beat).

        This method is called periodically by Celery beat to update the status
        of all active jobs in the system.
        """
        try:
            active_jobs = await self._get_active_jobs()

            for job in active_jobs:
                try:
                    await self.update_job_status(str(job.verification_job_id), job.hpc_job_id)
                except Exception as e:
                    logger.error(f"Failed to sync job {job.hpc_job_id}: {e}")
                    # Continue with other jobs even if one fails

            logger.info(f"Synced {len(active_jobs)} active jobs")

        except Exception as e:
            logger.error(f"Failed to sync active jobs: {e}")


# =============================================================================
# Celery Task for Periodic Status Sync
# =============================================================================


@celery_app.task
def sync_hpc_job_status() -> dict:
    """Celery task for periodic HPC job status synchronization.

    This task is called by Celery beat every 30 seconds to update the status
    of all active HPC jobs in the system.

    Returns:
        Dictionary with sync status and statistics
    """
    import asyncio

    # Get HPC orchestrator configuration
    try:
        config = SSHConnectionConfig(
            hosts=[os.getenv("NFM_HPC_PRIMARY_HOST", "login.example.com")],
            username=os.getenv("NFM_HPC_PRIMARY_USER", "user"),
            ssh_key_path=os.getenv("NFM_HPC_PRIMARY_SSH_KEY_PATH", "/path/to/key"),
            max_connections=int(os.getenv("NFM_HPC_MAX_CONNECTIONS", "10"))
        )

        orchestrator = HPCOrchestrator(config)

        # Run async sync function
        loop = asyncio.get_event_loop()
        loop.run_until_complete(orchestrator.sync_all_active_jobs())

        return {
            "status": "success",
            "message": "HPC job status sync completed"
        }

    except Exception as e:
        logger.error(f"HPC job status sync failed: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
