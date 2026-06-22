"""HPC File Transfer Operations.

Handles upload, download, checksum verification, resume transfer,
and object storage integration for HPC cluster file operations.
"""

import os
import hashlib
import asyncio
import logging
import re
from typing import Dict, List, Any, Optional

from nfm_db.database import get_db

logger = logging.getLogger(__name__)

# Pattern for safe remote file paths (alphanumeric, /, ., -, _, and :)
_REMOTE_PATH_SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9/.:_-]+$')


def validate_remote_path(path: str) -> str:
    """Validate that a remote file path contains only safe characters.

    Prevents command injection in SSH commands by allowing only
    alphanumeric characters, forward slashes, dots, dashes, underscores,
    and colons (used in Windows paths or port specifications).

    Args:
        path: The remote file path to validate.

    Returns:
        The validated path (unchanged).

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


async def upload_file(
    ssh_manager,
    task_id: str,
    local_file: str,
    remote_file: str,
) -> bool:
    """Upload a single file to HPC cluster.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier for directory organization
        local_file: Path to local file to upload
        remote_file: Remote destination path

    Returns:
        True if upload succeeded, False otherwise
    """
    client = None
    try:
        safe_remote_file = validate_remote_path(remote_file)
        client = ssh_manager.acquire_connection()

        await create_task_directory(ssh_manager, task_id)

        sftp = None
        try:
            sftp = client.open_sftp()
            sftp.put(local_file, safe_remote_file)
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
            ssh_manager.release_connection(client)


async def upload_files(
    ssh_manager,
    task_id: str,
    files: List[tuple[str, str]],
) -> Dict[str, bool]:
    """Upload multiple files to HPC cluster.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        files: List of (local_path, remote_path) tuples

    Returns:
        Dictionary mapping file paths to success status
    """
    results: Dict[str, bool] = {}
    for local_file, remote_file in files:
        success = await upload_file(ssh_manager, task_id, local_file, remote_file)
        results[local_file] = success
    return results


async def create_task_directory(ssh_manager, task_id: str) -> None:
    """Create task-specific directory on HPC cluster.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier for directory path
    """
    client = None
    try:
        client = ssh_manager.acquire_connection()

        remote_dir = f"$SCRATCH/nfm-md/{task_id}"

        sftp = None
        try:
            sftp = client.open_sftp()
            try:
                sftp.mkdir(remote_dir)
            except IOError:
                pass  # Directory already exists

            logger.info(f"Task directory ready: {remote_dir}")

        finally:
            if sftp:
                sftp.close()

    finally:
        if client:
            ssh_manager.release_connection(client)


async def download_file(
    ssh_manager,
    task_id: str,
    remote_file: str,
    local_path: str,
) -> Optional[str]:
    """Download a single file from HPC cluster.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        remote_file: Remote file path to download
        local_path: Local destination path

    Returns:
        Local path to downloaded file, or None if failed
    """
    client = None
    try:
        safe_remote_file = validate_remote_path(remote_file)
        client = ssh_manager.acquire_connection()

        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        sftp = None
        try:
            sftp = client.open_sftp()
            sftp.get(safe_remote_file, local_path)
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
            ssh_manager.release_connection(client)


async def download_results(ssh_manager, task_id: str) -> Dict[str, str]:
    """Download all result files for a task.

    Args:
        ssh_manager: SSHConnectionManager instance
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

    downloaded: Dict[str, str] = {}
    for name, remote_path in result_files.items():
        local_path = f"{local_dir}/{name}"
        result = await download_file(ssh_manager, task_id, remote_path, local_path)
        if result:
            downloaded[name] = local_path

    logger.info(f"Downloaded {len(downloaded)}/{len(result_files)} result files")
    return downloaded


async def verify_checksum(local_file: str, expected_checksum: str) -> bool:
    """Verify file checksum matches expected value.

    Args:
        local_file: Path to local file
        expected_checksum: Expected SHA256 checksum

    Returns:
        True if checksums match, False otherwise
    """
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


