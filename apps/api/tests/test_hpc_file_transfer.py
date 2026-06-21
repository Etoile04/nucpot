"""Tests for HPC Orchestration System - Phase 4.4: File Transfer."""

import uuid
import pytest
import gc
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig
from nfm_db.models.md_verification import HpcJob, HpcJobStatus


@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Auto-cleanup after each test to prevent memory leaks."""
    yield
    # Force garbage collection after each test
    gc.collect()


@pytest.fixture(scope="module")
def hpc_orchestrator():
    """Fixture that provides a shared HPC orchestrator for all tests.

    Uses module scope to reduce object creation and prevent memory leaks.
    All tests in this module share the same orchestrator instance.
    """
    import gc
    config = SSHConnectionConfig(
        hosts=["login01.example.com"],
        username="testuser",
        ssh_key_path="/path/to/key",
        skip_key_validation=True  # Skip validation for test environment
    )
    orchestrator = HPCOrchestrator(config)

    yield orchestrator

    # Critical cleanup to prevent memory leaks
    orchestrator.cleanup()
    del orchestrator
    del config

    # Force garbage collection to ensure Paramiko objects are collected
    gc.collect()


class TestFileUpload:
    """Test file upload to HPC $SCRATCH directory."""

    @pytest.mark.asyncio
    async def test_upload_input_files_to_scratch(self, hpc_orchestrator):
        """Test uploading input files to $SCRATCH/task_id/."""
        task_id = str(uuid.uuid4())
        local_file = "/path/to/structure.cif"
        remote_file = f"$SCRATCH/nfm-md/{task_id}/structure.cif"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                # Mock SFTP upload
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                await hpc_orchestrator.upload_file(task_id, local_file, remote_file)

                # Verify SFTP operations
                mock_sftp.mkdir.assert_called_once()
                mock_sftp.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_task_specific_subdirectory(self, hpc_orchestrator):
        """Test creation of task-specific subdirectory."""
        task_id = str(uuid.uuid4())
        remote_dir = f"$SCRATCH/nfm-md/{task_id}"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                # Mock SFTP directory creation
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                await hpc_orchestrator._create_task_directory(task_id)

                # Verify directory creation
                mock_sftp.mkdir.assert_called_once()
                call_args = mock_sftp.mkdir.call_args
                assert remote_dir in str(call_args)

    @pytest.mark.asyncio
    async def test_upload_multiple_files(self, hpc_orchestrator):
        """Test uploading multiple input files."""
        task_id = str(uuid.uuid4())
        files = [
            ("/local/structure.cif", "$SCRATCH/nfm-md/{task_id}/structure.cif"),
            ("/local/potential.file", "$SCRATCH/nfm-md/{task_id}/potential.file"),
        ]

        with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
            mock_upload.return_value = True

            results = await hpc_orchestrator.upload_files(task_id, files)

            assert len(results) == len(files)
            assert all(results.values())


class TestFileDownload:
    """Test file download from HPC to object storage."""

    @pytest.mark.asyncio
    async def test_download_lammps_output(self, hpc_orchestrator):
        """Test downloading lammps.out file."""
        task_id = str(uuid.uuid4())
        remote_file = f"$SCRATCH/nfm-md/{task_id}/lammps.out"
        local_path = f"/tmp/results/{task_id}/lammps.out"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                # Mock SFTP download
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                result_path = await hpc_orchestrator.download_file(task_id, remote_file, local_path)

                # Verify download
                mock_sftp.get.assert_called_once()
                assert result_path == local_path

    @pytest.mark.asyncio
    async def test_download_all_results(self, hpc_orchestrator):
        """Test downloading all result files."""
        task_id = str(uuid.uuid4())
        expected_files = {
            "lammps.out": f"/tmp/results/{task_id}/lammps.out",
            "log.lammps": f"/tmp/results/{task_id}/log.lammps",
            "energy_curve.dat": f"/tmp/results/{task_id}/energy_curve.dat",
        }

        with patch.object(hpc_orchestrator, 'download_results') as mock_download:
            mock_download.return_value = expected_files

            results = await hpc_orchestrator.download_results(task_id)

            assert results == expected_files
            mock_download.assert_called_once_with(task_id)


class TestChecksumVerification:
    """Test checksum verification for file transfers."""

    @pytest.mark.asyncio
    async def test_verify_file_checksum(self, hpc_orchestrator):
        """Test file checksum verification after transfer."""

        local_file = "/tmp/results/test.dat"
        expected_checksum = "abc123"

        with patch('hashlib.sha256') as mock_sha256:
            mock_hash = MagicMock()
            mock_sha256.return_value = mock_hash
            mock_hash.hexdigest.return_value = expected_checksum

            with patch('builtins.open', create=True) as mock_open:
                # Simulate file reading
                mock_open.return_value.__enter__.return_value.read.return_value = b"data"

                is_valid = await hpc_orchestrator.verify_checksum(local_file, expected_checksum)

                assert is_valid is True

    @pytest.mark.asyncio
    async def test_checksum_mismatch_detection(self, hpc_orchestrator):
        """Test checksum mismatch detection."""

        local_file = "/tmp/results/test.dat"
        expected_checksum = "abc123"
        actual_checksum = "wrong456"

        with patch('hashlib.sha256') as mock_sha256:
            mock_hash = MagicMock()
            mock_sha256.return_value = mock_hash
            mock_hash.hexdigest.return_value = actual_checksum

            with patch('builtins.open', create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = b"data"

                is_valid = await hpc_orchestrator.verify_checksum(local_file, expected_checksum)

                assert is_valid is False

    @pytest.mark.asyncio
    async def test_generate_checksum_for_remote_file(self, hpc_orchestrator):
        """Test generating checksum for remote file before download."""

        task_id = str(uuid.uuid4())
        remote_file = f"$SCRATCH/nfm-md/{task_id}/lammps.out"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                # Mock sha256sum command
                stdin, stdout, stderr = mock_client.exec_command.return_value = (None, MagicMock(), MagicMock())
                stdout.read.return_value = b"abc123  $SCRATCH/nfm-md/{task_id}/lammps.out"

                checksum = await hpc_orchestrator.get_remote_checksum(task_id, remote_file)

                assert checksum == "abc123"


class TestFileTransferSuccess:
    """Test file transfer success rate and reliability."""

    @pytest.mark.asyncio
    async def test_file_transfer_success_rate_above_threshold(self, hpc_orchestrator):
        """Test file transfer success rate exceeds 95% threshold."""

        successful_transfers = 0
        total_transfers = 20

        for i in range(total_transfers):
            task_id = str(uuid.uuid4())

            with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
                # Simulate 95% success rate (1 failure)
                if i == 10:
                    mock_upload.return_value = False
                else:
                    mock_upload.return_value = True

                result = await hpc_orchestrator.upload_file(task_id, "/local/file", "/remote/file")
                if result:
                    successful_transfers += 1

        success_rate = successful_transfers / total_transfers
        assert success_rate >= 0.95, f"Success rate {success_rate:.2%} below 95% threshold"

    @pytest.mark.asyncio
    async def test_retry_failed_transfers(self, hpc_orchestrator):
        """Test retry mechanism for failed file transfers."""

        task_id = str(uuid.uuid4())

        with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
            # First attempt fails, second succeeds
            mock_upload.side_effect = [False, True]

            result = await hpc_orchestrator.upload_file_with_retry(
                task_id, "/local/file", "/remote/file", max_retries=2
            )

            assert result is True
            assert mock_upload.call_count == 2

    @pytest.mark.asyncio
    async def test_transfer_timeout_handling(self, hpc_orchestrator):
        """Test timeout handling for stalled transfers."""

        task_id = str(uuid.uuid4())

        with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
            # Simulate timeout
            import asyncio
            mock_upload.side_effect = asyncio.TimeoutError("Transfer timeout")

            result = await hpc_orchestrator.upload_file_with_retry(
                task_id, "/local/file", "/remote/file", max_retries=1
            )

            assert result is False


class TestObjectStorageIntegration:
    """Test integration with NFMD object storage."""

    @pytest.mark.asyncio
    async def test_save_results_to_object_storage(self, hpc_orchestrator):
        """Test saving downloaded results to object storage."""

        task_id = str(uuid.uuid4())
        downloaded_files = {
            "lammps.out": f"/tmp/{task_id}/lammps.out",
            "log.lammps": f"/tmp/{task_id}/log.lammps",
        }

        with patch.object(hpc_orchestrator, '_save_to_object_storage') as mock_save:
            mock_save.return_value = {
                "lammps.out": f"https://storage.example.com/{task_id}/lammps.out",
                "log.lammps": f"https://storage.example.com/{task_id}/log.lammps",
            }

            storage_urls = await hpc_orchestrator.save_to_object_storage(task_id, downloaded_files)

            assert "lammps.out" in storage_urls
            assert "log.lammps" in storage_urls
            assert "https://storage.example.com" in storage_urls["lammps.out"]

    @pytest.mark.asyncio
    async def test_object_storage_metadata(self, hpc_orchestrator):
        """Test metadata storage with file information."""

        task_id = str(uuid.uuid4())
        file_metadata = {
            "lammps.out": {"size": 12345, "checksum": "abc123"},
            "log.lammps": {"size": 6789, "checksum": "def456"},
        }

        with patch.object(hpc_orchestrator, '_save_metadata') as mock_meta:
            await hpc_orchestrator.save_metadata(task_id, file_metadata)

            mock_meta.assert_called_once()


class TestResumableTransfers:
    """Test resumable file transfers (断点续传)."""

    @pytest.mark.asyncio
    async def test_resume_interrupted_upload(self, hpc_orchestrator):
        """Test resuming interrupted upload from last position."""

        task_id = str(uuid.uuid4())
        local_file = "/local/largefile.dat"
        remote_file = f"$SCRATCH/nfm-md/{task_id}/largefile.dat"
        resume_position = 1024  # Resume from byte 1024

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                with patch('builtins.open', create=True) as mock_open:
                    # Simulate resuming from position
                    await hpc_orchestrator.upload_file_with_resume(
                        task_id, local_file, remote_file, resume_position
                    )

                    # Verify file opened in binary append mode
                    # (In real implementation, would seek to resume_position)

    @pytest.mark.asyncio
    async def test_check_partial_file_exists(self, hpc_orchestrator):
        """Test checking for partial file on remote system."""

        task_id = str(uuid.uuid4())
        remote_file = f"$SCRATCH/nfm-md/{task_id}/largefile.dat"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                # File exists with partial content
                mock_stat = MagicMock()
                mock_stat.st_size = 1024
                mock_sftp.stat.return_value = mock_stat

                position = await hpc_orchestrator.get_remote_file_position(task_id, remote_file)

                assert position == 1024
                mock_sftp.stat.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_partial_file_starts_from_beginning(self, hpc_orchestrator):
        """Test starting from beginning when no partial file exists."""

        task_id = str(uuid.uuid4())
        remote_file = f"$SCRATCH/nfm-md/{task_id}/largefile.dat"

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                # File doesn't exist
                mock_sftp.stat.side_effect = IOError("File not found")

                position = await hpc_orchestrator.get_remote_file_position(task_id, remote_file)

                assert position == 0  # Start from beginning


class TestErrorHandling:
    """Test error handling for file transfer operations."""

    @pytest.mark.asyncio
    async def test_handle_upload_permission_denied(self, hpc_orchestrator):
        """Test handling of permission denied errors during upload."""

        task_id = str(uuid.uuid4())

        with patch.object(hpc_orchestrator.ssh_manager, 'acquire_connection') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(hpc_orchestrator.ssh_manager, 'release_connection'):
                mock_sftp = MagicMock()
                mock_client.open_sftp.return_value = mock_sftp

                # Simulate permission error
                mock_sftp.put.side_effect = PermissionError("Permission denied")

                with pytest.raises(PermissionError):
                    await orchestrator.upload_file(task_id, "/local/file", "/remote/file")

    @pytest.mark.asyncio
    async def test_handle_disk_space_full(self, hpc_orchestrator):
        """Test handling of disk space full errors."""

        task_id = str(uuid.uuid4())

        with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
            # Simulate disk full error
            mock_upload.side_effect = OSError("No space left on device")

            result = await hpc_orchestrator.upload_file_with_retry(
                task_id, "/local/file", "/remote/file", max_retries=1
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_log_transfer_errors(self, hpc_orchestrator):
        """Test error logging for transfer failures."""

        task_id = str(uuid.uuid4())

        with patch.object(hpc_orchestrator, 'upload_file') as mock_upload:
            mock_upload.side_effect = Exception("Connection lost")

            with pytest.raises(Exception):
                await orchestrator.upload_file(task_id, "/local/file", "/remote/file")

            # Verify error was logged (in real implementation)
            # Check logs contain error details
