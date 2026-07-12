"""HPC SSH Connection Management.

Provides SSH connection pooling with multi-login node support,
health monitoring, and auto-reconnect capabilities.
"""

import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import paramiko

from nfm_db.services.hpc_metrics import (
    PROMETHEUS_AVAILABLE,
    hpc_active_connections,
    hpc_connection_errors,
)

logger = logging.getLogger(__name__)


class JobSubmissionError(Exception):
    """Exception raised when SLURM job submission fails."""

    pass


class HPCConnectionError(Exception):
    """Exception raised when HPC cluster connection fails."""

    pass


@dataclass(frozen=True)
class SSHConnectionConfig:
    """Immutable SSH connection configuration."""

    hosts: tuple[str, ...]
    username: str
    ssh_key_path: str
    max_connections: int = 10
    heartbeat_interval: int = 30
    skip_key_validation: bool = False
    known_hosts_path: str | None = None
    backup_hosts: tuple[str, ...] | None = None
    backup_username: str | None = None
    backup_ssh_key_path: str | None = None
    failover_threshold_seconds: int = 300
    work_dir: str = "/scratch/{username}/nfm-md"

    @classmethod
    def from_lists(
        cls,
        hosts: list[str],
        username: str,
        ssh_key_path: str,
        max_connections: int = 10,
        heartbeat_interval: int = 30,
        skip_key_validation: bool = False,
        known_hosts_path: str | None = None,
        backup_hosts: list[str] | None = None,
        backup_username: str | None = None,
        backup_ssh_key_path: str | None = None,
        failover_threshold_seconds: int = 300,
        work_dir: str = "/scratch/{username}/nfm-md",
    ) -> "SSHConnectionConfig":
        """Create SSHConnectionConfig from mutable lists (backward compat)."""
        return cls(
            hosts=tuple(hosts),
            username=username,
            ssh_key_path=ssh_key_path,
            max_connections=max_connections,
            heartbeat_interval=heartbeat_interval,
            skip_key_validation=skip_key_validation,
            known_hosts_path=known_hosts_path,
            backup_hosts=tuple(backup_hosts) if backup_hosts else None,
            backup_username=backup_username,
            backup_ssh_key_path=backup_ssh_key_path,
            failover_threshold_seconds=failover_threshold_seconds,
            work_dir=work_dir,
        )


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
        host: str | list[str] | None = None,
        username: str | None = None,
        ssh_key_path: str | None = None,
        max_connections: int = 10,
        hosts: str | list[str] | None = None,
        skip_key_validation: bool = False,
        known_hosts_path: str | None = None,
    ) -> None:
        """Initialize SSH connection manager.

        Args:
            host: Single host or list of hosts for multi-login support
            username: SSH username
            ssh_key_path: Path to SSH private key file
            max_connections: Maximum concurrent connections (default: 10)
            hosts: Alternative parameter name for multi-login support
            skip_key_validation: Skip SSH key file validation (for testing)
            known_hosts_path: Path to known_hosts file for host key verification
        """
        host_value = hosts if hosts is not None else host

        if isinstance(host_value, str):
            self.hosts = [host_value]
        else:
            self.hosts = host_value if host_value else []

        self.username = username
        self.ssh_key_path = ssh_key_path
        self.max_connections = max_connections
        self._skip_key_validation = skip_key_validation
        self._known_hosts_path = known_hosts_path

        self._active_connections: set = set()
        self._connection_lock = threading.Lock()

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
            if len(self._active_connections) >= self.max_connections:
                raise ConnectionError("Connection pool exhausted")

            if not self._skip_key_validation and not Path(self.ssh_key_path).exists():
                raise FileNotFoundError(f"SSH key file not found: {self.ssh_key_path}")

            client = self._create_ssh_connection()
            self._active_connections.add(client)
            return client

    def acquire_connection_with_retry(
        self, max_retries: int = 3, backoff_base: float = 1.0
    ) -> paramiko.SSHClient | None:
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

                wait_time = backoff_base * (2**attempt)
                logger.warning(
                    f"Connection attempt {attempt + 1} failed, retrying in {wait_time}s: {e}"
                )
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
                with contextlib.suppress(Exception):
                    client.close()

            if PROMETHEUS_AVAILABLE:
                hpc_active_connections.labels(cluster=self.host).set(len(self._active_connections))

    def cleanup(self) -> None:
        """Clean up all connections and resources.

        This method should be called when the manager is no longer needed
        to prevent memory leaks.
        """
        with self._connection_lock:
            for client in list(self._active_connections):
                try:
                    if hasattr(client, "transport") and client.transport:
                        client.transport.close()
                    client.close()
                except Exception:
                    pass
                del client
            self._active_connections.clear()
            self.hosts = []

    def __del__(self) -> None:
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
            _stdin, stdout, _stderr = client.exec_command("echo 'health_check'")
            stdout.read()
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    def _create_ssh_connection(self) -> paramiko.SSHClient:
        """Create a new SSH connection using key authentication.

        Host key policy:
        - Default: RejectPolicy (rejects unknown keys, prevents MITM)
        - skip_key_validation=True: AutoAddPolicy (for test/local only)
        - known_hosts_path set: loads known keys, then uses RejectPolicy

        Returns:
            Connected SSH client

        Raises:
            ConnectionError: If connection fails
        """
        client = paramiko.SSHClient()

        if self._skip_key_validation:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            if self._known_hosts_path:
                client.load_host_keys(self._known_hosts_path)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        host = self.hosts[0]

        try:
            client.connect(
                hostname=host, username=self.username, key_filename=self.ssh_key_path, timeout=10
            )
            logger.info(f"SSH connection established to {host}")

            if PROMETHEUS_AVAILABLE:
                hpc_active_connections.labels(cluster=host).inc()

            return client
        except paramiko.AuthenticationException as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type="authentication").inc()
            client.close()
            raise ConnectionError(f"Authentication failed for {host}: {e}")
        except paramiko.SSHException as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type="ssh").inc()
            client.close()
            raise ConnectionError(f"SSH connection failed to {host}: {e}")
        except Exception as e:
            if PROMETHEUS_AVAILABLE:
                hpc_connection_errors.labels(cluster=host, error_type="unknown").inc()
            client.close()
            raise ConnectionError(f"Failed to connect to {host}: {e}")
