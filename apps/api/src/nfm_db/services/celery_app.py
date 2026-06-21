"""Celery application configuration for NFMD async task processing.

Phase 2.5 of NFM-337: Celery configuration for MD verification tasks.
Phase 4.5 of NFM-346: Celery Beat monitoring for HPC failover.
Configures Celery app with Redis broker, result backend, and task registration.

Phase 2.5 of NFM-337: Celery configuration for MD verification tasks.
Configures Celery app with Redis broker, result backend, and task registration.

Usage:
    from nfm_db.services.celery_app import celery_app

    # Register tasks
    from nfm_db.services import md_tasks  # noqa: F401

    # Start worker
    celery_app.worker_main(['worker', '--loglevel=info'])

Environment Variables:
    NFM_CELERY_BROKER_URL: Redis connection URL for task broker
    NFM_CELERY_RESULT_BACKEND: Redis connection URL for result storage
    NFM_CELERY_TASK_TRACKING: Enable task event tracking (default: true)
    NFM_CELERY_TASK_SOFT_TIME_LIMIT: Soft time limit for tasks (default: 3600s)
    NFM_CELERY_TASK_TIME_LIMIT: Hard time limit for tasks (default: 7200s)
"""

from __future__ import annotations

import os
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

# Import settings
try:
    from pydantic_settings import BaseSettings, Field


    class CelerySettings(BaseSettings):
        """Celery configuration from environment variables."""

        model_config = {
            "extra": "ignore",
            "env_prefix": "NFM_CELERY_",
        }

        # Broker and backend
        broker_url: str = Field(
            default="redis://localhost:6379/0",
            description="Redis broker URL for Celery"
        )
        result_backend: str = Field(
            default="redis://localhost:6379/0",
            description="Redis backend URL for Celery results"
        )

        # Task settings
        task_soft_time_limit: int = Field(
            default=3600,
            description="Soft time limit for tasks in seconds"
        )
        task_time_limit: int = Field(
            default=7200,
            description="Hard time limit for tasks in seconds"
        )
        task_acks_late: bool = Field(
            default=True,
            description="Ack tasks after execution (more reliable)"
        )
        task_reject_on_worker_lost: bool = Field(
            default=True,
            description="Reject tasks if worker is lost"
        )
        task_track_started: bool = Field(
            default=True,
            description="Track when tasks start"
        )

        # Worker settings
        worker_prefetch_multiplier: int = Field(
            default=1,
            description="Number of tasks to prefetch per worker"
        )
        worker_max_tasks_per_child: int = Field(
            default=1000,
            description="Max tasks per worker before restart"
        )

        # Result settings
        result_expires: int = Field(
            default=86400,  # 24 hours
            description="Result expiration time in seconds"
        )
        result_compression: str = Field(
            default="gzip",
            description="Compression for result storage"
        )

        # Security
        broker_use_ssl: bool = Field(
            default=False,
            description="Use SSL for broker connection"
        )
        redis_max_connections: int = Field(
            default=50,
            description="Max connections to Redis"
        )

    celery_settings = CelerySettings()


