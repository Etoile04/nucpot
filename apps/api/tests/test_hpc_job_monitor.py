"""Comprehensive tests for HPC Job Monitoring module.

Tests cover all public functions in hpc_job_monitor.py:
- execute_squeue
- check_job_completion
- poll_job_status
- update_job_status
- get_active_jobs
- sync_all_active_jobs
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.md_verification import HpcJob, HpcJobStatus
from nfm_db.services.hpc_job_monitor import (
    check_job_completion,
    execute_squeue,
    get_active_jobs,
    poll_job_status,
    sync_all_active_jobs,
    update_job_status,
)

# get_db is imported lazily inside functions, so we patch at the source module
_DB_PATCH_TARGET = "nfm_db.database.get_db"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_channel(exit_status: int = 0, stderr_bytes: bytes = b"") -> MagicMock:
    """Build a mock SSH channel that returns the given exit status."""
    channel = MagicMock()
    channel.recv_exit_status.return_value = exit_status
    channel.stderr = MagicMock()
    channel.stderr.read.return_value = stderr_bytes
    return channel


def _make_stdout(text: str, channel: MagicMock) -> MagicMock:
    """Build a mock stdout with the given text and channel reference."""
    stdout = MagicMock()
    stdout.read.return_value = text.encode()
    stdout.channel = channel
    return stdout


def _make_sftp_stat(file_path: str, size: int = 0) -> MagicMock:
    """Return a stat mock that raises IOError unless the path matches."""
    stat_mock = MagicMock()

    def _stat_side_effect(path: str, *_args: Any, **_kwargs: Any) -> MagicMock:
        if path == file_path:
            result = MagicMock()
            result.st_size = size
            return result
        raise OSError(f"File not found: {path}")

    stat_mock.side_effect = _stat_side_effect
    return stat_mock


@pytest.fixture()
def ssh_manager() -> MagicMock:
    """Return a mock SSHConnectionManager."""
    manager = MagicMock()
    manager.acquire_connection = MagicMock()
    manager.release_connection = MagicMock()
    return manager


@pytest.fixture()
def mock_client() -> MagicMock:
    """Return a mock SSH client."""
    return MagicMock()


@pytest.fixture()
def mock_db_session() -> AsyncMock:
    """Return a mock async database session."""
    return AsyncMock()


@pytest.fixture()
def sample_hpc_job() -> HpcJob:
    """Return a sample HpcJob ORM object (not committed to DB)."""
    job = HpcJob(
        hpc_job_id="slurm-12345",
        hpc_cluster="test-cluster",
        verification_job_id="a0000000-0000-0000-0000-000000000001",
    )
    return job


def _mock_db_gen(session: AsyncMock) -> AsyncMock:
    """Create a mock async generator that yields *session* once."""
    gen = AsyncMock()
    gen.__anext__ = AsyncMock(side_effect=[session, StopAsyncIteration()])
    return gen


# ---------------------------------------------------------------------------
# execute_squeue
# ---------------------------------------------------------------------------


class TestExecuteSqueue:
    """Tests for execute_squeue(ssh_manager, hpc_job_id)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_output_on_success(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=0)
        stdout = _make_stdout("RUNNING test-job\n", channel)
        stderr = MagicMock()
        stderr.read.return_value = b""

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        result = await execute_squeue(ssh_manager, "slurm-12345")

        assert result == "RUNNING test-job"
        mock_client.exec_command.assert_called_once_with(
            "squeue -j 12345 -o '%T %j'"
        )
        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_none_when_job_not_in_queue(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=0)
        stdout = _make_stdout("", channel)
        stderr = MagicMock()
        stderr.read.return_value = b""

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        result = await execute_squeue(ssh_manager, "slurm-99999")

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_none_on_slurm_load_error(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=1)
        stdout = _make_stdout("", channel)
        stderr = MagicMock()
        stderr.read.return_value = b"slurm_load_error: invalid job id"

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        result = await execute_squeue(ssh_manager, "slurm-12345")

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_job_id_error(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=1)
        stdout = _make_stdout("", channel)
        stderr = MagicMock()
        stderr.read.return_value = b"Invalid job id specified"

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        result = await execute_squeue(ssh_manager, "slurm-12345")

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_raises_on_invalid_job_id_format(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        with pytest.raises(ValueError, match="Invalid job ID format"):
            await execute_squeue(ssh_manager, "slurm-abcde")

        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_raises_on_squeue_command_failure(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=1)
        stdout = _make_stdout("", channel)
        stderr = MagicMock()
        stderr.read.return_value = b"some other error"

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        with pytest.raises(Exception, match="squeue command failed"):
            await execute_squeue(ssh_manager, "slurm-12345")

        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_released_in_finally_block(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client
        mock_client.exec_command.side_effect = RuntimeError("SSH broke")

        with pytest.raises(RuntimeError, match="SSH broke"):
            await execute_squeue(ssh_manager, "slurm-12345")

        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_strips_slurm_prefix_from_job_id(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        channel = _make_channel(exit_status=0)
        stdout = _make_stdout("PENDING test-job\n", channel)
        stderr = MagicMock()
        stderr.read.return_value = b""

        mock_client.exec_command.return_value = (None, stdout, stderr)
        ssh_manager.acquire_connection.return_value = mock_client

        await execute_squeue(ssh_manager, "slurm-42")

        mock_client.exec_command.assert_called_once_with("squeue -j 42 -o '%T %j'")


# ---------------------------------------------------------------------------
# check_job_completion
# ---------------------------------------------------------------------------


class TestCheckJobCompletion:
    """Tests for check_job_completion(ssh_manager, task_id)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_true_when_lammps_out_exists_with_size(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = [
            _make_sftp_stat(
                "$SCRATCH/nfm-md/task-001/lammps.out", size=1024
            ),
        ]
        mock_sftp.stat = _make_sftp_stat(
            "$SCRATCH/nfm-md/task-001/lammps.out", size=1024
        )
        mock_client.open_sftp.return_value = mock_sftp

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is True
        ssh_manager.release_connection.assert_called_once_with(mock_client)
        mock_sftp.close.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_true_when_log_lammps_exists_with_size(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        mock_sftp = MagicMock()

        def stat_side_effect(path: str) -> MagicMock:
            if path.endswith("lammps.out"):
                raise OSError("not found")
            result = MagicMock()
            result.st_size = 500
            return result

        mock_sftp.stat.side_effect = stat_side_effect
        mock_client.open_sftp.return_value = mock_sftp

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_false_when_no_output_files(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = OSError("not found")
        mock_client.open_sftp.return_value = mock_sftp

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_false_when_output_file_has_zero_size(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        mock_sftp = MagicMock()
        file_stat = MagicMock()
        file_stat.st_size = 0
        mock_sftp.stat.return_value = file_stat
        mock_client.open_sftp.return_value = mock_sftp

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_false_on_sftp_error(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client
        mock_client.open_sftp.side_effect = OSError("SFTP not available")

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is False
        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_false_on_general_exception(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client
        mock_client.open_sftp.side_effect = RuntimeError("connection reset")

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_closes_sftp_in_finally_block(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.return_value = mock_client

        mock_sftp = MagicMock()
        mock_sftp.stat.side_effect = OSError("not found")
        mock_client.open_sftp.return_value = mock_sftp

        await check_job_completion(ssh_manager, "task-001")

        mock_sftp.close.assert_called_once()
        ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_released_on_exception(
        self, ssh_manager: MagicMock, mock_client: MagicMock
    ) -> None:
        ssh_manager.acquire_connection.side_effect = RuntimeError("connect fail")

        result = await check_job_completion(ssh_manager, "task-001")

        assert result is False
        ssh_manager.release_connection.assert_not_called()


# ---------------------------------------------------------------------------
# poll_job_status
# ---------------------------------------------------------------------------


class TestPollJobStatus:
    """Tests for poll_job_status(ssh_manager, hpc_job_id)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_running_when_squeue_has_running(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value="RUNNING my-job",
        ) as mock_squeue:
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "RUNNING"
        mock_squeue.assert_called_once_with(ssh_manager, "slurm-12345")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_pending_when_squeue_has_pending(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value="PENDING my-job",
        ) as mock_squeue:
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "PENDING"
        mock_squeue.assert_called_once_with(ssh_manager, "slurm-12345")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_completed_when_job_gone_and_output_exists(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "nfm_db.services.hpc_job_monitor.check_job_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_check:
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "COMPLETED"
        mock_check.assert_called_once_with(ssh_manager, "12345")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_failed_when_job_gone_and_no_output(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "nfm_db.services.hpc_job_monitor.check_job_completion",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_check:
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "FAILED"
        mock_check.assert_called_once_with(ssh_manager, "12345")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_failed_on_unknown_squeue_output(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value="SUSPENDED my-job",
        ):
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "FAILED"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_failed_on_general_exception(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network failure"),
        ):
            result = await poll_job_status(ssh_manager, "slurm-12345")

        assert result == "FAILED"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_extracts_task_id_from_hpc_job_id(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.execute_squeue",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "nfm_db.services.hpc_job_monitor.check_job_completion",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_check:
            await poll_job_status(ssh_manager, "slurm-98765")

        mock_check.assert_called_once_with(ssh_manager, "98765")


# ---------------------------------------------------------------------------
# update_job_status
# ---------------------------------------------------------------------------


class TestUpdateJobStatus:
    """Tests for update_job_status(ssh_manager, task_id, hpc_job_id)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_update_of_hpc_and_verification_jobs(
        self, ssh_manager: MagicMock, mock_db_session: AsyncMock
    ) -> None:
        task_id = "a0000000-0000-0000-0000-000000000001"
        hpc_job_id = "slurm-12345"

        hpc_job = MagicMock()
        verification_job = MagicMock()

        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: hpc_job)
        )
        mock_db_session.get = AsyncMock(return_value=verification_job)

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            "nfm_db.services.hpc_job_monitor.poll_job_status",
            new_callable=AsyncMock,
            return_value="RUNNING",
        ), patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            await update_job_status(ssh_manager, task_id, hpc_job_id)

        assert hpc_job.status == HpcJobStatus.RUNNING
        assert verification_job.status == "RUNNING"
        mock_db_session.commit.call_count == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_handles_hpc_job_not_found_in_db(
        self, ssh_manager: MagicMock, mock_db_session: AsyncMock
    ) -> None:
        task_id = "a0000000-0000-0000-0000-000000000001"
        hpc_job_id = "slurm-99999"

        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )
        verification_job = MagicMock()
        mock_db_session.get = AsyncMock(return_value=verification_job)

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            "nfm_db.services.hpc_job_monitor.poll_job_status",
            new_callable=AsyncMock,
            return_value="COMPLETED",
        ), patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            await update_job_status(ssh_manager, task_id, hpc_job_id)

        assert verification_job.status == "COMPLETED"
        mock_db_session.commit.call_count == 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_database_error_triggers_rollback(
        self, ssh_manager: MagicMock, mock_db_session: AsyncMock
    ) -> None:
        task_id = "a0000000-0000-0000-0000-000000000001"
        hpc_job_id = "slurm-12345"

        mock_db_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            "nfm_db.services.hpc_job_monitor.poll_job_status",
            new_callable=AsyncMock,
            return_value="FAILED",
        ), patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ), pytest.raises(RuntimeError, match="DB error"):
            await update_job_status(ssh_manager, task_id, hpc_job_id)

        mock_db_session.rollback.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_general_exception_is_raised(
        self, ssh_manager: MagicMock
    ) -> None:
        task_id = "a0000000-0000-0000-0000-000000000001"
        hpc_job_id = "slurm-12345"

        with patch(
            "nfm_db.services.hpc_job_monitor.poll_job_status",
            new_callable=AsyncMock,
            side_effect=RuntimeError("poll failed"),
        ), pytest.raises(RuntimeError, match="poll failed"):
            await update_job_status(ssh_manager, task_id, hpc_job_id)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_db_generator_is_exhausted_after_use(
        self, ssh_manager: MagicMock, mock_db_session: AsyncMock
    ) -> None:
        task_id = "a0000000-0000-0000-0000-000000000001"
        hpc_job_id = "slurm-12345"

        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )
        mock_db_session.get = AsyncMock(return_value=None)

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            "nfm_db.services.hpc_job_monitor.poll_job_status",
            new_callable=AsyncMock,
            return_value="RUNNING",
        ), patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            await update_job_status(ssh_manager, task_id, hpc_job_id)

        assert db_gen.__anext__.call_count >= 2


# ---------------------------------------------------------------------------
# get_active_jobs
# ---------------------------------------------------------------------------


class TestGetActiveJobs:
    """Tests for get_active_jobs()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_active_jobs_list(
        self, mock_db_session: AsyncMock, sample_hpc_job: HpcJob
    ) -> None:
        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [sample_hpc_job])
            )
        )

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            result = await get_active_jobs()

        assert len(result) == 1
        assert result[0].hpc_job_id == "slurm-12345"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_active_jobs(
        self, mock_db_session: AsyncMock
    ) -> None:
        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        )

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            result = await get_active_jobs()

        assert result == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_database_error_is_propagated(
        self, mock_db_session: AsyncMock
    ) -> None:
        mock_db_session.execute = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ), pytest.raises(RuntimeError, match="connection lost"):
            await get_active_jobs()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_db_generator_is_exhausted(
        self, mock_db_session: AsyncMock
    ) -> None:
        mock_db_session.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        )

        db_gen = _mock_db_gen(mock_db_session)

        with patch(
            _DB_PATCH_TARGET,
            return_value=db_gen,
        ):
            await get_active_jobs()

        assert db_gen.__anext__.call_count >= 2


