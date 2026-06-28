"""Tests for the SLURM Executor (NFM-377).

Implements the Executor Protocol from ports.py.
All SSH interactions are mocked — no real connections.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_md_runner.ports import (
    Executor,
    ExecutionError,
    JobSpec,
    JobStatus,
    JobOutput,
)
from nfm_md_runner.slurm_executor import SlurmExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_transport():
    """Mock SSH transport with controllable responses."""
    transport = AsyncMock()
    transport.exec_command = AsyncMock()
    transport.write_remote_file = AsyncMock()
    transport.download_file = AsyncMock()
    transport.close = AsyncMock()
    return transport


@pytest.fixture
def mock_credentials():
    """Frozen credentials matching test expectations."""
    return MagicMock(
        host="hpc.example.com",
        user="nfmd_user",
        ssh_key_path="/home/nfmd/.ssh/id_ed25519",
        port=22,
        work_dir="/scratch/nfmd",
    )


def _default_spec(**overrides: Any) -> JobSpec:
    defaults = dict(
        potential_file="UO2.eam.alloy",
        structure_file="UO2.lmp",
        temperature=300.0,
        steps=1000,
        partition="compute",
        nodes=1,
        walltime="24:00:00",
    )
    defaults.update(overrides)
    return JobSpec(**defaults)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestSlurmExecutorInit:
    """Tests for executor initialization."""

    def test_is_executor(self):
        """SlurmExecutor satisfies the Executor Protocol."""
        transport = AsyncMock()
        creds = MagicMock()
        ex = SlurmExecutor(transport=transport, credentials=creds)
        assert isinstance(ex, Executor)

    def test_stores_credentials(self):
        """Credentials are stored and accessible."""
        creds = MagicMock()
        ex = SlurmExecutor(transport=AsyncMock(), credentials=creds)
        assert ex.credentials is creds


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


class TestSlurmExecutorSubmit:
    """Tests for job submission via sbatch."""

    @pytest.mark.asyncio
    async def test_submit_returns_job_id(self, mock_transport, mock_credentials):
        """submit generates sbatch script, uploads, and returns SLURM job ID."""
        mock_transport.exec_command.return_value = "Submitted batch job 42"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        job_id = await ex.submit(_default_spec())

        assert job_id == "42"

    @pytest.mark.asyncio
    async def test_submit_creates_remote_work_dir(self, mock_transport, mock_credentials):
        """submit creates remote working directory before uploading."""
        mock_transport.exec_command.return_value = "Submitted batch job 42"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        await ex.submit(_default_spec())

        # First exec_command should be mkdir -p
        mkdir_call = mock_transport.exec_command.call_args_list[0]
        assert "mkdir" in mkdir_call[0][0].lower()

    @pytest.mark.asyncio
    async def test_submit_writes_script_remotely(self, mock_transport, mock_credentials):
        """submit writes the sbatch script to the remote host."""
        mock_transport.exec_command.return_value = "Submitted batch job 42"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        await ex.submit(_default_spec())

        assert mock_transport.write_remote_file.call_count >= 1
        # First write is the sbatch script
        write_call = mock_transport.write_remote_file.call_args_list[0]
        script_content = write_call[0][0]
        assert "#!/bin/bash" in script_content
        assert "#SBATCH" in script_content

    @pytest.mark.asyncio
    async def test_submit_sbatch_script_content(self, mock_transport, mock_credentials):
        """Generated sbatch script contains correct SLURM directives."""
        mock_transport.exec_command.return_value = "Submitted batch job 42"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        spec = _default_spec(partition="gpu", nodes=2, walltime="48:00:00")
        await ex.submit(spec)

        write_call = mock_transport.write_remote_file.call_args_list[0]
        script = write_call[0][0]
        assert "--partition=gpu" in script
        assert "--nodes=2" in script
        assert "--time=48:00:00" in script

    @pytest.mark.asyncio
    async def test_submit_executes_sbatch(self, mock_transport, mock_credentials):
        """submit runs sbatch on the remote host."""
        mock_transport.exec_command.return_value = "Submitted batch job 42"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        await ex.submit(_default_spec())

        # Last exec_command should be sbatch
        sbatch_call = mock_transport.exec_command.call_args_list[-1]
        assert "sbatch" in sbatch_call[0][0].lower()

    @pytest.mark.asyncio
    async def test_submit_parse_error_raises(self, mock_transport, mock_credentials):
        """submit raises ExecutionError when sbatch output is unexpected."""
        mock_transport.exec_command.return_value = "unexpected garbage"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        with pytest.raises(ExecutionError, match="Failed to parse SLURM job ID"):
            await ex.submit(_default_spec())

    @pytest.mark.asyncio
    async def test_submit_connection_error_raises(self, mock_transport, mock_credentials):
        """submit raises ExecutionError when SSH connection fails."""
        mock_transport.exec_command.side_effect = ConnectionError("unreachable")
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        with pytest.raises(ExecutionError, match="SSH connection failed"):
            await ex.submit(_default_spec())


# ---------------------------------------------------------------------------
# poll
# ---------------------------------------------------------------------------


class TestSlurmExecutorPoll:
    """Tests for job status polling via squeue/sacct."""

    @pytest.mark.asyncio
    async def test_poll_running(self, mock_transport, mock_credentials):
        """poll returns RUNNING when squeue shows R."""
        mock_transport.exec_command.return_value = "R|compute|2|node01|01:00:00"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        status = await ex.poll("42")

        assert status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_poll_pending(self, mock_transport, mock_credentials):
        """poll returns PENDING when squeue shows PD."""
        mock_transport.exec_command.return_value = "PD|compute||01:00:00"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        status = await ex.poll("42")

        assert status == JobStatus.PENDING

    @pytest.mark.asyncio
    async def test_poll_completed_sacct(self, mock_transport, mock_credentials):
        """poll returns COMPLETED when squeue is empty but sacct shows COMPLETED."""
        mock_transport.exec_command.side_effect = [
            "",
            "COMPLETED compute 01:23:45 32 0:0",
        ]
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        status = await ex.poll("42")

        assert status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_poll_failed(self, mock_transport, mock_credentials):
        """poll returns FAILED when sacct shows FAILED."""
        mock_transport.exec_command.side_effect = [
            "",
            "FAILED compute 01:23:45 32 1:0",
        ]
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        status = await ex.poll("42")

        assert status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_poll_unknown(self, mock_transport, mock_credentials):
        """poll returns UNKNOWN when both squeue and sacct are empty."""
        mock_transport.exec_command.return_value = ""
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        status = await ex.poll("42")

        assert status == JobStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_poll_connection_error_raises(self, mock_transport, mock_credentials):
        """poll raises ExecutionError when SSH connection fails."""
        mock_transport.exec_command.side_effect = ConnectionError("unreachable")
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        with pytest.raises(ExecutionError, match="SSH connection failed"):
            await ex.poll("42")


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


class TestSlurmExecutorCancel:
    """Tests for job cancellation via scancel."""

    @pytest.mark.asyncio
    async def test_cancel_returns_true(self, mock_transport, mock_credentials):
        """cancel returns True when scancel succeeds."""
        mock_transport.exec_command.return_value = ""
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        result = await ex.cancel("42")

        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_uses_scancel(self, mock_transport, mock_credentials):
        """cancel runs scancel command on remote host."""
        mock_transport.exec_command.return_value = ""
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        await ex.cancel("42")

        mock_transport.exec_command.assert_called_once()
        assert "scancel" in mock_transport.exec_command.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cancel_connection_error_returns_false(self, mock_transport, mock_credentials):
        """cancel returns False on SSH failure."""
        mock_transport.exec_command.side_effect = ConnectionError("unreachable")
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        result = await ex.cancel("42")

        assert result is False


# ---------------------------------------------------------------------------
# retrieve_output
# ---------------------------------------------------------------------------


class TestSlurmExecutorRetrieveOutput:
    """Tests for output retrieval."""

    @pytest.mark.asyncio
    async def test_retrieve_output_calls_download(self, mock_transport, mock_credentials):
        """retrieve_output downloads output files from remote host."""
        mock_transport.exec_command.return_value = "COMPLETED|0"
        mock_transport.download_file.return_value = b"stdout output"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        output = await ex.retrieve_output("42")

        assert isinstance(output, JobOutput)
        assert output.job_id == "42"
        mock_transport.download_file.assert_called()

    @pytest.mark.asyncio
    async def test_retrieve_output_includes_stdout(self, mock_transport, mock_credentials):
        """retrieve_output includes stdout in the result."""
        mock_transport.exec_command.return_value = "COMPLETED|0"
        mock_transport.download_file.return_value = b"LAMMPS run complete\n"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        output = await ex.retrieve_output("42")

        assert output.log_output == "LAMMPS run complete\n"

    @pytest.mark.asyncio
    async def test_retrieve_output_failed_job(self, mock_transport, mock_credentials):
        """retrieve_output sets status to FAILED for failed jobs."""
        mock_transport.exec_command.return_value = "FAILED|1"
        mock_transport.download_file.return_value = b"ERROR: something failed\n"
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        output = await ex.retrieve_output("42")

        assert output.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_retrieve_output_connection_error_raises(self, mock_transport, mock_credentials):
        """retrieve_output raises ExecutionError when SSH fails."""
        mock_transport.exec_command.side_effect = ConnectionError("unreachable")
        ex = SlurmExecutor(transport=mock_transport, credentials=mock_credentials)

        with pytest.raises(ExecutionError, match="SSH connection failed"):
            await ex.retrieve_output("42")
