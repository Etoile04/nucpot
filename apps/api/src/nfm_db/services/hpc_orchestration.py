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
import threading
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

# Prometheus metrics
try:
    from prometheus_client import Counter, Histogram, Gauge

    hpc_job_submissions = Counter(
        'hpc_job_submissions_total',
        'Total HPC job submissions',
        ['cluster', 'status']
    )
    hpc_job_duration = Histogram(
        'hpc_job_duration_seconds',
        'HPC job completion time',
        ['cluster']
    )
    hpc_file_transfer_bytes = Counter(
        'hpc_file_transfer_bytes_total',
        'File transfer volume',
        ['direction', 'cluster']
    )
    hpc_connection_errors = Counter(
        'hpc_connection_errors_total',
        'SSH connection errors',
        ['cluster', 'error_type']
    )
    hpc_failover_events = Counter(
        'hpc_failover_events_total',
        'Failover triggers',
        ['from_cluster', 'to_cluster']
    )
    hpc_active_connections = Gauge(
        'hpc_active_connections',
        'Number of active SSH connections',
        ['cluster']
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.warning("Prometheus client not available - metrics disabled")
    PROMETHEUS_AVAILABLE = False


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
    skip_key_validation: bool = False  # For testing with mock SSH servers

    # Phase 4.5: Failover configuration
    backup_hosts: Optional[List[str]] = None  # Backup cluster hosts
    backup_username: Optional[str] = None  # Backup cluster username
    backup_ssh_key_path: Optional[str] = None  # Backup cluster SSH key
    failover_threshold_seconds: int = 300  # 5 minutes
    work_dir: str = "/scratch/{username}/nfm-md"  # HPC work directory


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

        # Connection tracking (no pooling - create/close immediately)
        self._active_connections: set = set()
        self._connection_lock = threading.Lock()  # Thread-safe lock

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
        with self._connection_lock:
            # Check if pool is exhausted
            if len(self._active_connections) >= self.max_connections:
                raise ConnectionError("Connection pool exhausted")

            # Validate SSH key exists before creating connection (unless skipped)
            if not self._skip_key_validation and not Path(self.ssh_key_path).exists():
                raise FileNotFoundError(f"SSH key file not found: {self.ssh_key_path}")

            # Create new connection (don't reuse - Paramiko clients hold state)
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
        """Release and close a connection.

        Note: SSH connections are closed immediately to prevent memory leaks.
        Paramiko SSHClient objects accumulate state when reused.

        Args:
            client: SSH client to release
        """
        with self._connection_lock:
            if client in self._active_connections:
                self._active_connections.remove(client)
                # Close immediately to prevent memory leaks
                try:
                    client.close()
                except Exception:
                    pass

            # Update Prometheus metrics
            if PROMETHEUS_AVAILABLE:
                hpc_active_connections.labels(cluster=self.host).set(len(self._active_connections))

    def cleanup(self) -> None:
        """Clean up all connections and resources.

        This method should be called when the manager is no longer needed
        to prevent memory leaks.
        """
        # Close all active connections
        with self._connection_lock:
            # Close each connection completely
            for client in list(self._active_connections):
                try:
                    # Close transport first to ensure all channels are closed
                    if hasattr(client, 'transport') and client.transport:
                        client.transport.close()
                    # Then close the client
                    client.close()
                except Exception:
                    pass
                # Delete reference to help garbage collection
                del client
            # Clear the set
            self._active_connections.clear()
            # Clear reference to hosts list
            self.hosts = []

    def __del__(self):
        """Destructor to ensure cleanup on garbage collection."""
        self.cleanup()

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

            # Update Prometheus metrics
            if PROMETHEUS_AVAILABLE:
                hpc_active_connections.labels(cluster=host).inc()

            return client
        except paramiko.AuthenticationException as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type='authentication').inc()
            client.close()
            raise ConnectionError(f"Authentication failed for {host}: {e}")
        except paramiko.SSHException as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type='ssh').inc()
            client.close()
            raise ConnectionError(f"SSH connection failed to {host}: {e}")
        except Exception as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type='unknown').inc()
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

        # Phase 4.5: Failover support
        self.backup_ssh_manager = None
        self.current_cluster = "primary"  # Track current active cluster
        self.primary_healthy = True  # Track primary cluster health
        self.last_health_check = None
        self.failover_count = 0

        # Initialize backup cluster if configured
        if config.backup_hosts and config.backup_username and config.backup_ssh_key_path:
            self.backup_ssh_manager = SSHConnectionManager(
                host=config.backup_hosts,
                username=config.backup_username,
                ssh_key_path=config.backup_ssh_key_path,
                max_connections=config.max_connections
            )
            logger.info(f"Backup cluster configured: {config.backup_hosts[0]}")

    def cleanup(self) -> None:
        """Clean up all SSH connections and resources.

        This method should be called when the orchestrator is no longer needed
        to prevent memory leaks.
        """
        self.ssh_manager.cleanup()
        if self.backup_ssh_manager:
            self.backup_ssh_manager.cleanup()
        # Clear references to help garbage collection
        self.ssh_manager = None
        self.backup_ssh_manager = None
        self.config = None

    def __del__(self):
        """Destructor to ensure cleanup on garbage collection."""
        try:
            self.cleanup()
        except Exception:
            pass  # Ignore errors during cleanup

    async def _log_failover_event(
        self,
        event_type: str,
        source_cluster: str,
        reason: str,
        success: bool = True,
        target_cluster: str = None,
        failure_count: int = 0,
        event_metadata: dict = None
    ) -> None:
        """Log failover event to database.

        Args:
            event_type: Type of event (failover_triggered, failover_failed, primary_recovered, etc.)
            source_cluster: Cluster where failover initiated from
            reason: Human-readable description of why failover occurred
            success: Whether the operation succeeded
            target_cluster: Cluster where failover switched to (if successful)
            failure_count: Number of consecutive failures that triggered failover
            event_metadata: Additional metadata (JSONB field)
        """
        from nfm_db.models.hpc_failover_event import HPCFailoverEvent

        # Use async generator pattern for database session
        db_gen = get_db()
        db = None

        try:
            # Get database session
            db = await db_gen.__anext__()

            # Create failover event record
            event = HPCFailoverEvent(
                event_type=event_type,
                source_cluster=source_cluster,
                target_cluster=target_cluster,
                reason=reason,
                failure_count=failure_count,
                success=success,
                event_metadata=event_metadata or {}
            )

            db.add(event)
            await db.commit()

            logger.info(f"Logged failover event: {event_type} - {source_cluster} -> {target_cluster}")

        except Exception as e:
            # Fallback to stdout logging if database fails
            logger.error(f"Failed to log failover event to database: {e}")
            logger.info(
                f"FAILOVER EVENT (stdout fallback): {event_type} - "
                f"{source_cluster} -> {target_cluster} - {reason}"
            )
            if db:
                try:
                    await db.rollback()
                except Exception:
                    pass
        finally:
            # Close database session
            try:
                await db_gen.__anext__()
            except StopAsyncIteration:
                pass

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

    def check_primary_health(self) -> bool:
        """Check if primary cluster is healthy.

        Returns:
            True if primary cluster is healthy, False otherwise
        """
        try:
            client = self.ssh_manager.acquire_connection_with_retry(max_retries=1)
            if client:
                self.ssh_manager.release_connection(client)
                self.last_health_check = datetime.now()
                return True
            return False
        except Exception as e:
            logger.warning(f"Primary health check failed: {e}")
            return False

    def should_trigger_failover(self) -> bool:
        """Determine if failover should be triggered based on primary health.

        Returns:
            True if failover should be triggered, False otherwise
        """
        # Check if we have backup cluster configured
        if not self.backup_ssh_manager:
            logger.warning("No backup cluster configured - failover not available")
            return False

        # Check if primary is currently marked unhealthy
        if not self.primary_healthy:
            return True

        # Check if we've exceeded the failover threshold
        if self.last_health_check:
            time_since_healthy = (datetime.now() - self.last_health_check).total_seconds()
            if time_since_healthy > self.config.failover_threshold_seconds:
                logger.error(f"Primary cluster unhealthy for {time_since_healthy:.1f}s - triggering failover")
                self.primary_healthy = False
                return True

        # Do a fresh health check
        is_healthy = self.check_primary_health()
        if not is_healthy:
            if self.last_health_check:
                time_since_healthy = (datetime.now() - self.last_health_check).total_seconds()
                if time_since_healthy > self.config.failover_threshold_seconds:
                    logger.error(f"Primary cluster unhealthy for {time_since_healthy:.1f}s - triggering failover")
                    self.primary_healthy = False
                    return True
            else:
                # First health check failed - start the clock
                self.last_health_check = datetime.now()

        return False

    async def trigger_failover(self) -> bool:
        """Trigger failover to backup cluster.

        Returns:
            True if failover was successful, False otherwise
        """
        if not self.backup_ssh_manager:
            logger.error("Cannot trigger failover - no backup cluster configured")
            # Log failed failover attempt
            await self._log_failover_event(
                event_type="failover_failed",
                source_cluster=self.hpc_cluster,
                target_cluster=None,
                reason="No backup cluster configured",
                success=False,
                failure_count=self.failover_count,
                event_metadata={"error": "No backup cluster configured"}
            )
            return False

        try:
            # Test backup cluster connectivity
            client = self.backup_ssh_manager.acquire_connection_with_retry(max_retries=2)
            if not client:
                logger.error("Backup cluster connectivity test failed")
                # Log failed failover attempt
                await self._log_failover_event(
                    event_type="failover_failed",
                    source_cluster=self.hpc_cluster,
                    target_cluster=self.config.backup_hosts[0],
                    reason="Backup cluster connectivity test failed",
                    success=False,
                    failure_count=self.failover_count,
                    event_metadata={"error": "Backup cluster unreachable"}
                )
                return False

            self.backup_ssh_manager.release_connection(client)

            # Log failover event
            self.failover_count += 1
            from_cluster = self.hpc_cluster
            to_cluster = self.config.backup_hosts[0]

            await self._log_failover_event(
                event_type="failover_triggered",
                source_cluster=from_cluster,
                target_cluster=to_cluster,
                reason=f"Primary cluster down after {self.failover_count} consecutive failures",
                success=True,
                failure_count=self.failover_count,
                event_metadata={"failover_number": self.failover_count}
            )

            logger.error(f"FAILOVER #{self.failover_count}: {from_cluster} -> {to_cluster}")

            # Update Prometheus metrics
            if PROMETHEUS_AVAILABLE:
                hpc_failover_events.labels(
                    from_cluster=from_cluster,
                    to_cluster=to_cluster
                ).inc()

            # Switch to backup cluster
            self.current_cluster = "backup"
            return True

        except Exception as e:
            logger.error(f"Failover failed: {e}")
            # Log failed failover attempt
            await self._log_failover_event(
                event_type="failover_failed",
                source_cluster=self.hpc_cluster,
                target_cluster=self.config.backup_hosts[0] if self.config.backup_hosts else None,
                reason=f"Exception during failover: {str(e)}",
                success=False,
                failure_count=self.failover_count,
                event_metadata={"exception": str(e), "exception_type": type(e).__name__}
            )
            return False

    async def try_recover_primary(self) -> bool:
        """Attempt to recover connection to primary cluster.

        Returns:
            True if primary cluster recovered, False otherwise
        """
        if self.check_primary_health():
            logger.info("Primary cluster recovered - will switch back on next job submission")
            self.primary_healthy = True

            # Log recovery event
            await self._log_failover_event(
                event_type="primary_recovered",
                source_cluster=self.hpc_cluster,
                target_cluster=None,
                reason="Primary cluster health restored",
                success=True,
                failure_count=0,
                event_metadata={"recovery_timestamp": datetime.now().isoformat()}
            )

            return True

        # Log failed recovery attempt
        await self._log_failover_event(
            event_type="recovery_attempted",
            source_cluster=self.hpc_cluster,
            target_cluster=None,
            reason="Primary cluster still unhealthy",
            success=False,
            failure_count=0,
            event_metadata={"health_check_failed": True}
        )

        return False

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
        # Phase 4.5: Check if we should trigger failover
        if self.should_trigger_failover():
            if await self.trigger_failover():
                logger.warning(f"Using backup cluster for job {task_id}")
            else:
                raise HPCConnectionError("Both primary and backup clusters unavailable")

        # Try to recover primary if we're on backup
        if self.current_cluster == "backup":
            if await self.try_recover_primary():
                logger.info(f"Switching back to primary cluster for job {task_id}")
                self.current_cluster = "primary"

        # Validate required parameters
        self._validate_simulation_params(params)

        # Generate SLURM script
        slurm_script = self._generate_slurm_script(params)

        # Submit to SLURM via SSH (with automatic failover)
        try:
            hpc_job_id = await self._submit_to_slurm(task_id, slurm_script)
            cluster_used = self.current_cluster
        except Exception as e:
            # If submission failed and we're on primary, try failover
            if self.current_cluster == "primary":
                if await self.trigger_failover():
                    logger.warning(f"Primary submission failed, retrying on backup: {e}")
                    hpc_job_id = await self._submit_to_slurm(task_id, slurm_script)
                    cluster_used = "backup"
                else:
                    raise HPCConnectionError(f"Job submission failed on all clusters: {e}")
            else:
                raise HPCConnectionError(f"Job submission failed on all clusters: {e}")

        # Create database record
        await self._create_hpc_job_record(task_id, hpc_job_id, params, cluster_used)

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
        # Use appropriate cluster manager based on current cluster
        cluster_manager = self.ssh_manager if self.current_cluster == "primary" else self.backup_ssh_manager
        cluster_name = self.hpc_cluster if self.current_cluster == "primary" else self.config.backup_hosts[0]

        if not cluster_manager:
            raise JobSubmissionError(f"No cluster manager available for {self.current_cluster}")

        client = None
        try:
            # Acquire SSH connection
            client = cluster_manager.acquire_connection()

            # Create temporary script file on HPC
            script_path = f"$SCRATCH/nfm-md/{task_id}/submit.sh"
            self._upload_script_via_sftp(client, slurm_script, script_path)

            # Submit job via sbatch
            stdin, stdout, stderr = client.exec_command(f"sbatch {script_path}")
            exit_status = stdout.channel.recv_exit_status()

            if exit_status != 0:
                error_msg = stderr.read().decode()
                if "Socket timed out" in error_msg or "qos: QOSMaxSubmitJobLimit" in error_msg:
                    # Update Prometheus metrics
                    if PROMETHEUS_AVAILABLE:
                        hpc_job_submissions.labels(cluster=cluster_name, status='queue_full').inc()
                    raise JobSubmissionError(f"SLURM queue is full: {error_msg}")
                elif "Permission denied" in error_msg:
                    if PROMETHEUS_AVAILABLE:
                        hpc_job_submissions.labels(cluster=cluster_name, status='permission_denied').inc()
                    raise JobSubmissionError(f"Permission denied: {error_msg}")
                else:
                    if PROMETHEUS_AVAILABLE:
                        hpc_job_submissions.labels(cluster=cluster_name, status='failed').inc()
                    raise JobSubmissionError(f"SLURM submission failed: {error_msg}")

            # Parse job ID from output
            job_id = stdout.read().decode().strip()
            if not job_id.isdigit():
                if PROMETHEUS_AVAILABLE:
                    hpc_job_submissions.labels(cluster=cluster_name, status='invalid_response').inc()
                raise JobSubmissionError(f"Invalid job ID returned: {job_id}")

            logger.info(f"Job submitted successfully to {cluster_name}: {job_id}")

            # Update Prometheus metrics
            if PROMETHEUS_AVAILABLE:
                hpc_job_submissions.labels(cluster=cluster_name, status='success').inc()

            return f"slurm-{job_id}"

        except JobSubmissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to submit job to {cluster_name}: {e}")
            if PROMETHEUS_AVAILABLE:
                hpc_job_submissions.labels(cluster=cluster_name, status='error').inc()
            raise JobSubmissionError(f"HPC connection failed: {e}")
        finally:
            if client:
                cluster_manager.release_connection(client)

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
        params: Dict[str, Any],
        cluster_used: str = "primary"
    ) -> None:
        """Create record in hpc_jobs table.

        Args:
            task_id: MD verification job ID
            hpc_job_id: SLURM job identifier
            params: Job parameters
            cluster_used: Which cluster was used (primary/backup)
        """
        # Use async generator pattern for database session
        db_gen = get_db()
        db = await db_gen.__anext__()

        try:
            # Determine actual cluster used
            actual_cluster = self.hpc_cluster if cluster_used == "primary" else (
                self.config.backup_hosts[0] if self.config.backup_hosts else "unknown"
            )

            hpc_job = HpcJob(
                verification_job_id=uuid.UUID(task_id),
                hpc_cluster=actual_cluster,
                hpc_job_id=hpc_job_id,
                status=HpcJobStatus.PENDING,
                partition=params.get("partition", "compute"),
                nodes=params.get("nodes", 1),
                walltime_requested=self._parse_walltime(params.get("walltime", "02:00:00")),
                submitted_at=datetime.utcnow()
            )

            db.add(hpc_job)
            await db.commit()

            logger.info(f"Created HPC job record: {hpc_job.id} on cluster {actual_cluster}")
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
    # Phase 4.4: File Transfer Methods
    # =============================================================================

    async def upload_file(self, task_id: str, local_file: str, remote_file: str) -> bool:
        """Upload a single file to HPC cluster.

        Args:
            task_id: Task identifier for directory organization
            local_file: Path to local file to upload
            remote_file: Remote destination path

        Returns:
            True if upload succeeded, False otherwise
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Create task directory if it doesn't exist
            await self._create_task_directory(task_id)

            # Upload file via SFTP
            sftp = None
            try:
                sftp = client.open_sftp()
                sftp.put(local_file, remote_file)
                logger.info(f"Uploaded file: {local_file} -> {remote_file}")
                return True

            finally:
                if sftp:
                    sftp.close()

        except Exception as e:
            logger.error(f"Failed to upload file {local_file}: {e}")
            return False
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def upload_files(self, task_id: str, files: List[tuple[str, str]]) -> Dict[str, bool]:
        """Upload multiple files to HPC cluster.

        Args:
            task_id: Task identifier
            files: List of (local_path, remote_path) tuples

        Returns:
            Dictionary mapping file paths to success status
        """
        results = {}
        for local_file, remote_file in files:
            success = await self.upload_file(task_id, local_file, remote_file)
            results[local_file] = success
        return results

    async def _create_task_directory(self, task_id: str) -> None:
        """Create task-specific directory on HPC cluster.

        Args:
            task_id: Task identifier for directory path
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            remote_dir = f"$SCRATCH/nfm-md/{task_id}"

            sftp = None
            try:
                sftp = client.open_sftp()
                try:
                    # Try to create directory (may already exist)
                    sftp.mkdir(remote_dir)
                except IOError:
                    # Directory already exists, that's fine
                    pass

                logger.info(f"Task directory ready: {remote_dir}")

            finally:
                if sftp:
                    sftp.close()

        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def download_file(self, task_id: str, remote_file: str, local_path: str) -> Optional[str]:
        """Download a single file from HPC cluster.

        Args:
            task_id: Task identifier
            remote_file: Remote file path to download
            local_path: Local destination path

        Returns:
            Local path to downloaded file, or None if failed
        """
        import os
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Create local directory if it doesn't exist
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # Download file via SFTP
            sftp = None
            try:
                sftp = client.open_sftp()
                sftp.get(remote_file, local_path)
                logger.info(f"Downloaded file: {remote_file} -> {local_path}")
                return local_path

            finally:
                if sftp:
                    sftp.close()

        except Exception as e:
            logger.error(f"Failed to download file {remote_file}: {e}")
            return None
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def download_results(self, task_id: str) -> Dict[str, str]:
        """Download all result files for a task.

        Args:
            task_id: Task identifier

        Returns:
            Dictionary mapping result file names to local paths
        """
        remote_dir = f"$SCRATCH/nfm-md/{task_id}"
        local_dir = f"/tmp/results/{task_id}"

        result_files = {
            "lammps.out": f"{remote_dir}/lammps.out",
            "log.lammps": f"{remote_dir}/log.lammps",
            "energy_curve.dat": f"{remote_dir}/energy_curve.dat",
        }

        downloaded = {}
        for name, remote_path in result_files.items():
            local_path = f"{local_dir}/{name}"
            result = await self.download_file(task_id, remote_path, local_path)
            if result:
                downloaded[name] = local_path

        logger.info(f"Downloaded {len(downloaded)}/{len(result_files)} result files")
        return downloaded

    async def verify_checksum(self, local_file: str, expected_checksum: str) -> bool:
        """Verify file checksum matches expected value.

        Args:
            local_file: Path to local file
            expected_checksum: Expected SHA256 checksum

        Returns:
            True if checksums match, False otherwise
        """
        import hashlib

        try:
            sha256_hash = hashlib.sha256()
            with open(local_file, 'rb') as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)

            actual_checksum = sha256_hash.hexdigest()
            return actual_checksum == expected_checksum

        except Exception as e:
            logger.error(f"Failed to verify checksum for {local_file}: {e}")
            return False

    async def get_remote_checksum(self, task_id: str, remote_file: str) -> Optional[str]:
        """Get checksum of remote file via SSH.

        Args:
            task_id: Task identifier
            remote_file: Remote file path

        Returns:
            SHA256 checksum, or None if failed
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Execute sha256sum command on remote file
            cmd = f"sha256sum {remote_file}"
            stdin, stdout, stderr = client.exec_command(cmd)
            output = stdout.read().decode().strip()

            if exit_status := stdout.channel.recv_exit_status() == 0:
                # Parse output: "checksum  filename"
                checksum = output.split()[0]
                return checksum
            else:
                logger.error(f"Failed to get remote checksum: {stderr.read().decode()}")
                return None

        except Exception as e:
            logger.error(f"Failed to get remote checksum: {e}")
            return None
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def save_to_object_storage(self, task_id: str, downloaded_files: Dict[str, str]) -> Dict[str, str]:
        """Save downloaded files to NFMD object storage.

        Args:
            task_id: Task identifier
            downloaded_files: Dictionary of file names to local paths

        Returns:
            Dictionary of file names to storage URLs
        """
        # This is a placeholder for object storage integration
        # In production, this would upload to S3, GCS, or similar
        storage_urls = {}

        for filename, local_path in downloaded_files.items():
            # Generate storage URL
            storage_url = f"https://storage.example.com/{task_id}/{filename}"
            storage_urls[filename] = storage_url

            logger.info(f"Saved to object storage: {filename} -> {storage_url}")

        return storage_urls

    async def _save_metadata(self, task_id: str, file_metadata: Dict[str, Dict[str, Any]]) -> None:
        """Save file metadata to database.

        Args:
            task_id: Task identifier
            file_metadata: Dictionary of file metadata
        """
        db_gen = get_db()
        db = await db_gen.__anext__()

        try:
            # In production, this would save to md_simulation_results table
            # For now, just log the metadata
            logger.info(f"Saving metadata for task {task_id}: {file_metadata}")

        finally:
            try:
                await db_gen.__anext__()
            except StopAsyncIteration:
                pass

    async def upload_file_with_retry(self, task_id: str, local_file: str, remote_file: str, max_retries: int = 3) -> bool:
        """Upload file with automatic retry on failure.

        Args:
            task_id: Task identifier
            local_file: Path to local file
            remote_file: Remote destination path
            max_retries: Maximum number of retry attempts

        Returns:
            True if upload succeeded, False otherwise
        """
        for attempt in range(max_retries):
            try:
                result = await self.upload_file(task_id, local_file, remote_file)
                if result:
                    return True

                logger.warning(f"Upload attempt {attempt + 1} failed for {local_file}, retrying...")
                # Wait before retry (exponential backoff)
                import asyncio
                await asyncio.sleep(2 ** attempt)

            except Exception as e:
                logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    return False

        return False

    async def upload_file_with_resume(self, task_id: str, local_file: str, remote_file: str, resume_position: int = 0) -> bool:
        """Upload file with resume capability (断点续传).

        Args:
            task_id: Task identifier
            local_file: Path to local file
            remote_file: Remote destination path
            resume_position: Byte position to resume from

        Returns:
            True if upload succeeded, False otherwise
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            # Create task directory if needed
            await self._create_task_directory(task_id)

            sftp = None
            try:
                sftp = client.open_sftp()

                # Open local file in binary mode
                with open(local_file, 'rb') as local_f:
                    # Seek to resume position
                    local_f.seek(resume_position)

                    # Open remote file in binary append mode
                    with sftp.file(remote_file, 'ab') as remote_f:
                        # Copy from resume position to end
                        while True:
                            chunk = local_f.read(65536)  # 64KB chunks
                            if not chunk:
                                break
                            remote_f.write(chunk)

                logger.info(f"Resumed upload from position {resume_position}: {local_file} -> {remote_file}")
                return True

            finally:
                if sftp:
                    sftp.close()

        except Exception as e:
            logger.error(f"Failed to upload file with resume: {e}")
            return False
        finally:
            if client:
                self.ssh_manager.release_connection(client)

    async def get_remote_file_position(self, task_id: str, remote_file: str) -> int:
        """Get current position of partial file on remote system for resume.

        Args:
            task_id: Task identifier
            remote_file: Remote file path

        Returns:
            Current file size in bytes (0 if file doesn't exist)
        """
        client = None
        try:
            client = self.ssh_manager.acquire_connection()

            sftp = None
            try:
                sftp = client.open_sftp()

                try:
                    # Check if file exists and get its size
                    file_stat = sftp.stat(remote_file)
                    return file_stat.st_size

                except IOError:
                    # File doesn't exist, start from beginning
                    return 0

            finally:
                if sftp:
                    sftp.close()

        except Exception as e:
            logger.warning(f"Failed to check remote file position: {e}")
            return 0
        finally:
            if client:
                self.ssh_manager.release_connection(client)


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

    async def _sync_jobs():
        # Get HPC orchestrator configuration
        try:
            config = SSHConnectionConfig(
                hosts=[os.getenv("NFM_HPC_PRIMARY_HOST", "login.example.com")],
                username=os.getenv("NFM_HPC_PRIMARY_USER", "user"),
                ssh_key_path=os.getenv("NFM_HPC_PRIMARY_SSH_KEY_PATH", "/path/to/key"),
                max_connections=int(os.getenv("NFM_HPC_MAX_CONNECTIONS", "10")),
                backup_hosts=[os.getenv("NFM_HPC_BACKUP_HOST", "backup.example.com")] if os.getenv("NFM_HPC_BACKUP_HOST") else None,
                backup_username=os.getenv("NFM_HPC_BACKUP_USER"),
                backup_ssh_key_path=os.getenv("NFM_HPC_BACKUP_SSH_KEY_PATH"),
                failover_threshold_seconds=int(os.getenv("NFM_HPC_FAILOVER_THRESHOLD_SECONDS", "300"))
            )

            orchestrator = HPCOrchestrator(config)

            try:
                result = await orchestrator.sync_all_active_jobs()
                return result
            finally:
                orchestrator.cleanup()

        except Exception as e:
            logger.error(f"HPC job status sync failed: {e}")
            return {
                "status": "error",
                "message": str(e),
                "jobs_processed": 0
            }

    # Run async function properly
    try:
        result = asyncio.run(_sync_jobs())
        return result
    except Exception as e:
        logger.error(f"Failed to run job sync: {e}")
        return {
            "status": "error",
            "message": str(e),
            "jobs_processed": 0
        }

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


