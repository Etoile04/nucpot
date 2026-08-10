"""Celery task that runs the gap-driven literature pipeline (NFM-2781 CR3).

The dispatcher service
(:mod:`nfm_db.services.gap_dispatch_service`) schedules this task via
``celery_app.send_task("nfm_db.tasks.gap_literature_task.process_gap_literature_task", ...)``
when a :class:`nfm_db.models.DataCollectionRequest` with
``source_preference='literature'`` (or the first try in
``'any'``) needs a literature search.  Keeping the worker in its own
module means the dispatcher itself does not have to import Celery at
module load — a long-standing CR finding against PR #719.

The task body is intentionally minimal: it logs the dispatch and
returns a ``queued`` placeholder.  The full search/extraction
pipeline will be implemented in a follow-up ticket; for now this
task exists so the worker can acknowledge the schedule and the
dispatcher contract (per NFM-2621 / NFM-2651) holds end-to-end.
"""

from __future__ import annotations

import logging
from typing import Any

from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="nfm_db.tasks.gap_literature_task.process_gap_literature_task",
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=(ConnectionError, IOError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def process_gap_literature_task(
    self: Any,
    request_id: str,
    entity_type: str,
    property: str,
    material_system: str,
) -> dict[str, Any]:
    """Celery task: process a literature gap-collection request.

    Scheduled by
    :meth:`nfm_db.services.gap_dispatch_service.GapDispatchService._dispatch_literature`.

    Returns:
        A small ack payload with ``status='queued'``.  The full
        literature search pipeline is intentionally out of scope for
        this task; see the dispatcher docstring.
    """
    logger.info(
        "process_gap_literature_task started request_id=%s "
        "entity=%s property=%s material=%s task_id=%s",
        request_id,
        entity_type,
        property,
        material_system,
        self.request.id,
    )
    return {
        "request_id": request_id,
        "status": "queued",
        "message": (
            "Literature gap processing queued; search pipeline pending "
            "implementation."
        ),
    }


__all__ = ["process_gap_literature_task"]
