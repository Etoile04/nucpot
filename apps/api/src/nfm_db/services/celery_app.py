"""Celery application configuration for NFMD async task processing.

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
