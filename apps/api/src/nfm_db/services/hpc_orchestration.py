"""HPC Orchestration Service - Composition Root.

This module serves as the public API entry point for the HPC orchestration
system. It composes focused submodules and re-exports all public symbols
for backward compatibility.

Submodules:
- hpc_ssh: SSH connection management
- hpc_failover: Cluster failover logic
- hpc_slurm: SLURM job submission
- hpc_job_monitor: Job status polling and sync
- hpc_file_transfer: File upload/download operations
- hpc_metrics: Prometheus metrics definitions
- hpc_sync: Celery periodic sync task

Architecture follows Phase 4.1-4.5 implementation plan from NFM-345.
Refactored in NFM-355 to comply with ≤800 line file size limit.
"""

import contextlib
import logging
from typing import Any

from nfm_db.database import get_db
from nfm_db.services.hpc_failover import HPCFailoverManager
from nfm_db.services.hpc_file_transfer import (
    create_task_directory as _create_task_directory,
)
from nfm_db.services.hpc_file_transfer import (
    download_file as _download_file,
)
from nfm_db.services.hpc_file_transfer import (
    download_results as _download_results,
)
from nfm_db.services.hpc_file_transfer import (
    get_remote_checksum as _get_remote_checksum,
)
from nfm_db.services.hpc_file_transfer import (
    get_remote_file_position as _get_remote_file_position,
)
from nfm_db.services.hpc_file_transfer import (
    save_metadata as _save_metadata,
)
from nfm_db.services.hpc_file_transfer import (
    save_to_object_storage as _save_to_object_storage,
)
from nfm_db.services.hpc_file_transfer import (
    upload_file as _upload_file,
)
from nfm_db.services.hpc_file_transfer import (
    upload_file_with_resume as _upload_file_with_resume,
)
from nfm_db.services.hpc_file_transfer import (
    upload_file_with_retry as _upload_file_with_retry,
)
from nfm_db.services.hpc_file_transfer import (
    upload_files as _upload_files,
)
from nfm_db.services.hpc_file_transfer import (
    verify_checksum as _verify_checksum,
)
from nfm_db.services.hpc_job_monitor import (
    check_job_completion,
    execute_squeue,
    get_active_jobs,
    update_job_status,
)
from nfm_db.services.hpc_slurm import (
    create_hpc_job_record,
    generate_slurm_script,
    parse_walltime,
    submit_to_slurm,
    validate_simulation_params,
)
from nfm_db.services.hpc_ssh import (
    HPCConnectionError,
    SSHConnectionConfig,
    SSHConnectionManager,
)

logger = logging.getLogger(__name__)


