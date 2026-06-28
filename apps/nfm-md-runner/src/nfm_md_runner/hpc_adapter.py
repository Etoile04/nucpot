"""
HPC Adapter Module for LAMMPS Automation

This module provides SSH-based interaction with HPC clusters (SLURM workload manager).
Implements hybrid connection management:
- Persistent connections for job monitoring (squeue/sacct polling)
- On-demand connections for file transfers (SCP/SFTP)

Security: All credentials loaded from environment variables via config module
"""

import logging
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any

import paramiko
from paramiko import SSHClient, SFTPClient, AutoAddPolicy, RejectPolicy

from .config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Input Validation Helpers
# =============================================================================

_SHELL_DANGEROUS_PATTERN = re.compile(r"[;&\n\0`$|]")

_REMOTE_PATH_SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9/.:_-]+$')


def validate_shell_safe(value: str, field_name: str) -> str:
    """Validate that a value is safe for shell script interpolation.

    Rejects values containing shell metacharacters that could enable
    command injection: semicolons, newlines, null bytes, backticks,
    dollar signs (for $() and $var), and pipes.

    Args:
        value: The string to validate.
        field_name: Human-readable field name for error messages.

    Returns:
        The validated string (unchanged).

    Raises:
        ValueError: If the value contains shell-unsafe characters.
    """
    if _SHELL_DANGEROUS_PATTERN.search(value):
        raise ValueError(
            f"Unsafe shell character in {field_name}: "
            f"only alphanumeric, dashes, underscores, dots, slashes, "
            f"and percent signs are permitted"
        )
    return value


def validate_remote_path(path: str) -> str:
    """Validate that a remote file path contains only safe characters.

    Prevents command injection in SSH commands by allowing only
    alphanumeric characters, forward slashes, dots, dashes, underscores,
    and colons (used in port specifications).

    Args:
        path: The remote file path to validate.

    Returns:
        The validated path string (unchanged).

    Raises:
        ValueError: If the path contains unsafe characters.
    """
    if not _REMOTE_PATH_SAFE_PATTERN.match(path):
        raise ValueError(
            f"Unsafe remote path: only alphanumeric, slashes, dots, "
            f"dashes, underscores, and colons are permitted. "
            f"Received: {path}"
        )
    return path


def validate_positive_int(value: Any, field_name: str) -> int:
    """Validate that a value is a positive integer.

    Prevents shell injection by ensuring the value is a strict positive
    integer (``int`` type, ``>= 1``) before it is interpolated into
    SLURM batch script directives such as ``--nodes`` and
    ``--ntasks-per-node``.

    Args:
        value: The value to validate.
        field_name: Human-readable field name for error messages.

    Returns:
        The validated integer.

    Raises:
        ValueError: If the value is not a positive integer.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(
            f"{field_name} must be a positive integer, "
            f"got {type(value).__name__}: {value!r}"
        )
    if value < 1:
        raise ValueError(
            f"{field_name} must be a positive integer (>= 1), got: {value}"
        )
    return value


def validate_job_id(job_id: str | int) -> str:
    """Validate that a SLURM job ID is a non-empty numeric string.

    Prevents command injection by ensuring the job ID contains only
    digits before it is interpolated into shell commands (squeue,
    sacct, scancel, scp).

    Args:
        job_id: The SLURM job ID to validate.

    Returns:
        The validated job ID as a string.

    Raises:
        ValueError: If the job ID is empty or contains non-numeric characters.
    """
    job_id_str = str(job_id)
    if not re.match(r'^\d+$', job_id_str):
        raise ValueError(
            f"Invalid SLURM job ID (must be numeric): {job_id!r}"
        )
    return job_id_str


class JobStatus(Enum):
    """SLURM job status enumeration"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class ClusterType(Enum):
    """HPC cluster type enumeration"""
    GUANGZHOU = "guangzhou"
    TIANJIN = "tianjin"


