"""Celery Beat tasks for HPC orchestration monitoring."""

import asyncio
import logging
import os
from datetime import timedelta

from celery.schedules import crontab

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

# ---- Force module registration ----------------------------------------
# Importing these modules here (AFTER celery_app exists) ensures their
# ``@celery_app.task(...)`` decorators fire and register the task with
# this Celery app.  Without this, the worker raises
# ``Received unregistered task of type '...process_literature_task'``.
#
# Order matters: celery_app must exist before the decorator runs, so
# ``from celery_app import celery_app`` in those modules is the source
# of the circular import — we resolve it here by importing the side-
# effect modules AFTER celery_app is created.
# NFM-4539 CR fix: importing ``hpc_sync`` here (AFTER ``celery_app`` is
# constructed) registers its ``@celery_app.task`` decorator on this app
# instance.  Without this, the ``sync-hpc-job-status`` beat entry below
# fires the worker into ``Received unregistered task of type
# 'nfm_db.services.hpc_sync.sync_hpc_job_status'``.  The task module
# itself is unchanged from its pre-NFM-4539 form — only its
# registration point moves here.
from nfm_db.services import (  # noqa: E402,F401
    hpc_sync,
    literature_dispatcher,
)

# Route literature-processing tasks to their own queue so the MD worker
# (--queues=md_verification) and the literature worker
# (--queues=literature_processing) can scale independently. See
# docker-compose.prod.yml and the NFM-1489 dispatcher module.
celery_app.conf.task_routes = {
    "nfm_db.services.literature_dispatcher.process_literature_task": {
        "queue": "literature_processing",
    },
    # NFM-4539 RAG-D: route the daily reconciliation audit to the
    # default queue so it runs alongside other light housekeeping.
    # We deliberately do NOT route it through ``md_verification`` /
    # ``literature_processing`` — those queues are sized for the
    # extraction workload; the audit is a small read-mostly job.
    "nfm_db.services.celery_app.rag_audit_index_coverage_task": {
        "queue": "default",
    },
    # NFM-4742 F-3 §4: bucket-segregation + processing reaper also
    # runs on the default queue.  Same rationale as the index-coverage
    # task — small, read-mostly, runs back-to-back with the audit
    # so a single LightRAG ``/documents`` call covers both windows.
    "nfm_db.services.celery_app.rag_audit_document_buckets_task": {
        "queue": "default",
    },
}

# NFM-4539 RAG-D §4.2: register the daily audit under Celery beat at
# 03:30 UTC, reusing NFM-4257's prune automation slot so the
# reconciliation and the prune back-to-back minimise operational
# surface.  Cron is UTC by convention in this project.
#
# NFM-4539 CR fix: previous incarnation did
#   celery_app.conf.beat_schedule = {...}
# which **wipes** any default-initialised beat entries.  ``hpc_sync``'s
# periodic ``sync-hpc-job-status`` (every 30s) used to be wired here
# pre-NFM-4539 and ``tests/test_hpc_status_sync.py`` asserts it is in
# the live schedule dict.  Switch to ``.update()`` so future entries
# registered elsewhere (e.g. operator-supplied celery config) survive
# our additions.
celery_app.conf.beat_schedule.update(
    {
        "rag-audit-index-coverage-daily": {
            "task": "nfm_db.services.celery_app.rag_audit_index_coverage_task",
            "schedule": crontab(minute=30, hour=3),  # 03:30 UTC daily
        },
        # NFM-4742 F-3 §4: bucket segregation + processing reaper
        # runs one minute after the index-coverage audit so both
        # consume the same daily ``/documents`` snapshot in close
        # succession.  Output lives in
        # ``rag_index_audit_log`` rows with
        # ``action='bucket_counts'`` (totals) /
        # ``action='failed_<reason>'`` (segregated) /
        # ``action='processing_reaped'`` (timeout cleanup).
        "rag-audit-document-buckets-daily": {
            "task": "nfm_db.services.celery_app.rag_audit_document_buckets_task",
            "schedule": crontab(minute=31, hour=3),  # 03:31 UTC daily
        },
        # NFM-4539 CR fix: re-register the HPC sync periodic task that
        # previously lived here.  ``hpc_sync.sync_hpc_job_status`` runs
        # every 30 seconds to update the status of all active HPC jobs
        # in the system (see ``tests/test_hpc_sync.py``).  30.0 is the
        # documented contract — do not change without updating the
        # ``tests/test_hpc_status_sync.py`` assertion.
        "sync-hpc-job-status": {
            "task": "nfm_db.services.hpc_sync.sync_hpc_job_status",
            "schedule": 30.0,
        },
    }
)

# NFM-3902: per-task time limits.  Literature extraction drives an Ollama-
# served LLM (qwen3.8:27b-mlx in prod) whose cold-load from disk takes
# 50-60 s on the host's Metal MLX weights, and whose throughput after warm-up
# is ~0.07 tok/s on the actual prompt sizes (system_prompt_len=3553 +
# user_message_len=19065 per chunk x 3 chunks).  The Celery default soft /
# hard time limits are too tight to survive even a single cold start, let
# alone the full 3-chunk extraction.  These values are mirrored by
# test_celery_config.test_task_time_limits and test_md_tasks
# .test_task_time_limits.
celery_app.conf.task_soft_time_limit = 3600  # 1h — SoftTimeLimitExceeded, log + cleanup
celery_app.conf.task_time_limit = 7200  # 2h — SIGTERM, hard kill