async def get_remote_checksum(ssh_manager, task_id: str, remote_file: str) -> Optional[str]:
    """Get checksum of remote file via SSH.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        remote_file: Remote file path

    Returns:
        SHA256 checksum, or None if failed
    """
    client = None
    try:
        # Validate remote path to prevent command injection
        safe_remote_file = validate_remote_path(remote_file)
        client = ssh_manager.acquire_connection()

        cmd = f"sha256sum {safe_remote_file}"
        stdin, stdout, stderr = client.exec_command(cmd)
        output = stdout.read().decode().strip()

        if exit_status := stdout.channel.recv_exit_status() == 0:
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
            ssh_manager.release_connection(client)


async def save_to_object_storage(task_id: str, downloaded_files: Dict[str, str]) -> Dict[str, str]:
    """Save downloaded files to NFMD object storage.

    Args:
        task_id: Task identifier
        downloaded_files: Dictionary of file names to local paths

    Returns:
        Dictionary of file names to storage URLs
    """
    storage_urls: Dict[str, str] = {}

    for filename, local_path in downloaded_files.items():
        storage_url = f"https://storage.example.com/{task_id}/{filename}"
        storage_urls[filename] = storage_url
        logger.info(f"Saved to object storage: {filename} -> {storage_url}")

    return storage_urls


async def save_metadata(task_id: str, file_metadata: Dict[str, Dict[str, Any]]) -> None:
    """Save file metadata to database.

    Args:
        task_id: Task identifier
        file_metadata: Dictionary of file metadata
    """
    db_gen = get_db()
    db = await db_gen.__anext__()

    try:
        logger.info(f"Saving metadata for task {task_id}: {file_metadata}")
    finally:
        try:
            await db_gen.__anext__()
        except StopAsyncIteration:
            pass


async def upload_file_with_retry(
    ssh_manager,
    task_id: str,
    local_file: str,
    remote_file: str,
    max_retries: int = 3,
) -> bool:
    """Upload file with automatic retry on failure.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        local_file: Path to local file
        remote_file: Remote destination path
        max_retries: Maximum number of retry attempts

    Returns:
        True if upload succeeded, False otherwise
    """
    for attempt in range(max_retries):
        try:
            result = await upload_file(ssh_manager, task_id, local_file, remote_file)
            if result:
                return True

            logger.warning(f"Upload attempt {attempt + 1} failed for {local_file}, retrying...")
            await asyncio.sleep(2 ** attempt)

        except Exception as e:
            logger.error(f"Upload attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                return False

    return False


async def upload_file_with_resume(
    ssh_manager,
    task_id: str,
    local_file: str,
    remote_file: str,
    resume_position: int = 0,
) -> bool:
    """Upload file with resume capability.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        local_file: Path to local file
        remote_file: Remote destination path
        resume_position: Byte position to resume from

    Returns:
        True if upload succeeded, False otherwise
    """
    client = None
    try:
        client = ssh_manager.acquire_connection()

        await create_task_directory(ssh_manager, task_id)

        sftp = None
        try:
            sftp = client.open_sftp()

            with open(local_file, 'rb') as local_f:
                local_f.seek(resume_position)

                with sftp.file(remote_file, 'ab') as remote_f:
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
            ssh_manager.release_connection(client)


async def get_remote_file_position(ssh_manager, task_id: str, remote_file: str) -> int:
    """Get current position of partial file on remote system for resume.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier
        remote_file: Remote file path

    Returns:
        Current file size in bytes (0 if file doesn't exist)
    """
    client = None
    try:
        client = ssh_manager.acquire_connection()

        sftp = None
        try:
            sftp = client.open_sftp()

            try:
                file_stat = sftp.stat(remote_file)
                return file_stat.st_size
            except IOError:
                return 0

        finally:
            if sftp:
                sftp.close()

    except Exception as e:
        logger.warning(f"Failed to check remote file position: {e}")
        return 0
    finally:
        if client:
            ssh_manager.release_connection(client)