@dataclass
class HPCJob:
    """HPC job data structure"""
    job_id: str
    cluster: ClusterType
    status: JobStatus
    partition: Optional[str] = None
    nodes: int = 1
    cores: int = 1
    walltime_requested: Optional[int] = None  # seconds
    walltime_used: Optional[int] = None
    submit_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class ClusterConfig:
    """HPC cluster configuration"""
    name: ClusterType
    host: str
    port: int = 22
    username: Optional[str] = None
    ssh_key_path: Optional[Path] = None
    work_dir: Optional[Path] = None
    is_primary: bool = True


class SSHConnectionManager:
    """
    Manages SSH connections with connection pooling and auto-reconnect
    
    Hybrid approach:
    - Maintains persistent connections for frequent operations (job monitoring)
    - Creates on-demand connections for occasional operations (file transfer)
    """
    
    def __init__(
        self,
        max_connections: int = 3,
        skip_key_validation: bool = False,
        known_hosts_path: Optional[str] = None,
    ):
        """
        Initialize SSH connection manager

        Args:
            max_connections: Maximum persistent connections per cluster
            skip_key_validation: Skip SSH host key validation (test/local only)
            known_hosts_path: Path to known_hosts file for host key verification
        """
        self.max_connections = max_connections
        self._skip_key_validation = skip_key_validation
        self._known_hosts_path = known_hosts_path
        self._lock = threading.Lock()
        self._connections: Dict[ClusterType, List[SSHClient]] = {}
        self._connection_last_used: Dict[ClusterType, List[datetime]] = {}
        
    def get_connection(self, cluster: ClusterConfig) -> SSHClient:
        """
        Get or create SSH connection

        Thread-safe: acquires _lock for all dict access.

        Args:
            cluster: Cluster configuration

        Returns:
            Active SSH client connection
        """
        with self._lock:
            # Check if we have an existing persistent connection
            if cluster.name in self._connections and self._connections[cluster.name]:
                conn_idx = self._get_least_recently_used(cluster.name)
                conn = self._connections[cluster.name][conn_idx]

                # Test connection is still alive
                if self._test_connection(conn):
                    self._connection_last_used[cluster.name][conn_idx] = datetime.now()
                    logger.debug(f"Reusing existing SSH connection to {cluster.host}")
                    return conn
                else:
                    # Remove stale connection
                    logger.warning(f"Stale SSH connection to {cluster.host}, recreating")
                    self._remove_connection(cluster.name, conn_idx)

            # Create new connection (called under lock to protect pool insertion)
            return self._create_connection(cluster)
    
    def _get_least_recently_used(self, cluster: ClusterType) -> int:
        """Get index of least recently used connection"""
        if cluster not in self._connection_last_used or not self._connection_last_used[cluster]:
            return 0
        return min(range(len(self._connection_last_used[cluster])), 
                  key=lambda i: self._connection_last_used[cluster][i])
    
    def _test_connection(self, conn: SSHClient) -> bool:
        """Test if SSH connection is still alive"""
        try:
            transport = conn.get_transport()
            if not transport or not transport.is_active():
                return False
            transport.send_ignore()
            return True
        except Exception as e:
            logger.debug(f"Connection test failed: {e}")
            return False
    
    def _create_connection(self, cluster: ClusterConfig) -> SSHClient:
        """
        Create new SSH connection
        
        Args:
            cluster: Cluster configuration
            
        Returns:
            New SSH client connection
        """
        client = SSHClient()

        # Host key policy: RejectPolicy prevents MITM by default
        if self._skip_key_validation:
            client.set_missing_host_key_policy(AutoAddPolicy())
        else:
            if self._known_hosts_path:
                client.load_host_keys(self._known_hosts_path)
            client.set_missing_host_key_policy(RejectPolicy())
        
        try:
            # Load SSH key
            key_path = cluster.ssh_key_path or settings.hpc_ssh_key_path
            if not key_path or not key_path.exists():
                raise ValueError(f"SSH key not found: {key_path}")
            
            # Verify key permissions (must be 600)
            if oct(key_path.stat().st_mode & 0o777) != "0o600":
                logger.warning(f"SSH key permissions insecure: {oct(key_path.stat().st_mode & 0o777)}")
                logger.warning(f"Run: chmod 600 {key_path}")
            
            # Connect with key authentication
            username = cluster.username or settings.hpc_user
            client.connect(
                hostname=cluster.host,
                port=cluster.port,
                username=username,
                key_filename=str(key_path),
                timeout=30,
                banner_timeout=30
            )
            
            # Add to persistent pool if space available
            if len(self._connections.get(cluster.name, [])) < self.max_connections:
                if cluster.name not in self._connections:
                    self._connections[cluster.name] = []
                    self._connection_last_used[cluster.name] = []
                
                self._connections[cluster.name].append(client)
                self._connection_last_used[cluster.name].append(datetime.now())
                logger.info(f"Created persistent SSH connection to {cluster.host} "
                          f"({len(self._connections[cluster.name])}/{self.max_connections})")
            else:
                logger.info(f"Created on-demand SSH connection to {cluster.host}")
            
            return client
            
        except Exception as e:
            client.close()
            raise ConnectionError(f"Failed to connect to {cluster.host}: {e}") from e
    
    def _remove_connection(self, cluster: ClusterType, index: int):
        """Remove connection from pool"""
        if cluster in self._connections and index < len(self._connections[cluster]):
            conn = self._connections[cluster].pop(index)
            self._connection_last_used[cluster].pop(index)
            try:
                conn.close()
            except Exception:
                pass
    
    def close_all(self):
        """Close all persistent connections. Thread-safe."""
        with self._lock:
            for cluster, connections in self._connections.items():
                for conn in connections:
                    try:
                        conn.close()
                    except Exception as e:
                        logger.debug(f"Error closing connection: {e}")
            self._connections.clear()
            self._connection_last_used.clear()
            logger.info("All SSH connections closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_all()


class SLURMJobManager:
    """
    Manages SLURM job operations (submit, monitor, cancel)
    
    Uses squeue for active jobs and sacct for historical data
    """
    
    # SLURM status code mapping
    STATUS_MAP = {
        'PD': JobStatus.PENDING,
        'PENDING': JobStatus.PENDING,
        'R': JobStatus.RUNNING,
        'RUNNING': JobStatus.RUNNING,
        'CD': JobStatus.COMPLETED,
        'COMPLETED': JobStatus.COMPLETED,
        'F': JobStatus.FAILED,
        'FAILED': JobStatus.FAILED,
        'CA': JobStatus.CANCELLED,
        'CANCELLED': JobStatus.CANCELLED,
        'TO': JobStatus.TIMEOUT,
        'TIMEOUT': JobStatus.TIMEOUT,
    }
    
    def __init__(self, connection_manager: SSHConnectionManager):
        """
        Initialize SLURM job manager
        
        Args:
            connection_manager: SSH connection manager instance
        """
        self.conn_manager = connection_manager
    
    def submit_job(
        self,
        cluster: ClusterConfig,
        script_content: str,
        work_dir: Optional[Path] = None
    ) -> str:
        """
        Submit job via sbatch
        
        Args:
            cluster: Cluster configuration
            script_content: SLURM batch script content
            work_dir: Working directory on cluster
            
        Returns:
            Submitted job ID
        """
        conn = self.conn_manager.get_connection(cluster)
        
        # Determine work directory
        remote_work_dir = work_dir or cluster.work_dir or settings.hpc_work_dir
        if not remote_work_dir:
            raise ValueError("HPC work directory not configured")
        
        try:
            # Create remote working directory
            safe_work_dir = validate_remote_path(str(remote_work_dir))
            self._exec_command(conn, f"mkdir -p {safe_work_dir}")

            # Write script to remote file
            script_path = remote_work_dir / "job_script.sh"
            self._write_remote_file(conn, script_content, script_path)

            # Submit job
            safe_script_name = validate_remote_path(script_path.name)
            result = self._exec_command(
                conn,
                f"cd {safe_work_dir} && sbatch {safe_script_name}"
            )
            
            # Parse job ID from output: "Submitted batch job 12345"
            match = re.search(r'Submitted batch job (\d+)', result)
            if not match:
                raise ValueError(f"Failed to parse job ID from sbatch output: {result}")
            
            job_id = match.group(1)
            logger.info(f"Submitted SLURM job {job_id} to {cluster.host}")
            return job_id
            
        except Exception as e:
            logger.error(f"Failed to submit job to {cluster.host}: {e}")
            raise
    
    def get_job_status(self, cluster: ClusterConfig, job_id: str) -> HPCJob:
        """
        Get job status via squeue (active) or sacct (completed)
        
        Args:
            cluster: Cluster configuration
            job_id: SLURM job ID
            
        Returns:
            HPCJob with current status
        """
        job_id = validate_job_id(job_id)
        conn = self.conn_manager.get_connection(cluster)
        
        # Try squeue first (for active jobs)
        try:
            squeue_output = self._exec_command(
                conn,
                f"squeue -j {job_id} -o '%T|%P|%D|%N|%m' --noheader"
            )
            
            if squeue_output.strip():
                return self._parse_squeue_output(job_id, cluster.name, squeue_output)
        except Exception as e:
            logger.debug(f"squeue query failed: {e}")
        
        # Fall back to sacct for completed jobs
        try:
            sacct_output = self._exec_command(
                conn,
                f"sacct -j {job_id} -o 'State,Partition,Elapsed,AllocCPUS,ExitCode' --noheader"
            )
            
            if sacct_output.strip():
                return self._parse_sacct_output(job_id, cluster.name, sacct_output)
        except Exception as e:
            logger.debug(f"sacct query failed: {e}")
        
        # If both fail, return unknown status
        logger.warning(f"Could not determine status for job {job_id}")
        return HPCJob(job_id=job_id, cluster=cluster.name, status=JobStatus.UNKNOWN)
    
    def cancel_job(self, cluster: ClusterConfig, job_id: str) -> bool:
        """
        Cancel job via scancel
        
        Args:
            cluster: Cluster configuration
            job_id: SLURM job ID
            
        Returns:
            True if cancelled successfully
        """
        conn = self.conn_manager.get_connection(cluster)

        job_id = validate_job_id(job_id)

        try:
            result = self._exec_command(conn, f"scancel {job_id}")
            logger.info(f"Cancelled job {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return False
    
    # Default timeout for SLURM commands (seconds)
    DEFAULT_COMMAND_TIMEOUT: int = 60

    def _exec_command(
        self,
        conn: SSHClient,
        command: str,
        timeout: Optional[int] = None,
    ) -> str:
        """Execute command and return output with a channel timeout.

        Args:
            conn: Active SSH client connection.
            command: Shell command to execute.
            timeout: Seconds before the channel read times out.
                     Defaults to DEFAULT_COMMAND_TIMEOUT.

        Returns:
            Stdout output with trailing whitespace stripped.

        Raises:
            RuntimeError: If the command exits non-zero or the channel times out.
        """
        effective_timeout = timeout if timeout is not None else self.DEFAULT_COMMAND_TIMEOUT
        stdin, stdout, stderr = conn.exec_command(command)
        stdout.channel.settimeout(effective_timeout)

        try:
            exit_status = stdout.channel.recv_exit_status()
        except socket.timeout:
            raise RuntimeError(
                f"Command timed out after {effective_timeout}s: {command}"
            )

        if exit_status != 0:
            error = stderr.read().decode('utf-8')
            raise RuntimeError(f"Command failed with exit code {exit_status}: {error}")

        return stdout.read().decode('utf-8').strip()
    
    def _write_remote_file(self, conn: SSHClient, content: str, remote_path: Path):
        """Write content to remote file via SFTP"""
        sftp = conn.open_sftp()
        try:
            with sftp.file(str(remote_path), 'w') as f:
                f.write(content)
        finally:
            sftp.close()
    
    def _parse_squeue_output(self, job_id: str, cluster: ClusterType, output: str) -> HPCJob:
        """Parse squeue output into HPCJob"""
        parts = output.split('|')
        status_str = parts[0] if parts else 'UNKNOWN'
        
        status = self.STATUS_MAP.get(status_str.upper(), JobStatus.UNKNOWN)
        
        return HPCJob(
            job_id=job_id,
            cluster=cluster,
            status=status,
            partition=parts[1] if len(parts) > 1 else None
        )
    
    def _parse_sacct_output(self, job_id: str, cluster: ClusterType, output: str) -> HPCJob:
        """Parse sacct output into HPCJob"""
        # Parse first line (most recent job step)
        lines = output.split('\n')
        parts = lines[0].split() if lines else []
        
        if not parts:
            return HPCJob(job_id=job_id, cluster=cluster, status=JobStatus.UNKNOWN)
        
        status_str = parts[0]
        status = self.STATUS_MAP.get(status_str.upper(), JobStatus.UNKNOWN)
        
        # Parse exit code if available
        exit_code = None
        if len(parts) > 4:
            exit_match = re.search(r'(\d+):(\d+)', parts[4])
            if exit_match:
                exit_code = int(exit_match.group(1))
        
        return HPCJob(
            job_id=job_id,
            cluster=cluster,
            status=status,
            partition=parts[1] if len(parts) > 1 else None,
            exit_code=exit_code
        )


class HPCFileTransfer:
    """
    Manages file uploads and downloads to/from HPC clusters

    Uses SFTP for reliable file transfer with resume capability
    """

    DEFAULT_COMMAND_TIMEOUT: int = 60

    def __init__(self, connection_manager: SSHConnectionManager):
        """
        Initialize file transfer manager

        Args:
            connection_manager: SSH connection manager instance
        """
        self.conn_manager = connection_manager

    def _exec_command(
        self,
        conn: SSHClient,
        command: str,
        timeout: Optional[int] = None,
    ) -> str:
        """Execute command and return output with a channel timeout."""
        effective_timeout = timeout if timeout is not None else self.DEFAULT_COMMAND_TIMEOUT
        stdin, stdout, stderr = conn.exec_command(command)
        stdout.channel.settimeout(effective_timeout)

        try:
            exit_status = stdout.channel.recv_exit_status()
        except socket.timeout:
            raise RuntimeError(
                f"Command timed out after {effective_timeout}s: {command}"
            )

        output = stdout.read().decode("utf-8").strip()
        error_output = stderr.read().decode("utf-8").strip()

        if exit_status != 0:
            raise RuntimeError(f"Command failed with exit code {exit_status}: {error_output}")

        return output
    
    def upload_file(
        self,
        cluster: ClusterConfig,
        local_path: Path,
        remote_path: Path,
        overwrite: bool = True
    ) -> bool:
        """
        Upload file to cluster
        
        Args:
            cluster: Cluster configuration
            local_path: Local file path
            remote_path: Remote destination path
            overwrite: Overwrite if exists
            
        Returns:
            True if uploaded successfully
        """
        conn = self.conn_manager.get_connection(cluster)

        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        # Validate remote paths to prevent command injection
        safe_remote_path = validate_remote_path(str(remote_path))
        safe_remote_dir = validate_remote_path(str(remote_path.parent))

        # Create remote directory if needed
        self._ensure_remote_directory(conn, remote_path.parent)

        try:
            sftp = conn.open_sftp()
            try:
                sftp.put(str(local_path), safe_remote_path)
                logger.info(f"Uploaded {local_path} to {cluster.host}:{remote_path}")
                return True
            finally:
                sftp.close()
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise

    def download_file(
        self,
        cluster: ClusterConfig,
        remote_path: Path,
        local_path: Path,
        overwrite: bool = False
    ) -> bool:
        """
        Download file from cluster

        Args:
            cluster: Cluster configuration
            remote_path: Remote file path
            local_path: Local destination path
            overwrite: Overwrite if exists

        Returns:
            True if downloaded successfully
        """
        conn = self.conn_manager.get_connection(cluster)

        # Validate remote path to prevent command injection
        safe_remote_path = validate_remote_path(str(remote_path))
        
        if local_path.exists() and not overwrite:
            raise FileExistsError(f"Local file exists: {local_path}")
        
        try:
            sftp = conn.open_sftp()
            try:
                sftp.get(safe_remote_path, str(local_path))
                logger.info(f"Downloaded {cluster.host}:{remote_path} to {local_path}")
                return True
            finally:
                sftp.close()
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            raise
    
    def list_directory(
        self,
        cluster: ClusterConfig,
        remote_path: Path
    ) -> List[str]:
        """
        List files in remote directory
        
        Args:
            cluster: Cluster configuration
            remote_path: Remote directory path
            
        Returns:
            List of file names
        """
        conn = self.conn_manager.get_connection(cluster)

        # Validate remote path to prevent command injection
        safe_remote_path = validate_remote_path(str(remote_path))

        try:
            sftp = conn.open_sftp()
            try:
                return sftp.listdir(safe_remote_path)
            finally:
                sftp.close()
        except Exception as e:
            logger.error(f"Failed to list directory: {e}")
            raise
    
    def _ensure_remote_directory(self, conn: SSHClient, remote_path: Path):
        """Create remote directory if it doesn't exist"""
        safe_path = validate_remote_path(str(remote_path))
        try:
            sftp = conn.open_sftp()
            try:
                # Check if directory exists
                sftp.stat(safe_path)
            except IOError:
                # Directory doesn't exist, create it
                self._exec_command(conn, f"mkdir -p {safe_path}")
            finally:
                sftp.close()
        except (IOError, OSError) as e:
            logger.error(f"Failed to create remote directory: {e}")
            raise


class HPCAdapter:
    """
    Main HPC adapter facade
    
    Coordinates SSH connections, job management, and file transfer
    with automatic cluster failover
    """
    
    def __init__(self):
        """Initialize HPC adapter with connection manager"""
        self.conn_manager = SSHConnectionManager()
        self.job_manager = SLURMJobManager(self.conn_manager)
        self.file_transfer = HPCFileTransfer(self.conn_manager)
        
        # Configure clusters from settings
        self._clusters: List[ClusterConfig] = []
        self._configure_clusters()
    
    def _configure_clusters(self):
        """Configure cluster list from settings"""
        # Primary cluster (Guangzhou)
        if settings.hpc_host:
            self._clusters.append(ClusterConfig(
                name=ClusterType.GUANGZHOU,
                host=settings.hpc_host,
                port=settings.hpc_port,
                username=settings.hpc_user,
                ssh_key_path=settings.hpc_ssh_key_path,
                work_dir=settings.hpc_work_dir,
                is_primary=True
            ))
        
        # Backup cluster (Tianjin) - not yet configured
        # TODO: Add when Tianjin cluster details are confirmed
    
    def submit_lammps_job(
        self,
        potential_file: Path,
        structure_file: Path,
        config: Dict[str, Any],
        cluster: Optional[ClusterType] = None
    ) -> str:
        """
        Submit LAMMPS job to HPC cluster
        
        Args:
            potential_file: Path to potential file
            structure_file: Path to structure file
            config: Job configuration (resources, simulation parameters)
            cluster: Target cluster (None for primary)
            
        Returns:
            Submitted job ID
        """
        # Select cluster
        target_cluster = self._select_cluster(cluster)
        
        # Generate SLURM script
        script_content = self._generate_slurm_script(config)
        
        # Upload input files
        work_dir = target_cluster.work_dir or settings.hpc_work_dir
        job_dir = work_dir / f"job_{int(time.time())}"
        
        self.file_transfer.upload_file(
            target_cluster,
            potential_file,
            job_dir / "potential.file"
        )
        self.file_transfer.upload_file(
            target_cluster,
            structure_file,
            job_dir / "structure.file"
        )
        
        # Submit job
        job_id = self.job_manager.submit_job(
            target_cluster,
            script_content,
            job_dir
        )
        
        return job_id
    
    def monitor_job(self, job_id: str, cluster: ClusterType) -> HPCJob:
        """
        Monitor job status
        
        Args:
            job_id: SLURM job ID
            cluster: Cluster where job was submitted
            
        Returns:
            Current job status
        """
        cluster_config = self._get_cluster_config(cluster)
        return self.job_manager.get_job_status(cluster_config, job_id)
    
    def download_results(
        self,
        job_id: str,
        cluster: ClusterType,
        local_dir: Path
    ) -> List[Path]:
        """
        Download job results
        
        Args:
            job_id: SLURM job ID
            cluster: Cluster where job was submitted
            local_dir: Local destination directory
            
        Returns:
            List of downloaded file paths
        """
        job_id = validate_job_id(job_id)
        cluster_config = self._get_cluster_config(cluster)
        work_dir = cluster_config.work_dir or settings.hpc_work_dir
        job_dir = work_dir / f"job_{job_id}"
        
        # List remote files
        remote_files = self.file_transfer.list_directory(cluster_config, job_dir)
        
        # Download files
        downloaded = []
        for filename in remote_files:
            remote_path = job_dir / filename
            local_path = local_dir / filename
            
            self.file_transfer.download_file(
                cluster_config,
                remote_path,
                local_path
            )
            downloaded.append(local_path)
        
        return downloaded
    
    def _select_cluster(self, preferred: Optional[ClusterType]) -> ClusterConfig:
        """Select cluster with failover support"""
        if preferred:
            return self._get_cluster_config(preferred)
        
        # Try primary cluster first
        if self._clusters:
            return self._clusters[0]
        
        raise ValueError("No HPC clusters configured")
    
    def _get_cluster_config(self, cluster: ClusterType) -> ClusterConfig:
        """Get cluster configuration by type"""
        for cluster_config in self._clusters:
            if cluster_config.name == cluster:
                return cluster_config
        raise ValueError(f"Cluster not configured: {cluster}")
    
    def _generate_slurm_script(self, config: Dict[str, Any]) -> str:
        """Generate SLURM batch script with input validation.

        All user-controlled string parameters are validated against shell
        injection characters before interpolation into the script.

        Args:
            config: Job configuration (resources, simulation parameters).

        Returns:
            SLURM batch script content.

        Raises:
            ValueError: If any user-controlled parameter contains shell-unsafe characters.
        """
        partition = validate_shell_safe(
            str(config.get('partition', settings.slurm_partition)), "partition"
        )
        nodes = validate_positive_int(
            config.get('nodes', settings.slurm_nodes), "nodes"
        )
        ntasks = validate_positive_int(
            config.get('ntasks_per_node', settings.slurm_ntasks_per_node),
            "ntasks_per_node",
        )
        walltime = validate_shell_safe(
            str(config.get('walltime', '24:00:00')), "walltime"
        )
        validate_shell_safe(str(settings.lammps_modules), "lammps_modules")
        
        script = f"""#!/bin/bash
#SBATCH --partition={partition}
#SBATCH --nodes={nodes}
#SBATCH --ntasks-per-node={ntasks}
#SBATCH --time={walltime}
#SBATCH --job-name=nfm-lammps-job
#SBATCH --output=lammps_%j.out
#SBATCH --error=lammps_%j.err

# Load LAMMPS modules
{settings.lammps_modules}

# Run LAMMPS
lmp -in input.in > lammps.log

# Run OVITO defect analysis (if enabled)
if [ "{settings.ovito_enabled}" = "True" ]; then
    ovito --script defect_analysis.py trajectory.dump
fi
"""
        return script
    
    def close(self):
        """Close all connections"""
        self.conn_manager.close_all()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
