"""Comprehensive unit tests for HPC file transfer operations.

Covers upload, download, checksum verification, resume transfer,
retry logic, and object storage integration.
"""

import hashlib
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.hpc_file_transfer import (
    create_task_directory,
    download_file,
    download_results,
    get_remote_checksum,
    get_remote_file_position,
    save_metadata,
    save_to_object_storage,
    upload_file,
    upload_file_with_resume,
    upload_file_with_retry,
    upload_files,
    validate_remote_path,
    verify_checksum,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ssh_manager() -> MagicMock:
    """Create a mock SSHConnectionManager."""
    manager = MagicMock()
    client = MagicMock()
    manager.acquire_connection.return_value = client
    manager.release_connection = MagicMock()
    return manager


@pytest.fixture
def mock_sftp() -> MagicMock:
    """Create a mock SFTP client."""
    return MagicMock()


@pytest.fixture
def sample_task_id() -> str:
    """Return a sample task identifier."""
    return "task-001"


@pytest.fixture
def sample_local_file() -> str:
    """Return a sample local file path."""
    return "/local/path/input.dat"


@pytest.fixture
def sample_remote_file() -> str:
    """Return a sample remote file path."""
    return "/scratch/nfm-md/task-001/input.dat"


def _make_ssh_client_with_sftp(mock_sftp_client: MagicMock) -> MagicMock:
    """Attach an SFTP mock to an SSH client mock."""
    client = MagicMock()
    client.open_sftp.return_value = mock_sftp_client
    return client


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------


class TestUploadFile:
    """Tests for upload_file()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_success(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Successful upload returns True and cleans up resources."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file(
                mock_ssh_manager, sample_task_id, sample_local_file, sample_remote_file
            )

        assert result is True
        mock_sftp.put.assert_called_once_with(sample_local_file, sample_remote_file)
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_exception_returns_false(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Exception during upload returns False and releases connection."""
        client = MagicMock()
        client.open_sftp.side_effect = RuntimeError("SFTP failure")
        mock_ssh_manager.acquire_connection.return_value = client

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file(
                mock_ssh_manager, sample_task_id, sample_local_file, sample_remote_file
            )

        assert result is False
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_closes_sftp_on_put_error(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """SFTP client is closed even when put() raises."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        mock_sftp.put.side_effect = OSError("disk full")

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file(
                mock_ssh_manager, sample_task_id, sample_local_file, sample_remote_file
            )

        assert result is False
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_releases_connection_when_acquire_fails(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Connection release is skipped when acquire_connection raises."""
        mock_ssh_manager.acquire_connection.side_effect = RuntimeError("pool exhausted")

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file(
                mock_ssh_manager, sample_task_id, sample_local_file, sample_remote_file
            )

        assert result is False
        mock_ssh_manager.release_connection.assert_not_called()


# ---------------------------------------------------------------------------
# upload_files
# ---------------------------------------------------------------------------


class TestUploadFiles:
    """Tests for upload_files()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_files_all_succeed(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """All files upload successfully."""
        files = [
            ("/local/a.dat", "/remote/a.dat"),
            ("/local/b.dat", "/remote/b.dat"),
        ]

        with patch(
            "nfm_db.services.hpc_file_transfer.upload_file",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_upload:
            result = await upload_files(mock_ssh_manager, sample_task_id, files)

        assert result == {"/local/a.dat": True, "/local/b.dat": True}
        assert mock_upload.call_count == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_files_mixed_results(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Mixed success/failure returns per-file status."""
        files = [
            ("/local/a.dat", "/remote/a.dat"),
            ("/local/b.dat", "/remote/b.dat"),
            ("/local/c.dat", "/remote/c.dat"),
        ]

        call_count = 0

        async def _fake_upload(ssh_mgr, tid, local, remote):
            nonlocal call_count
            call_count += 1
            return call_count % 2 == 1

        with patch(
            "nfm_db.services.hpc_file_transfer.upload_file",
            side_effect=_fake_upload,
        ):
            result = await upload_files(mock_ssh_manager, sample_task_id, files)

        assert result == {
            "/local/a.dat": True,
            "/local/b.dat": False,
            "/local/c.dat": True,
        }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_files_empty_list(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Empty file list returns empty result dict."""
        result = await upload_files(mock_ssh_manager, sample_task_id, [])
        assert result == {}


# ---------------------------------------------------------------------------
# create_task_directory
# ---------------------------------------------------------------------------


class TestCreateTaskDirectory:
    """Tests for create_task_directory()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_directory_success(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Directory created and resources cleaned up."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client

        await create_task_directory(mock_ssh_manager, sample_task_id)

        expected_dir = f"$SCRATCH/nfm-md/{sample_task_id}"
        mock_sftp.mkdir.assert_called_once_with(expected_dir)
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_directory_already_exists(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
    ) -> None:
        """IOError from mkdir (dir exists) is silently ignored."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        mock_sftp.mkdir.side_effect = OSError("File exists")

        await create_task_directory(mock_ssh_manager, sample_task_id)

        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_directory_exception_releases_client(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Non-IOError exceptions propagate but still release the connection."""
        client = MagicMock()
        client.open_sftp.side_effect = RuntimeError("connection reset")
        mock_ssh_manager.acquire_connection.return_value = client

        with pytest.raises(RuntimeError, match="connection reset"):
            await create_task_directory(mock_ssh_manager, sample_task_id)

        mock_ssh_manager.release_connection.assert_called_once_with(client)


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


class TestDownloadFile:
    """Tests for download_file()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_file_success(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Successful download returns local path."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        local_path = "/tmp/results/task-001/lammps.out"

        with patch("nfm_db.services.hpc_file_transfer.os.makedirs") as mock_makedirs:
            result = await download_file(
                mock_ssh_manager, sample_task_id, sample_remote_file, local_path
            )

        assert result == local_path
        mock_makedirs.assert_called_once_with("/tmp/results/task-001", exist_ok=True)
        mock_sftp.get.assert_called_once_with(sample_remote_file, local_path)
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_file_sftp_error_returns_none(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """SFTP get failure returns None."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        mock_sftp.get.side_effect = OSError("No such file")
        local_path = "/tmp/results/task-001/lammps.out"

        with patch("nfm_db.services.hpc_file_transfer.os.makedirs"):
            result = await download_file(
                mock_ssh_manager, sample_task_id, sample_remote_file, local_path
            )

        assert result is None
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_file_exception_returns_none(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Unexpected exception returns None."""
        mock_ssh_manager.acquire_connection.side_effect = RuntimeError("broken")

        result = await download_file(
            mock_ssh_manager, sample_task_id, "/remote/file", "/local/file"
        )

        assert result is None


# ---------------------------------------------------------------------------
# download_results
# ---------------------------------------------------------------------------


class TestDownloadResults:
    """Tests for download_results()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_all_files(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """All three result files are downloaded."""
        with patch(
            "nfm_db.services.hpc_file_transfer.download_file",
            new_callable=AsyncMock,
            return_value="/tmp/results/task-001/file",
        ) as mock_dl:
            result = await download_results(mock_ssh_manager, sample_task_id)

        assert len(result) == 3
        assert "lammps.out" in result
        assert "log.lammps" in result
        assert "energy_curve.dat" in result
        assert mock_dl.call_count == 3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_partial_files(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """Only successfully downloaded files appear in result."""
        call_idx = 0

        async def _fake_download(ssh, tid, remote, local):
            nonlocal call_idx
            call_idx += 1
            return local if call_idx % 2 == 1 else None

        with patch(
            "nfm_db.services.hpc_file_transfer.download_file",
            side_effect=_fake_download,
        ):
            result = await download_results(mock_ssh_manager, sample_task_id)

        assert len(result) == 2
        assert "lammps.out" in result
        assert "energy_curve.dat" in result

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_no_files(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """All downloads fail returns empty dict."""
        with patch(
            "nfm_db.services.hpc_file_transfer.download_file",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await download_results(mock_ssh_manager, sample_task_id)

        assert result == {}


# ---------------------------------------------------------------------------
# verify_checksum
# ---------------------------------------------------------------------------


class TestVerifyChecksum:
    """Tests for verify_checksum()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_matching_checksum(
        self,
        tmp_path: Any,
    ) -> None:
        """Matching checksum returns True."""
        test_file = tmp_path / "test.dat"
        content = b"hello world"
        test_file.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        result = await verify_checksum(str(test_file), expected)
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_mismatching_checksum(
        self,
        tmp_path: Any,
    ) -> None:
        """Mismatching checksum returns False."""
        test_file = tmp_path / "test.dat"
        test_file.write_bytes(b"hello world")

        result = await verify_checksum(str(test_file), "0" * 64)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_file_not_found(self) -> None:
        """Non-existent file returns False."""
        result = await verify_checksum("/nonexistent/file.dat", "abc")
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_file_checksum(
        self,
        tmp_path: Any,
    ) -> None:
        """Empty file produces correct SHA256 hash."""
        test_file = tmp_path / "empty.dat"
        test_file.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()

        result = await verify_checksum(str(test_file), expected)
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_large_file_checksum(
        self,
        tmp_path: Any,
    ) -> None:
        """Large file (>4096 bytes) reads in chunks correctly."""
        test_file = tmp_path / "large.dat"
        content = os.urandom(10000)
        test_file.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        result = await verify_checksum(str(test_file), expected)
        assert result is True


# ---------------------------------------------------------------------------
# get_remote_checksum
# ---------------------------------------------------------------------------


class TestGetRemoteChecksum:
    """Tests for get_remote_checksum()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_success(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Successful checksum retrieval returns SHA256 hash."""
        client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b"abc123def456  /remote/file\n"
        client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())
        mock_ssh_manager.acquire_connection.return_value = client

        result = await get_remote_checksum(mock_ssh_manager, sample_task_id, sample_remote_file)

        assert result == "abc123def456"
        client.exec_command.assert_called_once_with(f"sha256sum {sample_remote_file}")
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_nonzero_exit_status(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Non-zero exit status returns None."""
        client = MagicMock()
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"file not found\n"
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stdout.read.return_value = b""
        client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
        mock_ssh_manager.acquire_connection.return_value = client

        result = await get_remote_checksum(mock_ssh_manager, sample_task_id, sample_remote_file)

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exception_returns_none(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Exception during SSH command returns None."""
        client = MagicMock()
        client.exec_command.side_effect = RuntimeError("connection lost")
        mock_ssh_manager.acquire_connection.return_value = client

        result = await get_remote_checksum(mock_ssh_manager, sample_task_id, sample_remote_file)

        assert result is None
        mock_ssh_manager.release_connection.assert_called_once_with(client)


# ---------------------------------------------------------------------------
# save_to_object_storage
# ---------------------------------------------------------------------------


class TestSaveToObjectStorage:
    """Tests for save_to_object_storage()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_generates_storage_urls(
        self,
        sample_task_id: str,
    ) -> None:
        """Generates correct storage URLs for each file."""
        downloaded_files: dict[str, str] = {
            "lammps.out": "/tmp/results/task-001/lammps.out",
            "log.lammps": "/tmp/results/task-001/log.lammps",
        }

        result = await save_to_object_storage(sample_task_id, downloaded_files)

        assert result == {
            "lammps.out": f"https://storage.example.com/{sample_task_id}/lammps.out",
            "log.lammps": f"https://storage.example.com/{sample_task_id}/log.lammps",
        }

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_dict(self) -> None:
        """Empty downloaded files dict returns empty URLs dict."""
        result = await save_to_object_storage("task-002", {})
        assert result == {}


# ---------------------------------------------------------------------------
# save_metadata
# ---------------------------------------------------------------------------


class TestSaveMetadata:
    """Tests for save_metadata()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_logs_metadata(
        self,
        sample_task_id: str,
    ) -> None:
        """Metadata is logged via the logger."""
        metadata: dict[str, dict[str, Any]] = {
            "lammps.out": {"size": 1024, "checksum": "abc123"},
        }

        mock_db = AsyncMock()
        mock_gen = _make_async_gen([mock_db])

        with patch(
            "nfm_db.services.hpc_file_transfer.get_db",
            return_value=mock_gen,
        ):
            await save_metadata(sample_task_id, metadata)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_handles_db_cleanup(
        self,
        sample_task_id: str,
    ) -> None:
        """Database generator is exhausted (cleanup runs)."""
        metadata: dict[str, dict[str, Any]] = {"file": {}}
        mock_db = AsyncMock()
        mock_gen = _make_async_gen([mock_db])

        with patch(
            "nfm_db.services.hpc_file_transfer.get_db",
            return_value=mock_gen,
        ):
            await save_metadata(sample_task_id, metadata)


def _make_async_gen(values: list) -> Any:
    """Create a simple async generator that yields values then stops."""

    async def _gen() -> Any:
        for v in values:
            yield v

    return _gen()


# ---------------------------------------------------------------------------
# upload_file_with_retry
# ---------------------------------------------------------------------------


class TestUploadFileWithRetry:
    """Tests for upload_file_with_retry()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_success_on_first_try(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """First attempt succeeds without sleeping."""
        with (
            patch(
                "nfm_db.services.hpc_file_transfer.upload_file",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_upload,
            patch(
                "nfm_db.services.hpc_file_transfer.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            result = await upload_file_with_retry(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                max_retries=3,
            )

        assert result is True
        mock_upload.assert_called_once()
        mock_sleep.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_retries_then_succeeds(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Fails first two attempts, succeeds on third."""
        call_count = 0

        async def _fake_upload(ssh, tid, local, remote):
            nonlocal call_count
            call_count += 1
            return call_count == 3

        with (
            patch(
                "nfm_db.services.hpc_file_transfer.upload_file",
                side_effect=_fake_upload,
            ),
            patch(
                "nfm_db.services.hpc_file_transfer.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            result = await upload_file_with_retry(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                max_retries=3,
            )

        assert result is True
        assert call_count == 3
        assert mock_sleep.call_count == 2
        # Exponential backoff: 2^0=1, 2^1=2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_all_retries_fail(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """All retries exhausted returns False."""
        with (
            patch(
                "nfm_db.services.hpc_file_transfer.upload_file",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_upload,
            patch(
                "nfm_db.services.hpc_file_transfer.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            result = await upload_file_with_retry(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                max_retries=3,
            )

        assert result is False
        assert mock_upload.call_count == 3
        assert mock_sleep.call_count == 3

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exception_in_upload_retries(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Exceptions during upload trigger retry, last attempt returns False."""
        with (
            patch(
                "nfm_db.services.hpc_file_transfer.upload_file",
                new_callable=AsyncMock,
                side_effect=RuntimeError("connection reset"),
            ),
            patch(
                "nfm_db.services.hpc_file_transfer.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await upload_file_with_retry(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                max_retries=2,
            )

        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_single_retry_allowed(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """max_retries=1 tries exactly once."""
        with (
            patch(
                "nfm_db.services.hpc_file_transfer.upload_file",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_upload,
            patch(
                "nfm_db.services.hpc_file_transfer.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await upload_file_with_retry(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                max_retries=1,
            )

        assert result is False
        assert mock_upload.call_count == 1


# ---------------------------------------------------------------------------
# upload_file_with_resume
# ---------------------------------------------------------------------------


class TestUploadFileWithResume:
    """Tests for upload_file_with_resume()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_resume_from_position(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
        tmp_path: Any,
    ) -> None:
        """Resumes upload from the given byte position."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client

        # Create a real local file for the open() call
        real_local_file = tmp_path / "resume_test.dat"
        real_local_file.write_bytes(b"A" * 100 + b"B" * 50)

        # Setup the SFTP file context manager mock
        mock_remote_f = MagicMock()
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=mock_remote_f)
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file_with_resume(
                mock_ssh_manager,
                sample_task_id,
                str(real_local_file),
                sample_remote_file,
                resume_position=100,
            )

        assert result is True
        mock_sftp.file.assert_called_once_with(sample_remote_file, "ab")
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_resume_from_position_zero(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
        tmp_path: Any,
    ) -> None:
        """Resume from position 0 uploads entire file."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client

        real_local_file = tmp_path / "full_test.dat"
        content = b"X" * 65536 * 2 + b"Y"
        real_local_file.write_bytes(content)

        mock_remote_f = MagicMock()
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=mock_remote_f)
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file_with_resume(
                mock_ssh_manager,
                sample_task_id,
                str(real_local_file),
                sample_remote_file,
                resume_position=0,
            )

        assert result is True
        mock_sftp.file.assert_called_once_with(sample_remote_file, "ab")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exception_returns_false(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
        sample_remote_file: str,
    ) -> None:
        """Exception during resume upload returns False."""
        client = MagicMock()
        client.open_sftp.side_effect = RuntimeError("connection lost")
        mock_ssh_manager.acquire_connection.return_value = client

        with patch(
            "nfm_db.services.hpc_file_transfer.create_task_directory",
            new_callable=AsyncMock,
        ):
            result = await upload_file_with_resume(
                mock_ssh_manager,
                sample_task_id,
                sample_local_file,
                sample_remote_file,
                resume_position=0,
            )

        assert result is False
        mock_ssh_manager.release_connection.assert_called_once_with(client)


# ---------------------------------------------------------------------------
# get_remote_file_position
# ---------------------------------------------------------------------------


class TestGetRemoteFilePosition:
    """Tests for get_remote_file_position()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_file_exists_returns_size(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Existing file returns its size."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        mock_stat = MagicMock()
        mock_stat.st_size = 1048576
        mock_sftp.stat.return_value = mock_stat

        result = await get_remote_file_position(
            mock_ssh_manager, sample_task_id, sample_remote_file
        )

        assert result == 1048576
        mock_sftp.stat.assert_called_once_with(sample_remote_file)
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_file_not_found_returns_zero(
        self,
        mock_ssh_manager: MagicMock,
        mock_sftp: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Non-existent file returns 0."""
        client = _make_ssh_client_with_sftp(mock_sftp)
        mock_ssh_manager.acquire_connection.return_value = client
        mock_sftp.stat.side_effect = OSError("No such file")

        result = await get_remote_file_position(
            mock_ssh_manager, sample_task_id, sample_remote_file
        )

        assert result == 0
        mock_sftp.close.assert_called_once()
        mock_ssh_manager.release_connection.assert_called_once_with(client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exception_returns_zero(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_remote_file: str,
    ) -> None:
        """Unexpected exception returns 0."""
        client = MagicMock()
        client.open_sftp.side_effect = RuntimeError("broken")
        mock_ssh_manager.acquire_connection.return_value = client

        result = await get_remote_file_position(
            mock_ssh_manager, sample_task_id, sample_remote_file
        )

        assert result == 0
        mock_ssh_manager.release_connection.assert_called_once_with(client)


# ---------------------------------------------------------------------------
# validate_remote_path() Tests (C2: Remote Checksum Command Injection Fix)
# ---------------------------------------------------------------------------


class TestValidateRemotePath:
    """Test suite for validate_remote_path function."""

    @pytest.mark.unit
    def test_simple_alphanumeric_passes(self) -> None:
        """Simple alphanumeric path should pass."""
        result = validate_remote_path("file123")
        assert result == "file123"

    @pytest.mark.unit
    def test_path_with_slashes_passes(self) -> None:
        """Path with forward slashes should pass."""
        result = validate_remote_path("/home/user/data/file.txt")
        assert result == "/home/user/data/file.txt"

    @pytest.mark.unit
    def test_relative_path_with_dots_passes(self) -> None:
        """Relative path with dots should pass."""
        result = validate_remote_path("../data/input.dat")
        assert result == "../data/input.dat"

    @pytest.mark.unit
    def test_filename_with_dashes_passes(self) -> None:
        """Filename with dashes should pass."""
        result = validate_remote_path("checkpoint-001.dat")
        assert result == "checkpoint-001.dat"

    @pytest.mark.unit
    def test_filename_with_underscores_passes(self) -> None:
        """Filename with underscores should pass."""
        result = validate_remote_path("results_final.csv")
        assert result == "results_final.csv"

    @pytest.mark.unit
    def test_windows_style_path_with_colon_passes(self) -> None:
        """Path with colon (for port or Windows drive) should pass."""
        result = validate_remote_path("server:8080/path")
        assert result == "server:8080/path"

    @pytest.mark.unit
    def test_complex_valid_path_passes(self) -> None:
        """Complex path with all allowed characters should pass."""
        result = validate_remote_path("/home/user_name/data-123/file_v2.txt:8080")
        assert result == "/home/user_name/data-123/file_v2.txt:8080"

    @pytest.mark.unit
    def test_command_substitution_rejected(self) -> None:
        """Command substitution $(whoami) should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("$(whoami)")

    @pytest.mark.unit
    def test_backtick_substitution_rejected(self) -> None:
        """Backtick command substitution should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("`whoami`")

    @pytest.mark.unit
    def test_semicolon_command_injection_rejected(self) -> None:
        """Semicolon command injection should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file.txt; rm -rf /")

    @pytest.mark.unit
    def test_pipe_operator_rejected(self) -> None:
        """Pipe operator should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file | cat /etc/passwd")

    @pytest.mark.unit
    def test_ampersand_rejected(self) -> None:
        """Ampersand should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file&whoami")

    @pytest.mark.unit
    def test_newline_rejected(self) -> None:
        """Newline should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file.txt\nwhoami")

    @pytest.mark.unit
    def test_null_byte_rejected(self) -> None:
        """Null byte should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file.txt\x00whoami")

    @pytest.mark.unit
    def test_space_rejected(self) -> None:
        """Space character should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file.txt whoami")

    @pytest.mark.unit
    def test_empty_string_rejected(self) -> None:
        """Empty string should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("")

    @pytest.mark.unit
    def test_special_chars_rejected(self) -> None:
        """Other special characters should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file@host.com")

    @pytest.mark.unit
    def test_exclamation_mark_rejected(self) -> None:
        """Exclamation mark should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file!.txt")

    @pytest.mark.unit
    def test_hash_rejected(self) -> None:
        """Hash character should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("file#123.txt")

    @pytest.mark.unit
    def test_dollar_sign_rejected(self) -> None:
        """Dollar sign should be rejected."""
        with pytest.raises(ValueError, match="Unsafe remote path"):
            validate_remote_path("$HOME/file.txt")


# ---------------------------------------------------------------------------
# Injection rejection in upload/download/get_remote_checksum
# ---------------------------------------------------------------------------


class TestPathValidationInTransferFunctions:
    """Verify that upload/download/checksum functions reject injected paths."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_rejects_command_substitution(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
    ) -> None:
        """upload_file returns False when remote path contains injection."""
        result = await upload_file(mock_ssh_manager, sample_task_id, sample_local_file, "$(whoami)")
        assert result is False
        mock_ssh_manager.acquire_connection.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_rejects_semicolon_injection(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
    ) -> None:
        """upload_file returns False for semicolon injection."""
        result = await upload_file(
            mock_ssh_manager,
            sample_task_id,
            sample_local_file,
            "file.txt; rm -rf /",
        )
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_rejects_pipe_injection(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
        sample_local_file: str,
    ) -> None:
        """upload_file returns False for pipe injection."""
        result = await upload_file(
            mock_ssh_manager,
            sample_task_id,
            sample_local_file,
            "file | cat /etc/passwd",
        )
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_rejects_command_substitution(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """download_file returns None when remote path contains injection."""
        result = await download_file(
            mock_ssh_manager, sample_task_id, "$(whoami)", "/local/out.dat"
        )
        assert result is None
        mock_ssh_manager.acquire_connection.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_rejects_backtick_injection(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """download_file returns None for backtick injection."""
        result = await download_file(mock_ssh_manager, sample_task_id, "`whoami`", "/local/out.dat")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_remote_checksum_rejects_injection(
        self,
        mock_ssh_manager: MagicMock,
        sample_task_id: str,
    ) -> None:
        """get_remote_checksum returns None when path contains injection."""
        result = await get_remote_checksum(mock_ssh_manager, sample_task_id, "file; rm -rf /")
        assert result is None
        mock_ssh_manager.acquire_connection.assert_not_called()
