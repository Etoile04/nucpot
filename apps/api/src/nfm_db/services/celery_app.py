"""Celery Beat tasks for HPC orchestration monitoring."""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _get_redis_client():
    """Get Redis client for failure counting."""
    try:
        import redis

        return redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
        )
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return None


def increment_failure_count() -> int:
    """Increment primary cluster failure count in Redis."""
    redis_client = _get_redis_client()
    if not redis_client:
        logger.warning("Redis not available - using in-memory counter")
        return 1

    try:
        # Use pipeline for atomic incr + expire operation
        pipe = redis_client.pipeline()
        pipe.incr("hpc:primary_failure_count")
        pipe.expire("hpc:primary_failure_count", 600)
        results = pipe.execute()
        count = results[0]
        return count
    except Exception as e:
        logger.error(f"Failed to increment failure count: {e}")
        return 1


def reset_failure_count() -> None:
    """Reset primary cluster failure count."""
    redis_client = _get_redis_client()
    if not redis_client:
        logger.warning("Redis not available - counter not reset")
        return

    try:
        redis_client.delete("hpc:primary_failure_count")
    except Exception as e:
        logger.error(f"Failed to reset failure count: {e}")


def _validate_hpc_environment():
    """Validate HPC environment variables required for monitoring."""
    from pathlib import Path

    required_vars = ["NFM_HPC_PRIMARY_HOST", "NFM_HPC_PRIMARY_USER", "NFM_HPC_PRIMARY_SSH_KEY_PATH"]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Required HPC environment variables not set: {', '.join(missing_vars)}")

    ssh_key_path = os.getenv("NFM_HPC_PRIMARY_SSH_KEY_PATH")
    if not Path(ssh_key_path).exists():
        raise FileNotFoundError(f"SSH key not found: {ssh_key_path}")

    logger.info("HPC environment variables validated successfully")


from celery import Celery  # noqa: E402

celery_app = Celery("nfm_tasks")

# Route literature-processing tasks to their own queue so the MD worker
# (--queues=md_verification) and the literature worker
# (--queues=literature_processing) can scale independently. See
# docker-compose.prod.yml and the NFM-1489 dispatcher module.
celery_app.conf.task_routes = {
    "nfm_db.services.literature_dispatcher.process_literature_task": {
        "queue": "literature_processing",
    },
}


@celery_app.task(
    bind=True,
    name="nfm_db.services.md_tasks.run_md_verification",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(ConnectionError, IOError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def _run_md_verification_dispatch(self, job_id, potential_file, structure_file, config):
    """Celery task entry point — lazily imports and delegates to the plain impl.

    The import is deferred to avoid circular imports at module load time.
    The plain function in md_tasks.py is kept unchanged so unit tests can
    call it directly without Celery's bind/self wrapping.
    """
    from nfm_db.services.md_tasks import run_md_verification_task as _impl

    return _impl(self, job_id, potential_file, structure_file, config)


@celery_app.task(name="hpc.monitor_primary_cluster_health")
def monitor_primary_cluster_health() -> dict:
    """Periodic health check for primary HPC cluster."""

    async def _monitor():
        from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig

        try:
            # Validate environment variables first
            _validate_hpc_environment()

            try:
                # Create orchestrator with environment config
                config = SSHConnectionConfig(
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
                    skip_key_validation=True,  # For Celery task context
                )

                orchestrator = HPCOrchestrator(config)

                try:
                    # Check primary cluster health
                    is_healthy = orchestrator.check_primary_health()

                    if not is_healthy:
                        # Increment failure counter
                        failure_count = increment_failure_count()

                        # Check if we should trigger failover (5 consecutive failures)
                        if orchestrator.should_trigger_failover():
                            # Trigger failover
                            success = await orchestrator.trigger_failover()

                            if success:
                                reset_failure_count()
                                logger.error(
                                    f"Automatic failover triggered after {failure_count} failures"
                                )
                                return {
                                    "status": "failover_triggered",
                                    "failover_count": failure_count,
                                    "message": "Successfully failed over to backup cluster",
                                }
                            else:
                                logger.error(
                                    f"Failover attempted after {failure_count} failures but failed"
                                )
                                return {
                                    "status": "failover_failed",
                                    "failover_count": failure_count,
                                    "message": "Failover attempt failed",
                                }
                        else:
                            logger.warning(
                                f"Primary unhealthy (failure #{failure_count}), threshold not yet reached"
                            )
                    else:
                        # Primary is healthy - reset counter
                        reset_failure_count()

                    # If we're on backup cluster, try to recover primary
                    if orchestrator.current_cluster == "backup":
                        recovered = await orchestrator.try_recover_primary()
                        if recovered:
                            logger.info("Primary cluster recovered - new jobs will use primary")

                    return {
                        "status": "success",
                        "primary_healthy": is_healthy,
                        "current_cluster": orchestrator.current_cluster,
                        "message": "Health check completed",
                    }

                finally:
                    orchestrator.cleanup()

            except Exception as e:
                logger.error(f"HPC health monitoring failed: {e}")
                return {"status": "error", "message": str(e), "error_type": type(e).__name__}

        except Exception as e:
            logger.error(f"Unexpected error in monitoring setup: {e}")
            return {"status": "error", "message": str(e), "error_type": type(e).__name__}

    # Handle both Celery (sync) and pytest (async) contexts
    try:
        asyncio.get_running_loop()
        # Already in event loop (pytest async context) - run in thread with own loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _monitor())
            return future.result()
    except RuntimeError:
        # No event loop running (normal Celery context) - run directly
        try:
            return asyncio.run(_monitor())
        except Exception as e:
            logger.error(f"Failed to run health monitoring: {e}")
            return {"status": "error", "message": str(e)}