class HPCOrchestrator:
    """Main HPC orchestration service for job submission and monitoring.

    Composes focused submodules for SSH, failover, SLURM, monitoring,
    and file transfer. Delegates all operations to the appropriate module.

    Handles SLURM job submission, status monitoring, file transfer, and failover.
    """

    def __init__(self, config: SSHConnectionConfig) -> None:
        """Initialize HPC orchestrator with SSH config.

        Args:
            config: SSH connection configuration
        """
        self.config = config
        self.ssh_manager = SSHConnectionManager(
            host=list(config.hosts) if isinstance(config.hosts, tuple) else config.hosts,
            username=config.username,
            ssh_key_path=config.ssh_key_path,
            max_connections=config.max_connections,
        )
        self.hpc_cluster = config.hosts[0] if config.hosts else ""

        # Initialize failover manager
        backup_ssh_manager = None
        backup_hosts = config.backup_hosts
        if backup_hosts and config.backup_username and config.backup_ssh_key_path:
            backup_ssh_manager = SSHConnectionManager(
                host=list(backup_hosts) if isinstance(backup_hosts, tuple) else backup_hosts,
                username=config.backup_username,
                ssh_key_path=config.backup_ssh_key_path,
                max_connections=config.max_connections,
            )
            logger.info(f"Backup cluster configured: {backup_hosts[0]}")

        self.failover_manager = HPCFailoverManager(
            config=config,
            ssh_manager=self.ssh_manager,
            backup_ssh_manager=backup_ssh_manager,
        )

    def cleanup(self) -> None:
        """Clean up all SSH connections and resources."""
        self.failover_manager.cleanup()

    def __del__(self) -> None:
        """Destructor to ensure cleanup on garbage collection."""
        with contextlib.suppress(Exception):
            self.cleanup()

    # =========================================================================
    # Failover delegates
    # =========================================================================

    async def _log_failover_event(
        self,
        event_type: str,
        source_cluster: str,
        reason: str,
        success: bool = True,
        target_cluster: str | None = None,
        failure_count: int = 0,
        event_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log failover event to database using module-level get_db.

        Kept in the orchestrator (not delegated) so that tests can
        patch nfm_db.services.hpc_orchestration.get_db.
        """
        from nfm_db.models.hpc_failover_event import HPCFailoverEvent

        try:
            async for db in get_db():
                event = HPCFailoverEvent(
                    event_type=event_type,
                    source_cluster=source_cluster,
                    target_cluster=target_cluster,
                    reason=reason,
                    failure_count=failure_count,
                    success=success,
                    event_metadata=event_metadata or {},
                )
                db.add(event)
                await db.commit()
                logger.info(
                    f"Logged failover event: {event_type} - {source_cluster} -> {target_cluster}"
                )
                break
        except Exception as e:
            logger.error(f"Failed to log failover event to database: {e}")
            logger.info(
                f"FAILOVER EVENT (stdout fallback): {event_type} - "
                f"{source_cluster} -> {target_cluster} - {reason}"
            )

    def check_primary_health(self) -> bool:
        """Check if primary cluster is healthy."""
        return self.failover_manager.check_primary_health()

    def should_trigger_failover(self) -> bool:
        """Determine if failover should be triggered."""
        return self.failover_manager.should_trigger_failover()

    async def trigger_failover(self) -> bool:
        """Trigger failover to backup cluster."""
        result = await self.failover_manager.trigger_failover(log_event_fn=self._log_failover_event)
        return result

    async def try_recover_primary(self) -> bool:
        """Attempt to recover connection to primary cluster."""
        result = await self.failover_manager.try_recover_primary(
            log_event_fn=self._log_failover_event
        )
        return result

    # =========================================================================
    # SLURM job submission
    # =========================================================================

    async def submit_job(
        self,
        task_id: str,
        crystal_structure_file: str,
        params: dict[str, Any],
    ) -> str:
        """Submit MD verification job to HPC.

        Args:
            task_id: UUID of md_verification_jobs record
            crystal_structure_file: Path to input structure file
            params: Simulation parameters (temp, pressure, steps, etc.)

        Returns:
            hpc_job_id: SLURM job identifier

        Raises:
            HPCConnectionError: Primary and backup clusters unavailable
            JobSubmissionError: SLURM submission failed
        """
        if self.failover_manager.should_trigger_failover():
            if await self.failover_manager.trigger_failover():
                logger.warning(f"Using backup cluster for job {task_id}")
            else:
                raise HPCConnectionError("Both primary and backup clusters unavailable")

        if self.failover_manager.current_cluster == "backup":
            if await self.failover_manager.try_recover_primary():
                logger.info(f"Switching back to primary cluster for job {task_id}")
                self.failover_manager.current_cluster = "primary"

        validate_simulation_params(params)

        slurm_script = generate_slurm_script(params)

        cluster_manager = self.failover_manager.current_ssh_manager
        cluster_name = self.failover_manager.current_cluster_name

        try:
            hpc_job_id = await submit_to_slurm(cluster_manager, cluster_name, task_id, slurm_script)
        except Exception as e:
            if self.failover_manager.current_cluster == "primary":
                if await self.failover_manager.trigger_failover():
                    logger.warning(f"Primary submission failed, retrying on backup: {e}")
                    cluster_manager = self.failover_manager.current_ssh_manager
                    cluster_name = self.failover_manager.current_cluster_name
                    hpc_job_id = await submit_to_slurm(
                        cluster_manager, cluster_name, task_id, slurm_script
                    )
                else:
                    raise HPCConnectionError(f"Job submission failed on all clusters: {e}")
            else:
                raise HPCConnectionError(f"Job submission failed on all clusters: {e}")

        await create_hpc_job_record(task_id, hpc_job_id, params, cluster_name)

        return hpc_job_id

    def _validate_simulation_params(self, params: dict[str, Any]) -> None:
        """Validate simulation parameters."""
        validate_simulation_params(params)

    def _generate_slurm_script(self, params: dict[str, Any]) -> str:
        """Generate SLURM batch script from parameters."""
        return generate_slurm_script(params)

    async def _submit_to_slurm(self, task_id: str, slurm_script: str) -> str:
        """Submit job to SLURM cluster via SSH."""
        cluster_manager = self.failover_manager.current_ssh_manager
        cluster_name = self.failover_manager.current_cluster_name
        return await submit_to_slurm(cluster_manager, cluster_name, task_id, slurm_script)

    def _upload_script_via_sftp(self, client, script_content: str, remote_path: str) -> None:
        """Upload script content to remote path via SFTP."""
        from nfm_db.services.hpc_slurm import upload_script_via_sftp

        upload_script_via_sftp(client, script_content, remote_path)

    async def _create_hpc_job_record(
        self, task_id: str, hpc_job_id: str, params: dict[str, Any], cluster_used: str = "primary"
    ) -> None:
        """Create record in hpc_jobs table."""
        hpc_cluster = (
            self.hpc_cluster
            if cluster_used == "primary"
            else (self.config.backup_hosts[0] if self.config.backup_hosts else "unknown")
        )
        await create_hpc_job_record(task_id, hpc_job_id, params, hpc_cluster)

    def _parse_walltime(self, walltime: str) -> int:
        """Parse SLURM walltime format to minutes."""
        return parse_walltime(walltime)

    # =========================================================================
    # Job monitoring delegates
    # =========================================================================

    async def poll_job_status(self, hpc_job_id: str) -> str:
        """Poll SLURM for current job status.

        Calls self methods (not module-level functions) so that tests
        can patch.object(orchestrator, '_execute_squeue') etc.
        """
        try:
            squeue_output = await self._execute_squeue(hpc_job_id)

            if squeue_output and "RUNNING" in squeue_output:
                return "RUNNING"
            elif squeue_output and "PENDING" in squeue_output:
                return "PENDING"
            elif squeue_output is None:
                task_id = hpc_job_id.split("-")[-1]
                if await self._check_job_completion(task_id):
                    return "COMPLETED"
                else:
                    return "FAILED"
            else:
                return "FAILED"

        except Exception as e:
            logger.error(f"Failed to poll job status for {hpc_job_id}: {e}")
            return "FAILED"

    async def _execute_squeue(self, hpc_job_id: str):
        """Execute squeue command via SSH to check job status."""
        return await execute_squeue(self.ssh_manager, hpc_job_id)

    async def update_job_status(self, task_id: str, hpc_job_id: str) -> None:
        """Update job status in database."""
        return await update_job_status(self.ssh_manager, task_id, hpc_job_id)

    async def _check_job_completion(self, task_id: str) -> bool:
        """Check if job has completed successfully."""
        return await check_job_completion(self.ssh_manager, task_id)

    async def _get_active_jobs(self):
        """Get all active HPC jobs from database."""
        return await get_active_jobs()

    async def sync_all_active_jobs(self) -> None:
        """Sync status for all active HPC jobs.

        Calls self methods (not module-level functions) so that tests
        can patch.object(orchestrator, '_get_active_jobs') etc.
        """
        try:
            active_jobs = await self._get_active_jobs()

            for job in active_jobs:
                try:
                    await self.update_job_status(str(job.verification_job_id), job.hpc_job_id)
                except Exception as e:
                    logger.error(f"Failed to sync job {job.hpc_job_id}: {e}")

            logger.info(f"Synced {len(active_jobs)} active jobs")

        except Exception as e:
            logger.error(f"Failed to sync active jobs: {e}")

    # =========================================================================
    # File transfer delegates
    # =========================================================================

    async def upload_file(self, task_id: str, local_file: str, remote_file: str) -> bool:
        """Upload a single file to HPC cluster."""
        return await _upload_file(self.ssh_manager, task_id, local_file, remote_file)

    async def upload_files(self, task_id: str, files: list) -> dict[str, bool]:
        """Upload multiple files to HPC cluster."""
        return await _upload_files(self.ssh_manager, task_id, files)

    async def _create_task_directory(self, task_id: str) -> None:
        """Create task-specific directory on HPC cluster."""
        return await _create_task_directory(self.ssh_manager, task_id)

    async def download_file(self, task_id: str, remote_file: str, local_path: str):
        """Download a single file from HPC cluster."""
        return await _download_file(self.ssh_manager, task_id, remote_file, local_path)

    async def download_results(self, task_id: str) -> dict[str, str]:
        """Download all result files for a task."""
        return await _download_results(self.ssh_manager, task_id)

    async def verify_checksum(self, local_file: str, expected_checksum: str) -> bool:
        """Verify file checksum matches expected value."""
        return await _verify_checksum(local_file, expected_checksum)

    async def get_remote_checksum(self, task_id: str, remote_file: str):
        """Get checksum of remote file via SSH."""
        return await _get_remote_checksum(self.ssh_manager, task_id, remote_file)

    async def save_to_object_storage(
        self, task_id: str, downloaded_files: dict[str, str]
    ) -> dict[str, str]:
        """Save downloaded files to NFMD object storage."""
        return await _save_to_object_storage(task_id, downloaded_files)

    async def _save_metadata(self, task_id: str, file_metadata: dict[str, dict[str, Any]]) -> None:
        """Save file metadata to database."""
        return await _save_metadata(task_id, file_metadata)

    async def upload_file_with_retry(
        self, task_id: str, local_file: str, remote_file: str, max_retries: int = 3
    ) -> bool:
        """Upload file with automatic retry on failure."""
        return await _upload_file_with_retry(
            self.ssh_manager, task_id, local_file, remote_file, max_retries
        )

    async def upload_file_with_resume(
        self, task_id: str, local_file: str, remote_file: str, resume_position: int = 0
    ) -> bool:
        """Upload file with resume capability."""
        return await _upload_file_with_resume(
            self.ssh_manager, task_id, local_file, remote_file, resume_position
        )

    async def get_remote_file_position(self, task_id: str, remote_file: str) -> int:
        """Get current position of partial file on remote system."""
        return await _get_remote_file_position(self.ssh_manager, task_id, remote_file)