except ImportError:
    # Fallback if pydantic-settings is not available
    class _FallbackSettings:
        """Fallback settings without pydantic."""

        broker_url = os.getenv("NFM_CELERY_BROKER_URL", "redis://localhost:6379/0")
        result_backend = os.getenv("NFM_CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
        task_soft_time_limit = int(os.getenv("NFM_CELERY_TASK_SOFT_TIME_LIMIT", "3600"))
        task_time_limit = int(os.getenv("NFM_CELERY_TASK_TIME_LIMIT", "7200"))
        task_acks_late = os.getenv("NFM_CELERY_TASK_ACKS_LATE", "true").lower() == "true"
        task_reject_on_worker_lost = os.getenv("NFM_CELERY_TASK_REJECT_ON_WORKER_LOST", "true").lower() == "true"
        task_track_started = os.getenv("NFM_CELERY_TASK_TRACK_STARTED", "true").lower() == "true"
        worker_prefetch_multiplier = int(os.getenv("NFM_CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))
        worker_max_tasks_per_child = int(os.getenv("NFM_CELERY_WORKER_MAX_TASKS_PER_CHILD", "1000"))
        result_expires = int(os.getenv("NFM_CELERY_RESULT_EXPIRES", "86400"))
        result_compression = os.getenv("NFM_CELERY_RESULT_COMPRESSION", "gzip")
        broker_use_ssl = os.getenv("NFM_CELERY_BROKER_USE_SSL", "false").lower() == "true"
        redis_max_connections = int(os.getenv("NFM_CELERY_REDIS_MAX_CONNECTIONS", "50"))
        task_default_routing = os.getenv("NFM_CELERY_TASK_DEFAULT_ROUTING", "nfm.tasks.md")


    celery_settings = _FallbackSettings()


# =============================================================================
# Celery Application
# =============================================================================


celery_app = Celery('nfm_tasks')

# Configure from environment
celery_app.conf.update(
    # Broker and backend
    broker_url=celery_settings.broker_url,
    result_backend=celery_settings.result_backend,

    # Task settings
    task_soft_time_limit=celery_settings.task_soft_time_limit,
    task_time_limit=celery_settings.task_time_limit,
    task_acks_late=celery_settings.task_acks_late,
    task_reject_on_worker_lost=celery_settings.task_reject_on_worker_lost,
    task_track_started=celery_settings.task_track_started,
    task_default_routing='nfm.tasks.md',

    # Worker settings
    worker_prefetch_multiplier=celery_settings.worker_prefetch_multiplier,
    worker_max_tasks_per_child=celery_settings.worker_max_tasks_per_child,

    # Result settings
    result_expires=timedelta(seconds=celery_settings.result_expires),
    result_compression=celery_settings.result_compression,

    # Security
    broker_use_ssl=celery_settings.broker_use_ssl,

    # Redis settings
    redis_max_connections=celery_settings.redis_max_connections,

    # Task serialization
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],

    # Timezone
    timezone='UTC',
    enable_utc=True,

    # Task tracking
    task_send_sent_event=True,
    task_send_ready_event=True,

    # Result backend settings
    result_backend_transport_options={
        'retry_policy': {
            'timeout': 5.0,
            'max_retries': 3,
        },
    },
)

# Beat scheduler configuration (for periodic tasks)
celery_app.conf.beat_schedule = {
    'cleanup-old-results-daily': {
        'task': 'nfm_db.services.md_tasks.cleanup_old_results',
        'schedule': crontab(hour=2, minute=0),  # 2 AM UTC daily
        'options': {'expires': 3600},
    },
    'sync-hpc-job-status': {
        'task': 'nfm_db.services.hpc_orchestration.sync_hpc_job_status',
        'schedule': 30.0,  # Every 30 seconds
        'options': {'expires': 60},
    },
    # NFM-346: Periodic HPC health monitoring
    'monitor-primary-cluster-every-30-seconds': {
        'task': 'hpc.monitor_primary_cluster_health',
        'schedule': 30.0,  # Every 30 seconds
        'options': {'expires': 60},
    },
}


# =============================================================================
# Task Registration
# =============================================================================


def register_tasks() -> None:
    """Register all Celery tasks.

    This function should be called after importing task modules
    to ensure all tasks are properly registered with the Celery app.
    """
    # Import task modules to register their @celery_app.task decorators
    try:
        from nfm_db.services import md_tasks  # noqa: F401

        # Tasks are registered via their .name attribute
        # run_md_verification_task.name = "nfm_db.services.md_tasks.run_md_verification"

        import logging
        logger = logging.getLogger(__name__)
        logger.info("Celery tasks registered successfully")

    except ImportError as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to import MD tasks: {e}")


# Auto-register tasks on module import
try:
    register_tasks()
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f"Task registration deferred: {e}")


# =============================================================================
# Health Check
# =============================================================================


@celery_app.task
def health_check() -> dict[str, str]:
    """Simple health check task for monitoring Celery worker status.

    Returns:
        Dictionary with health status and timestamp
    """
    from datetime import datetime

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Monitoring hooks
# =============================================================================


# Optional: Task success/failure callbacks
def on_task_success(request, context, result, *args, **kwargs):
    """Callback for successful task completion."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Task {request.task} [{request.id}] completed successfully",
        extra={"task_id": request.id, "task_name": request.task}
    )


def on_task_failure(request, context, exception, *args, **kwargs):
    """Callback for task failure."""
    import logging
    logger = logging.getLogger(__name__)
    logger.error(
        f"Task {request.task} [{request.id}] failed: {exception}",
        extra={"task_id": request.id, "task_name": request.task},
        exc_info=exception
    )


# Register callbacks (optional)
# celery_app.conf.task_success = on_task_success
# celery_app.conf.task_failure = on_task_failure


if __name__ == '__main__':
    # Run Celery worker directly
    celery_app.worker_main(['worker', '--loglevel=info'])


# =============================================================================
# NFM-346 Phase 4.5: HPC Failover Monitoring
# =============================================================================

import os
import logging

logger = logging.getLogger(__name__)

# Redis client for failure counter (initialized when needed)
_redis_client = None


def _get_redis_client():
    """Get or create Redis client for failure counter.

    Returns:
        Redis client instance
    """
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            _redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', '6379')),
                db=0,
                decode_responses=True
            )
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            _redis_client = None
    return _redis_client


def _validate_hpc_environment():
    """Validate HPC environment variables required for monitoring.

    Raises:
        ValueError: If required environment variables are missing
        FileNotFoundError: If SSH key path doesn't exist
    """
    from pathlib import Path

    required_vars = [
        "NFM_HPC_PRIMARY_HOST",
        "NFM_HPC_PRIMARY_USER",
        "NFM_HPC_PRIMARY_SSH_KEY_PATH"
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(
            f"Required HPC environment variables not set: {', '.join(missing_vars)}"
        )

    # Validate SSH key path exists
    ssh_key_path = os.getenv("NFM_HPC_PRIMARY_SSH_KEY_PATH")
    if not Path(ssh_key_path).exists():
        raise FileNotFoundError(f"SSH key not found: {ssh_key_path}")

    logger.info("HPC environment variables validated successfully")


def increment_failure_count() -> int:
    """Increment primary cluster failure count in Redis.

    Returns:
        Current failure count after increment
    """
    redis_client = _get_redis_client()
    if not redis_client:
        logger.warning("Redis not available - using in-memory counter")
        return 1  # Fallback to in-memory

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
        return 1  # Fallback


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


@celery_app.task(name='hpc.monitor_primary_cluster_health')
def monitor_primary_cluster_health() -> dict:
    """Periodic health check for primary HPC cluster.

    Runs every 30 seconds via Celery beat.
    Triggers failover if primary cluster unhealthy for 5 consecutive checks.

    Returns:
        Dictionary with monitoring status and statistics
    """
    import asyncio

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
                backup_hosts=[os.getenv("NFM_HPC_BACKUP_HOST", "backup.example.com")] if os.getenv("NFM_HPC_BACKUP_HOST") else None,
                backup_username=os.getenv("NFM_HPC_BACKUP_USER"),
                backup_ssh_key_path=os.getenv("NFM_HPC_BACKUP_SSH_KEY_PATH"),
                failover_threshold_seconds=int(os.getenv("NFM_HPC_FAILOVER_THRESHOLD_SECONDS", "300")),
                skip_key_validation=True  # For Celery task context
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
                                logger.error(f"Automatic failover triggered after {failure_count} failures")
                                return {
                                    "status": "failover_triggered",
                                    "failover_count": failure_count,
                                    "message": "Successfully failed over to backup cluster"
                                }
                            else:
                                logger.error(f"Failover attempted after {failure_count} failures but failed")
                                return {
                                    "status": "failover_failed",
                                    "failover_count": failure_count,
                                    "message": "Failover attempt failed"
                                }
                        else:
                            logger.warning(f"Primary unhealthy (failure #{failure_count}), threshold not yet reached")
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
                        "message": "Health check completed"
                    }
    
                finally:
                orchestrator.cleanup()

        except Exception as e:
            logger.error(f"HPC health monitoring failed: {e}")
            return {
                "status": "error",
                "message": str(e),
                "error_type": type(e).__name__
            }

    # Handle both Celery (sync) and pytest (async) contexts
    try:
        loop = asyncio.get_running_loop()
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
            return {
                "status": "error",
                "message": str(e)
            }
