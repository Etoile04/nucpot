"""HPC Cluster Failover Management.

Manages automatic failover between primary and backup HPC clusters,
including health monitoring, failover triggering, and primary recovery.
"""

import logging
from datetime import datetime
from typing import Any

from nfm_db.services.hpc_metrics import PROMETHEUS_AVAILABLE, hpc_failover_events

logger = logging.getLogger(__name__)


class HPCFailoverManager:
    """Manages HPC cluster failover logic.

    Handles health monitoring, failover triggering, and primary cluster
    recovery. Extracted from HPCOrchestrator for single-responsibility.
    """

    def __init__(self, config, ssh_manager, backup_ssh_manager=None) -> None:
        """Initialize failover manager.

        Args:
            config: SSHConnectionConfig instance
            ssh_manager: Primary cluster SSHConnectionManager
            backup_ssh_manager: Optional backup cluster SSHConnectionManager
        """
        self._config = config
        self._ssh_manager = ssh_manager
        self._backup_ssh_manager = backup_ssh_manager
        self.current_cluster = "primary"
        self.primary_healthy = True
        self.last_health_check: datetime | None = None
        self.failover_count = 0

    @property
    def hpc_cluster(self) -> str:
        """Get primary cluster hostname."""
        return self._config.hosts[0] if isinstance(self._config.hosts, (list, tuple)) and self._config.hosts else ""

    @property
    def has_backup(self) -> bool:
        """Check if backup cluster is configured."""
        return self._backup_ssh_manager is not None

    @property
    def current_ssh_manager(self):
        """Get SSH manager for current active cluster."""
        if self.current_cluster == "backup" and self._backup_ssh_manager:
            return self._backup_ssh_manager
        return self._ssh_manager

    @property
    def current_cluster_name(self) -> str:
        """Get hostname of current active cluster."""
        if self.current_cluster == "backup":
            backup_hosts = self._config.backup_hosts
            if backup_hosts:
                return backup_hosts[0] if isinstance(backup_hosts[0], str) else str(backup_hosts[0])
            return "unknown"
        return self.hpc_cluster

    async def log_failover_event(
        self,
        event_type: str,
        source_cluster: str,
        reason: str,
        success: bool = True,
        target_cluster: str | None = None,
        failure_count: int = 0,
        event_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log failover event to database.

        Args:
            event_type: Type of event (failover_triggered, failover_failed, etc.)
            source_cluster: Cluster where failover initiated from
            reason: Human-readable description of why failover occurred
            success: Whether the operation succeeded
            target_cluster: Cluster switched to (if successful)
            failure_count: Number of consecutive failures
            event_metadata: Additional metadata (JSONB field)
        """
        from nfm_db.database import get_db
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
                logger.info(f"Logged failover event: {event_type} - {source_cluster} -> {target_cluster}")
                break
        except Exception as e:
            logger.error(f"Failed to log failover event to database: {e}")
            logger.info(
                f"FAILOVER EVENT (stdout fallback): {event_type} - "
                f"{source_cluster} -> {target_cluster} - {reason}"
            )

    def check_primary_health(self) -> bool:
        """Check if primary cluster is healthy.

        Returns:
            True if primary cluster is healthy, False otherwise
        """
        try:
            client = self._ssh_manager.acquire_connection_with_retry(max_retries=1)
            if client:
                self._ssh_manager.release_connection(client)
                self.last_health_check = datetime.now()
                return True
            return False
        except Exception as e:
            logger.warning(f"Primary health check failed: {e}")
            return False

    def should_trigger_failover(self) -> bool:
        """Determine if failover should be triggered based on primary health.

        Returns:
            True if failover should be triggered, False otherwise
        """
        if not self._backup_ssh_manager:
            logger.warning("No backup cluster configured - failover not available")
            return False

        if not self.primary_healthy:
            return True

        if self.last_health_check:
            time_since_healthy = (datetime.now() - self.last_health_check).total_seconds()
            if time_since_healthy > self._config.failover_threshold_seconds:
                logger.error(f"Primary cluster unhealthy for {time_since_healthy:.1f}s - triggering failover")
                self.primary_healthy = False
                return True

        is_healthy = self.check_primary_health()
        if not is_healthy:
            if self.last_health_check:
                time_since_healthy = (datetime.now() - self.last_health_check).total_seconds()
                if time_since_healthy > self._config.failover_threshold_seconds:
                    logger.error(f"Primary cluster unhealthy for {time_since_healthy:.1f}s - triggering failover")
                    self.primary_healthy = False
                    return True
            else:
                self.last_health_check = datetime.now()

        return False

    async def trigger_failover(self, log_event_fn=None) -> bool:
        """Trigger failover to backup cluster.

        Args:
            log_event_fn: Optional logging callback with the same
                signature as log_failover_event.  When provided the
                orchestrator can inject its own patchable logger.

        Returns:
            True if failover was successful, False otherwise
        """
        _log = log_event_fn or self.log_failover_event
        if not self._backup_ssh_manager:
            logger.error("Cannot trigger failover - no backup cluster configured")
            await _log(
                event_type="failover_failed",
                source_cluster=self.hpc_cluster,
                target_cluster=None,
                reason="No backup cluster configured",
                success=False,
                failure_count=self.failover_count,
                event_metadata={"error": "No backup cluster configured"},
            )
            return False

        try:
            client = self._backup_ssh_manager.acquire_connection_with_retry(max_retries=2)
            if not client:
                logger.error("Backup cluster connectivity test failed")
                await _log(
                    event_type="failover_failed",
                    source_cluster=self.hpc_cluster,
                    target_cluster=self.current_cluster_name,
                    reason="Backup cluster connectivity test failed",
                    success=False,
                    failure_count=self.failover_count,
                    event_metadata={"error": "Backup cluster unreachable"},
                )
                return False

            self._backup_ssh_manager.release_connection(client)

            self.failover_count += 1
            from_cluster = self.hpc_cluster
            to_cluster = self.current_cluster_name

            await _log(
                event_type="failover_triggered",
                source_cluster=from_cluster,
                target_cluster=to_cluster,
                reason=f"Primary cluster down after {self.failover_count} consecutive failures",
                success=True,
                failure_count=self.failover_count,
                event_metadata={"failover_number": self.failover_count},
            )

            logger.error(f"FAILOVER #{self.failover_count}: {from_cluster} -> {to_cluster}")

            if PROMETHEUS_AVAILABLE:
                hpc_failover_events.labels(
                    from_cluster=from_cluster,
                    to_cluster=to_cluster,
                ).inc()

            self.current_cluster = "backup"
            return True

        except Exception as e:
            logger.error(f"Failover failed: {e}")
            backup_hosts = self._config.backup_hosts
            await _log(
                event_type="failover_failed",
                source_cluster=self.hpc_cluster,
                target_cluster=backup_hosts[0] if backup_hosts else None,
                reason=f"Exception during failover: {e!s}",
                success=False,
                failure_count=self.failover_count,
                event_metadata={"exception": str(e), "exception_type": type(e).__name__},
            )
            return False

    async def try_recover_primary(self, log_event_fn=None) -> bool:
        """Attempt to recover connection to primary cluster.

        Args:
            log_event_fn: Optional logging callback (see trigger_failover).

        Returns:
            True if primary cluster recovered, False otherwise
        """
        _log = log_event_fn or self.log_failover_event

        if self.check_primary_health():
            logger.info("Primary cluster recovered - will switch back on next job submission")
            self.primary_healthy = True

            await _log(
                event_type="primary_recovered",
                source_cluster=self.hpc_cluster,
                target_cluster=None,
                reason="Primary cluster health restored",
                success=True,
                failure_count=0,
                event_metadata={"recovery_timestamp": datetime.now().isoformat()},
            )
            return True

        await _log(
            event_type="recovery_attempted",
            source_cluster=self.hpc_cluster,
            target_cluster=None,
            reason="Primary cluster still unhealthy",
            success=False,
            failure_count=0,
            event_metadata={"health_check_failed": True},
        )
        return False

    def cleanup(self) -> None:
        """Clean up all managed SSH connections."""
        if self._backup_ssh_manager:
            self._backup_ssh_manager.cleanup()
        if self._ssh_manager:
            self._ssh_manager.cleanup()
