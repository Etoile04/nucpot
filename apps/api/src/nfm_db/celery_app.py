"""Celery application configuration for async MD verification tasks.

Phase 2.3 of NFM-313: MD verification backend integration.
Configures Celery with Redis broker/backend for long-running HPC job execution.
"""

from __future__ import annotations

import os
from typing import Any

from celery import Celery
from celery.app import defaults
from celery.schedules import crontab

# Redis configuration from environment
REDIS_HOST = os.getenv("NFM_REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("NFM_REDIS_PORT", "6379"))
REDIS_BROKER_DB = int(os.getenv("NFM_REDIS_BROKER_DB", "0"))
REDIS_RESULT_DB = int(os.getenv("NFM_REDIS_RESULT_DB", "1"))

# Construct Redis URLs
broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_BROKER_DB}"
result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_RESULT_DB}"

# Create Celery app
celery_app = Celery(
    "nfm_md_tasks",
    broker=broker_url,
    backend=result_backend,
    include=["nfm_db.services.md_tasks"],  # Task modules to load
)

# --- Configuration for long-running HPC tasks ---

celery_app.conf.update(
    # Task serialization (JSON for FastAPI compatibility)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Worker optimization for long-running tasks
    # prefetch_multiplier=1 ensures one task per worker at a time
    # prevents worker starvation with hour+ tasks
    worker_prefetch_multiplier=1,

    # Late ack ensures task only acknowledged after completion
    # prevents task loss on worker failure during long execution
    task_acks_late=True,

    # Result expiration (7 days)
    result_expires=60 * 60 * 24 * 7,

    # Task retry configuration
    task_max_retries=3,
    task_default_retry_delay=60,  # 1 minute initial delay
    task_retry_backoff=True,  # Enable exponential backoff
    task_retry_backoff_max=600,  # Max 10 minutes between retries
    task_retry_jitter=True,  # Add jitter to prevent thundering herd

    # Task time limits
    # soft_timeout: task gets Exception after warning
    # hard_timeout: task killed after limit
    task_soft_time_limit=3600,  # 1 hour (adjust per HPC job duration)
    task_time_limit=7200,  # 2 hours hard limit

    # Worker settings
    worker_concurrency=1,  # Single worker per process for isolation
    worker_max_tasks_per_child=1,  # Restart worker after each task (memory cleanup)

    # Result backend settings
    result_backend_transport_options={
        "retry_policy": {
            "timeout": 5.0,
            "max_retries": 3,
        },
    },

    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=5,

    # Task routing (optional: can route to specific queues)
    task_routes={
        "nfm_db.services.md_tasks.submit_md_verification_job": {
            "queue": "md_verification",
            "routing_key": "md.verify",
        },
    },

    # Monitoring (Flower integration)
    worker_send_event_events=True,
    task_send_sent_event=True,
)


def get_celery_config() -> dict[str, Any]:
    """Get current Celery configuration for testing/monitoring.

    Returns:
        Dictionary of current Celery configuration settings
    """
    return {
        "broker_url": celery_app.conf.broker_url,
        "result_backend": celery_app.conf.result_backend,
        "task_serializer": celery_app.conf.task_serializer,
        "worker_prefetch_multiplier": celery_app.conf.worker_prefetch_multiplier,
        "task_acks_late": celery_app.conf.task_acks_late,
        "task_max_retries": celery_app.conf.task_max_retries,
        "task_soft_time_limit": celery_app.conf.task_soft_time_limit,
        "task_time_limit": celery_app.conf.task_time_limit,
    }


# Celery beat schedule for periodic tasks (optional for Phase 2)
# Example: cleanup old results, health checks
celery_app.conf.beat_schedule = {
    "cleanup-old-results": {
        "task": "nfm_db.services.md_tasks.cleanup_old_results",
        "schedule": crontab(hour=2, minute=0),  # 2 AM daily
    },
    "hpc-health-check": {
        "task": "nfm_db.services.md_tasks.hpc_health_check",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes
    },
}


__all__ = ["celery_app", "get_celery_config"]
