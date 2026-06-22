"""HPC Job Monitoring and Status Synchronization.

Handles SLURM job status polling, database status updates,
completion checks, and periodic sync of all active jobs.
"""

import logging
from typing import List, Optional
from sqlalchemy import select

from nfm_db.models.md_verification import HpcJob, HpcJobStatus, MDVerificationJob

logger = logging.getLogger(__name__)


async def execute_squeue(ssh_manager, hpc_job_id: str) -> Optional[str]:
    """Execute squeue command via SSH to check job status.

    Args:
        ssh_manager: SSHConnectionManager instance
        hpc_job_id: SLURM job identifier

    Returns:
        squeue output string, or None if job not found in queue
    """
    client = None
    try:
        client = ssh_manager.acquire_connection()

        job_id = hpc_job_id.replace("slurm-", "")

        if not job_id.isdigit():
            raise ValueError(f"Invalid job ID format (must be numeric): {hpc_job_id}")

        cmd = f"squeue -j {job_id} -o '%T %j'"
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_msg = stderr.read().decode()
            if "slurm_load_error" in error_msg or "Invalid job id" in error_msg:
                return None
            else:
                raise Exception(f"squeue command failed: {error_msg}")

        output = stdout.read().decode().strip()
        return output if output else None

    finally:
        if client:
            ssh_manager.release_connection(client)


async def check_job_completion(ssh_manager, task_id: str) -> bool:
    """Check if job has completed successfully by checking output files.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: Task identifier

    Returns:
        True if job completed successfully, False otherwise
    """
    client = None
    try:
        client = ssh_manager.acquire_connection()

        remote_dir = f"$SCRATCH/nfm-md/{task_id}"
        output_files = ["lammps.out", "log.lammps"]

        sftp = None
        try:
            sftp = client.open_sftp()

            for output_file in output_files:
                remote_path = f"{remote_dir}/{output_file}"
                try:
                    file_stat = sftp.stat(remote_path)
                    if file_stat.st_size > 0:
                        return True
                except IOError:
                    pass

            return False

        finally:
            if sftp:
                sftp.close()

    except Exception as e:
        logger.warning(f"Failed to check job completion: {e}")
        return False
    finally:
        if client:
            ssh_manager.release_connection(client)


async def poll_job_status(ssh_manager, hpc_job_id: str) -> str:
    """Poll SLURM for current job status.

    Args:
        ssh_manager: SSHConnectionManager instance
        hpc_job_id: SLURM job identifier (e.g., "slurm-12345")

    Returns:
        Job status: PENDING, RUNNING, COMPLETED, or FAILED
    """
    try:
        squeue_output = await execute_squeue(ssh_manager, hpc_job_id)

        if squeue_output and "RUNNING" in squeue_output:
            return "RUNNING"
        elif squeue_output and "PENDING" in squeue_output:
            return "PENDING"
        elif squeue_output is None:
            task_id = hpc_job_id.split("-")[-1]
            if await check_job_completion(ssh_manager, task_id):
                return "COMPLETED"
            else:
                return "FAILED"
        else:
            return "FAILED"

    except Exception as e:
        logger.error(f"Failed to poll job status for {hpc_job_id}: {e}")
        return "FAILED"


async def update_job_status(
    ssh_manager,
    task_id: str,
    hpc_job_id: str,
) -> None:
    """Update job status in database.

    Args:
        ssh_manager: SSHConnectionManager instance
        task_id: MD verification job ID
        hpc_job_id: HPC job identifier

    Raises:
        Exception: If database update fails
    """
    import uuid as uuid_module
    from nfm_db.database import get_db

    try:
        status = await poll_job_status(ssh_manager, hpc_job_id)

        db_gen = get_db()
        db = await db_gen.__anext__()

        try:
            hpc_result = await db.execute(
                select(HpcJob).where(HpcJob.hpc_job_id == hpc_job_id)
            )
            hpc_job = hpc_result.scalar_one_or_none()

            if hpc_job:
                hpc_job.status = HpcJobStatus[status]
                await db.commit()

            verification_job = await db.get(MDVerificationJob, uuid_module.UUID(task_id))
            if verification_job:
                verification_job.status = status
                await db.commit()

            logger.info(f"Updated job status: {task_id} -> {status}")

        except Exception:
            await db.rollback()
            raise
        finally:
            try:
                await db_gen.__anext__()
            except StopAsyncIteration:
                pass

    except Exception as e:
        logger.error(f"Failed to update job status: {e}")
        raise


async def get_active_jobs() -> List[HpcJob]:
    """Get all active HPC jobs from database.

    Returns:
        List of active HpcJob objects (PENDING or RUNNING)
    """
    from nfm_db.database import get_db

    db_gen = get_db()
    db = await db_gen.__anext__()

    try:
        result = await db.execute(
            select(HpcJob).where(
                HpcJob.status.in_([HpcJobStatus.PENDING, HpcJobStatus.RUNNING])
            )
        )
        return result.scalars().all()
    finally:
        try:
            await db_gen.__anext__()
        except StopAsyncIteration:
            pass


async def sync_all_active_jobs(ssh_manager) -> None:
    """Sync status for all active HPC jobs (called by Celery beat).

    Args:
        ssh_manager: SSHConnectionManager instance for SSH operations
    """
    try:
        active_jobs = await get_active_jobs()

        for job in active_jobs:
            try:
                await update_job_status(ssh_manager, str(job.verification_job_id), job.hpc_job_id)
            except Exception as e:
                logger.error(f"Failed to sync job {job.hpc_job_id}: {e}")

        logger.info(f"Synced {len(active_jobs)} active jobs")

    except Exception as e:
        logger.error(f"Failed to sync active jobs: {e}")
