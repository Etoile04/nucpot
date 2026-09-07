"""Single seam for submitting Celery tasks (C4 / NFM-2564).

One interface over the broker:

- production adapter: ``celery_app.send_task`` (name-based — works for any
  registered task regardless of where its module is imported);
- tests: patch this module's :func:`dispatch` (a plain function accessor)
  or the singleton's ``send_task`` — both are safe inspection targets,
  unlike module attributes holding live task objects.

Errors propagate unchanged (``CeleryError`` and transport errors): the
endpoints already translate broker failures to 503 at their edge, and
re-typing them here would break existing handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from nfm_db.services.celery_app import celery_app

logger = logging.getLogger(__name__)


def dispatch(
    task_name: str,
    *,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    queue: str | None = None,
) -> str:
    """Submit *task_name* to the broker; return the Celery task id.

    Broker failures propagate unchanged so callers keep their existing
    ``CeleryError`` handling.
    """
    async_result = celery_app.send_task(
        task_name,
        args=args,
        kwargs=kwargs,
        queue=queue,
    )
    logger.info(
        "dispatched task=%s queue=%s task_id=%s",
        task_name,
        queue,
        async_result.id,
    )
    return str(async_result.id)
