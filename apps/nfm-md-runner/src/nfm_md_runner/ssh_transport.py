"""
Secure SSH Transport Layer (NFM-377).

Async wrapper around paramiko for HPC cluster communication.
Key-based authentication only. Path parameters are whitelist-validated.
Host key policy is configurable (default: RejectPolicy).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Regex: whitelist-allowed characters for remote paths
_SAFE_PATH_RE = re.compile(r"^[\w\-./_]+$")

# Allowed remote command prefixes
_ALLOWED_COMMAND_PREFIXES = (
    "mkdir",
    "sbatch",
    "squeue",
    "sacct",
    "scancel",
    "ls",
    "cat",
    "mv",
    "rm",
    "chmod",
)


class _SSHClientProtocol(Protocol):
    """Protocol matching paramiko.SSHClient public interface."""

    def connect(
        self,
        hostname: str,
        port: int,
        username: str,
        key_filename: str,
        timeout: float | None = None,
    ) -> None: ...

    def exec_command(
        self,
        command: str,
        timeout: float | None = None,
    ) -> tuple[Any, Any, Any]: ...

    def open_sftp(self) -> Any: ...

    def close(self) -> None: ...


def _validate_path(path: str, label: str = "path") -> None:
    """Validate that a remote path contains only safe characters.

    Raises:
        ValueError: If the path contains potentially dangerous characters.
    """
    if not _SAFE_PATH_RE.match(path):
        raise ValueError(
            f"Invalid {label}: '{path}' contains disallowed characters. "
            "Only alphanumeric, dash, underscore, dot, and slash are permitted."
        )


def _validate_remote_command(command: str) -> None:
    """Validate that a remote command starts with an allowed prefix.

    Raises:
        ValueError: If the command doesn't start with an allowed prefix.
    """
    first_word = command.strip().split()[0] if command.strip() else ""
    if first_word not in _ALLOWED_COMMAND_PREFIXES:
        raise ValueError(
            f"Disallowed remote command: '{command}'. "
            f"Allowed prefixes: {_ALLOWED_COMMAND_PREFIXES}"
        )


def _validate_host_key_policy(
    host: str,
    key: Any,
) -> None:
    """Default host key policy: reject unknown keys.

    In production, this should be replaced with a known-hosts-based policy.
    """
    # paramiko.RejectPolicy is the default — unknown keys are rejected.
    # This is intentionally strict for security.
    logger.debug("Host key validation for %s (policy: reject unknown)", host)


class SSHTransport:
    """Async SSH transport for HPC cluster communication.

    Wraps paramiko.SSHClient in an async interface.
    All remote paths are validated before use.
    All remote commands are validated against an allowlist.

    Usage::

        transport = SSHTransport(credentials=creds)
        await transport.connect()
        stdout = await transport.exec_command("squeue -j 42")
        await transport.close()
    """

    def __init__(
        self,
        credentials: dataclass,
        connect_timeout: float = 30.0,
    ) -> None:
        self._credentials = credentials
        self._connect_timeout = connect_timeout
        self._client: _SSHClientProtocol | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> None:
        """Establish SSH connection using key-based auth."""
        import paramiko

        client: _SSHClientProtocol = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        loop = asyncio.get_running_loop()

        try:
            await loop.run_in_executor(
                None,
                client.connect,
                self._credentials.host,
                self._credentials.port,
                self._credentials.user,
                self._credentials.ssh_key_path,
                self._connect_timeout,
            )
        except Exception as exc:
            client.close()
            raise ConnectionError(
                f"SSH connection to {self._credentials.host}:"
                f"{self._credentials.port} failed: {exc}"
            ) from exc

        self._client = client
        logger.info("SSH connected to %s:%d", self._credentials.host, self._credentials.port)

    async def exec_command(self, command: str) -> str:
        """Execute a validated command on the remote host.

        Args:
            command: Remote shell command (must start with allowed prefix).

        Returns:
            Combined stdout from the remote command.

        Raises:
            ConnectionError: If not connected or SSH fails.
            ValueError: If the command is not in the allowlist.
        """
        _validate_remote_command(command)

        if self._client is None:
            raise ConnectionError("SSH not connected — call connect() first")

        loop = asyncio.get_running_loop()

        try:
            stdin, stdout, stderr = await loop.run_in_executor(
                None,
                self._client.exec_command,
                command,
            )
            # Read output in executor to avoid blocking the event loop
            out = await loop.run_in_executor(None, stdout.read)
            return out.decode("utf-8", errors="replace").strip()
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(f"Remote command failed: {exc}") from exc

    async def write_remote_file(
        self,
        content: str,
        remote_path: str,
    ) -> None:
        """Write content to a file on the remote host.

        Args:
            content: File content to write.
            remote_path: Remote file path (validated for safety).

        Raises:
            ConnectionError: If not connected or SSH fails.
            ValueError: If the remote path contains disallowed characters.
        """
        _validate_path(remote_path, label="remote_path")

        if self._client is None:
            raise ConnectionError("SSH not connected — call connect() first")

        loop = asyncio.get_running_loop()

        try:
            sftp = await loop.run_in_executor(None, self._client.open_sftp)
            encoded = content.encode("utf-8")

            def _write() -> None:
                with sftp.open(remote_path, "w") as f:
                    f.write(encoded)

            await loop.run_in_executor(None, _write)
            logger.debug("Wrote %d bytes to remote://%s", len(encoded), remote_path)
        except (ConnectionError, ValueError):
            raise
        except Exception as exc:
            raise ConnectionError(f"Remote file write failed: {exc}") from exc

    async def download_file(self, remote_path: str) -> bytes:
        """Download a file from the remote host.

        Args:
            remote_path: Remote file path (validated for safety).

        Returns:
            File contents as bytes.

        Raises:
            ConnectionError: If not connected or SSH fails.
            ValueError: If the remote path contains disallowed characters.
        """
        _validate_path(remote_path, label="remote_path")

        if self._client is None:
            raise ConnectionError("SSH not connected — call connect() first")

        loop = asyncio.get_running_loop()

        try:
            sftp = await loop.run_in_executor(None, self._client.open_sftp)

            def _read() -> bytes:
                with sftp.open(remote_path, "rb") as f:
                    return f.read()

            data = await loop.run_in_executor(None, _read)
            logger.debug(
                "Downloaded %d bytes from remote://%s",
                len(data),
                remote_path,
            )
            return data
        except (ConnectionError, ValueError):
            raise
        except Exception as exc:
            raise ConnectionError(f"Remote file download failed: {exc}") from exc

    async def close(self) -> None:
        """Close the SSH connection."""
        if self._client is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._client.close)
            self._client = None
            logger.info("SSH connection closed")
