"""HPC Job Status Synchronization (Celery Task).

Periodic Celery beat task that syncs status for all active HPC jobs.
Imports are deferred to avoid circular dependencies with hpc_orchestration.
"""

import asyncio
import logging
import os

from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def sync_hpc_job_status() -> dict:
    """Celery task for periodic HPC job status synchronization.

    This task is called by Celery beat every 30 seconds to update the status
    of all active HPC jobs in the system.

    Returns:
        Dictionary with sync status and statistics
    """
    # Deferred imports to avoid circular dependency with hpc_orchestration
    from nfm_db.services.hpc_job_monitor import sync_all_active_jobs
    from nfm_db.services.hpc_ssh import SSHConnectionConfig, SSHConnectionManager

    async def _sync_jobs() -> dict:
        try:
            config = SSHConnectionConfig.from_lists(
                hosts=[os.getenv("NFM_HPC_PRIMARY_HOST", "login.example.com")],
                username=os.getenv("NFM_HPC_PRIMARY_USER", "user"),
                ssh_key_path=os.getenv("NFM_HPC_PRIMARY_SSH_KEY_PATH", "/path/to/key"),
                max_connections=int(os.getenv("NFM_HPC_MAX_CONNECTIONS", "10")),
                backup_hosts=[os.getenv("NFM_HPC_BACKUP_HOST", "backup.example.com")]
                if os.getenv("NFM_HPC_BACKUP_HOST")
                else None,
                backup_username=os.getenv("NFM_HPC_BACKUP_USER"),
                backup_ssh_key_path=os.getenv("NFM_HPC_BACKUP_SSH_KEY_PATH"),
                failover_threshold_seconds=int(
                    os.getenv("NFM_HPC_FAILOVER_THRESHOLD_SECONDS", "300")
                ),
            )

            manager = SSHConnectionManager(
                host=config.hosts,
                username=config.username,
                ssh_key_path=config.ssh_key_path,
                max_connections=config.max_connections,
            )

            try:
                await sync_all_active_jobs(manager)
                return {
                    "status": "success",
                    "message": "HPC job status sync completed",
                }
            finally:
                manager.cleanup()

        except Exception as e:
            logger.error(f"HPC job status sync failed: {e}")
            return {
                "status": "error",
                "message": str(e),
                "jobs_processed": 0,
            }

    try:
        result = asyncio.run(_sync_jobs())
        return result
    except Exception as e:
        logger.error(f"Failed to run job sync: {e}")
        return {
            "status": "error",
            "message": str(e),
            "jobs_processed": 0,
        }
