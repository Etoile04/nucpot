"""Literature processing dispatcher (NFM-1489).

Choice: **Celery** (not FastAPI BackgroundTasks).

The production deployment (see ``docker-compose.prod.yml``) already runs a
Celery worker (``nucpot-prod-worker``) on the ``md_verification`` queue with
Redis as the broker.  Reusing that worker for the literature pipeline keeps
PDF/DOI parsing out of the API process (which would otherwise tie up a
FastAPI worker for 30+ s per upload) and gives us retries, observability, and
survivability across API restarts for free.

Fallback: ``FastAPI.BackgroundTasks`` is NOT wired here.  It runs in the
same process as the HTTP handler, so a 30 s parse would still block a
worker slot for the duration of the parse.  If the API container is killed
mid-parse, the work is lost.  Celery + Redis survives both concerns.

Both ``POST /literature/upload`` and ``POST /literature/from-doi`` call
:func:`schedule_literature_processing` after persisting a placeholder
``DataSource`` row.  The dispatcher is fire-and-forget from the caller's
perspective: it returns a task id for logging and re-raises broker errors
so endpoints can return 503 if Redis is unavailable.

The worker that picks up the task is started with
``--queues=literature_processing``; see ``docker-compose.prod.yml``.
"""

from __future__ import annotations

import logging
from uuid import UUID

from celery.exceptions import CeleryError

from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants — stable identifiers used by tests and by the worker
# ---------------------------------------------------------------------------

#: Name of the Celery task that performs the actual PDF/DOI parsing.
#: Must match the ``@celery_app.task(  # type: ignore[misc]name=...)`` decorator in this module.
LITERATURE_TASK_NAME = (
    "nfm_db.services.literature_dispatcher.process_literature_task"
)

#: Queue the dispatcher routes tasks to. The worker MUST be started with
#: ``--queues=literature_processing`` (see docker-compose.prod.yml).
LITERATURE_QUEUE = "literature_processing"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _send_literature_task(*, task_name: str, datasource_id: str, queue: str) -> Any:
    """Send the task to Celery with the correct routing.

    Wrapped in a tiny function so unit tests can patch it without booting
    the real broker.
    """
    return celery_app.send_task(
        task_name,
        kwargs={"datasource_id": datasource_id},
        queue=queue,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def schedule_literature_processing(datasource_id: UUID | str) -> str:
    """Schedule background parsing for *datasource_id*.

    Returns the Celery task id (str) so the caller can include it in the
    HTTP response for debugging.  Raises :class:`CeleryError` (or any
    transport error) if the broker is unreachable so the endpoint can
    surface a 503 to the client.

    The function is intentionally synchronous: Celery's ``send_task``
    is non-blocking from the FastAPI handler's perspective (it just
    publishes a message to Redis) but the call itself is short — well
    under the 500 ms acceptance budget (NFM-1489 §A.1).
    """
    if isinstance(datasource_id, UUID):
        datasource_id = str(datasource_id)

    logger.info(
        "Scheduling literature processing for datasource_id=%s on queue=%s",
        datasource_id,
        LITERATURE_QUEUE,
    )
    try:
        async_result = _send_literature_task(
            task_name=LITERATURE_TASK_NAME,
            datasource_id=datasource_id,
            queue=LITERATURE_QUEUE,
        )
    except CeleryError:
        logger.exception(
            "Celery broker error while scheduling literature task "
            "for datasource_id=%s",
            datasource_id,
        )
        raise
    except Exception:
        logger.exception(
            "Unexpected error dispatching literature task for datasource_id=%s",
            datasource_id,
        )
        raise

    task_id = getattr(async_result, "id", None) or str(async_result)
    logger.info(
        "Scheduled literature task_id=%s for datasource_id=%s",
        task_id,
        datasource_id,
    )
    return task_id


# ---------------------------------------------------------------------------
# Celery task — runs in the worker process
# ---------------------------------------------------------------------------
#
# This task body lives here (not in a separate literature_service module)
# for two reasons:
#   1. NFM-1485-2 (literature_service.py) is still in flight; keeping the
#      task body lazy-imported means the worker boots cleanly even before
#      the service lands.
#   2. The dispatcher already owns the routing/queue contract; co-locating
#      the worker entry point keeps the wire-up in one file.
#
# ``process_literature`` is imported lazily inside the body so unit tests
# can exercise the dispatcher without standing up the full service layer.


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name=LITERATURE_TASK_NAME,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, IOError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_literature_task(self: Any, datasource_id: str) -> dict[str, Any]:
    """Parse a PDF or DOI-fetched document into KG nodes/edges.

    Delegates to :func:`nfm_db.services.literature_service.process_literature`
    (NFM-1485-2 / NFM-1487).  We import lazily so a partial deploy that has
    the dispatcher but not yet the service still imports cleanly.
    """
    from nfm_db.services.literature_service import process_literature_sync

    logger.info(
        "process_literature_task started datasource_id=%s task_id=%s",
        datasource_id,
        self.request.id,
    )
    return process_literature_sync(datasource_id)


__all__ = [
    "LITERATURE_QUEUE",
    "LITERATURE_TASK_NAME",
    "process_literature_task",
    "schedule_literature_processing",
]