# ---------------------------------------------------------------------------
# sync_all_active_jobs
# ---------------------------------------------------------------------------


class TestSyncAllActiveJobs:
    """Tests for sync_all_active_jobs(ssh_manager)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_syncs_all_active_jobs(
        self,
        ssh_manager: MagicMock,
        sample_hpc_job: HpcJob,
    ) -> None:
        job_two = HpcJob(
            hpc_job_id="slurm-67890",
            hpc_cluster="test-cluster",
            verification_job_id="a0000000-0000-0000-0000-000000000002",
        )

        with patch(
            "nfm_db.services.hpc_job_monitor.get_active_jobs",
            new_callable=AsyncMock,
            return_value=[sample_hpc_job, job_two],
        ), patch(
            "nfm_db.services.hpc_job_monitor.update_job_status",
            new_callable=AsyncMock,
        ) as mock_update:
            await sync_all_active_jobs(ssh_manager)

        assert mock_update.call_count == 2
        mock_update.assert_any_call(
            ssh_manager,
            str(sample_hpc_job.verification_job_id),
            sample_hpc_job.hpc_job_id,
        )
        mock_update.assert_any_call(
            ssh_manager,
            str(job_two.verification_job_id),
            job_two.hpc_job_id,
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_continues_on_per_job_error(
        self,
        ssh_manager: MagicMock,
        sample_hpc_job: HpcJob,
    ) -> None:
        job_two = HpcJob(
            hpc_job_id="slurm-67890",
            hpc_cluster="test-cluster",
            verification_job_id="a0000000-0000-0000-0000-000000000002",
        )

        with patch(
            "nfm_db.services.hpc_job_monitor.get_active_jobs",
            new_callable=AsyncMock,
            return_value=[sample_hpc_job, job_two],
        ), patch(
            "nfm_db.services.hpc_job_monitor.update_job_status",
            new_callable=AsyncMock,
            side_effect=[None, RuntimeError("per-job failure")],
        ):
            await sync_all_active_jobs(ssh_manager)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_handles_empty_job_list(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.get_active_jobs",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "nfm_db.services.hpc_job_monitor.update_job_status",
            new_callable=AsyncMock,
        ) as mock_update:
            await sync_all_active_jobs(ssh_manager)

        mock_update.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_handles_general_error_in_get_active_jobs(
        self, ssh_manager: MagicMock
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.get_active_jobs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection failed"),
        ):
            await sync_all_active_jobs(ssh_manager)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_logs_info_after_sync(
        self,
        ssh_manager: MagicMock,
        sample_hpc_job: HpcJob,
    ) -> None:
        with patch(
            "nfm_db.services.hpc_job_monitor.get_active_jobs",
            new_callable=AsyncMock,
            return_value=[sample_hpc_job],
        ), patch(
            "nfm_db.services.hpc_job_monitor.update_job_status",
            new_callable=AsyncMock,
        ), patch(
            "nfm_db.services.hpc_job_monitor.logger"
        ) as mock_logger:
            await sync_all_active_jobs(ssh_manager)

            mock_logger.info.assert_called_with("Synced 1 active jobs")