# NFM-3902: prefetch + ack discipline already pinned in
# test_celery_config.test_worker_optimization_for_long_running_tasks; spell
# them out here too so the running worker matches the test contract even
# if the test module is lazily imported on a partial deploy.
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.task_acks_late = True
celery_app.conf.worker_max_tasks_per_child = 1000
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.result_expires = timedelta(days=1)


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


# ---------------------------------------------------------------------------
# NFM-4539 RAG-D — daily index-coverage reconciliation
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="nfm_db.services.celery_app.rag_audit_index_coverage_task",
    max_retries=1,
    default_retry_delay=300,
    autoretry_for=(ConnectionError,),
)
def rag_audit_index_coverage_task(self) -> dict:
    """Daily hook/对账 (NFM-4539 RAG-D §4.2).

    Wraps :func:`nfm_db.services.rag_audit.run_rag_audit_index_coverage`
    in a Celery-friendly sync boundary.  We import the async service
    lazily to avoid a circular import at module load time.

    Returns a JSON-friendly summary that downstream observability
    surfaces; raises on irrecoverable failures so the watchdog (NFM-4406)
    can fire.
    """

    async def _run() -> dict:
        from nfm_db.config import get_settings
        from nfm_db.database import task_session_factory
        from nfm_db.services.rag_audit import run_rag_audit_index_coverage

        settings = get_settings()
        # BUG-22 (NFM-4076 / ADR-NFM-4076 D3): the shared engine's asyncpg
        # pool binds its connections to the event loop that first used
        # them.  This task runs through a fresh ``asyncio.run`` loop per
        # invocation, so the shared engine deterministically fails with
        # ``cannot perform operation: another operation is in progress``
        # in a Celery prefork child that has run any prior async task
        # (verified live in prod 2026-09-10 while manually triggering the
        # audit for NFM-4636).  Use the task-scoped NullPool engine like
        # every other Celery task boundary.
        async with task_session_factory() as factory:
            async with factory() as session:
                outcome = await run_rag_audit_index_coverage(
                    session,
                    lightrag_host=settings.lightrag_host,
                    lightrag_port=settings.lightrag_port,
                )
        return {
            "run_date": outcome.run_date.isoformat(),
            "completed_total": outcome.completed_total,
            "indexed_total": outcome.indexed_total,
            "drift_total": outcome.drift_total,
            "reingested": outcome.reingested,
            "errors": outcome.errors,
        }

    try:
        asyncio.get_running_loop()
        # Pytest async context — run in a worker thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _run())
            return future.result()
    except RuntimeError:
        try:
            return asyncio.run(_run())
        except Exception as exc:  # pragma: no cover - propagated to celery
            logger.error("rag_audit_index_coverage_task failed: %s", exc)
            raise


# ---------------------------------------------------------------------------
# NFM-4742 F-3 §3 — daily bucket segregation + processing reaper
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="nfm_db.services.celery_app.rag_audit_document_buckets_task",
    max_retries=1,
    default_retry_delay=300,
    autoretry_for=(ConnectionError,),
)
def rag_audit_document_buckets_task(self) -> dict:
    """Daily bucket-segregation audit + processing reaper (NFM-4742 F-3 §3).

    Wraps :func:`nfm_db.services.rag_audit.run_rag_audit_document_buckets`
    in the same Celery-friendly sync boundary as the index-coverage
    task above (BUG-22 / NFM-4076 — see the ``task_session_factory``
    comment for why we cannot use the shared engine).

    The task is intentionally idempotent: re-running on the same day
    adds extra audit rows but never corrupts state.  Operators can
    trigger it manually via ``celery_app.send_task(
    "nfm_db.services.celery_app.rag_audit_document_buckets_task")``
    when F-3 evidence needs a fresh snapshot between the 03:31 UTC
    windows.
    """

    async def _run() -> dict:
        from nfm_db.config import get_settings
        from nfm_db.database import task_session_factory
        from nfm_db.services.rag_audit import run_rag_audit_document_buckets

        settings = get_settings()
        async with task_session_factory() as factory:
            async with factory() as session:
                outcome = await run_rag_audit_document_buckets(
                    session,
                    lightrag_host=settings.lightrag_host,
                    lightrag_port=settings.lightrag_port,
                )
        return {
            "run_date": outcome.run_date.isoformat(),
            "counts": outcome.counts.as_dict(),
            "processing_reaped": outcome.processing_reaped,
            "processing_reap_errors": outcome.processing_reap_errors,
            "failures_classified": outcome.failures_classified,
            "error_message": outcome.error_message,
        }

    try:
        asyncio.get_running_loop()
        # Pytest async context — run in a worker thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _run())
            return future.result()
    except RuntimeError:
        try:
            return asyncio.run(_run())
        except Exception as exc:  # pragma: no cover - propagated to celery
            logger.error("rag_audit_document_buckets_task failed: %s", exc)
            raise
