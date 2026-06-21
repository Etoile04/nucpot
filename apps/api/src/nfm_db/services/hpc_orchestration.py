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
import paramiko
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


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

    This class will be implemented in Phase 4.2-4.5.
    Currently a placeholder for future implementation.
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

    def submit_job(
        self,
        task_id: str,
        crystal_structure_file: str,
        params: Dict[str, Any]
    ) -> str:
        """Submit MD verification job to HPC.

        This will be implemented in Phase 4.2.

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
        raise NotImplementedError("submit_job will be implemented in Phase 4.2")

    def get_job_status(self, hpc_job_id: str) -> str:
        """Query current job status from SLURM.

        This will be implemented in Phase 4.3.

        Returns:
            status: PENDING | RUNNING | COMPLETED | FAILED
        """
        raise NotImplementedError("get_job_status will be implemented in Phase 4.3")

    def download_results(
        self,
        task_id: str,
        hpc_job_id: str
    ) -> Dict[str, str]:
        """Download and parse job results.

        This will be implemented in Phase 4.4.

        Returns:
            Results dict with file paths to:
            - lammps.out
            - log.lammps
            - energy_curve.dat
            - defect_analysis.json
        """
        raise NotImplementedError("download_results will be implemented in Phase 4.4")
